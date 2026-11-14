"""Versioned API. Admin mutations always require an expiring bearer capability."""
import hmac
import hashlib
import json
import os
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy import and_, case, func, or_, select, text, update, delete
from sqlalchemy.orm import Session

from . import security
from .models import AgentTrace, AuditLog, Event, EventSource, EventTicketEvidence, Feedback, NotificationOutbox, RawSourceRecord, Source, Subscription, now, uid
from .schemas import (CATEGORIES, CITIES, EventInput, FeedbackInput, ImportInput, LoginInput,
                      SubscriptionInput, VerifyInput, clean_text, normalize, EventOutput,
                      EventListOutput, SubscriptionOutput, FeedbackOutput, ErrorOutput,
                      NotificationAckInput, NotificationClaimInput)
from .services import (audit, digest, event_dict, import_records, iso, publication_problem,
                       register_source, verification_cutoff, invalidate_ticket_identity)
from .ticket_evidence import TicketSnapshotInput

router = APIRouter(prefix='/api/v1', responses={status: {'model': ErrorOutput} for status in [401, 403, 404, 409, 422, 429, 503]})
LOCAL_TZ = ZoneInfo('Asia/Yekaterinburg')


def db(request: Request):
    with request.app.state.session_factory() as session:
        session.info['actor'] = getattr(request.state, 'actor', 'system')
        yield session


def fetch_event(session, event_id):
    event = session.get(Event, event_id)
    if not event:
        raise HTTPException(404, 'Событие не найдено')
    return event


def public_conditions():
    timestamp = now()
    cutoff = verification_cutoff()
    ticket_end = EventTicketEvidence.facts['date_end_utc'].as_string()
    has_current_end = Event.ticket_evidence.has(and_(
        EventTicketEvidence.checked_at >= cutoff, ticket_end.is_not(None)))
    ticket_not_ended = Event.ticket_evidence.has(and_(
        EventTicketEvidence.checked_at >= cutoff,
        ticket_end >= timestamp.astimezone(timezone.utc).isoformat(timespec='microseconds')))
    base_not_ended = or_(Event.date_end >= timestamp,
                         and_(Event.date_end.is_(None), Event.date_start >= timestamp))
    return [Event.status == 'published',
            or_(Event.verified_at >= cutoff, Event.ticket_evidence.has(EventTicketEvidence.checked_at >= cutoff)),
            ~Event.ticket_evidence.has(EventTicketEvidence.availability.in_(['sold_out','off_sale','cancelled','rescheduled'])),
            Event.confidence >= .8,
            or_(and_(has_current_end, ticket_not_ended), and_(~has_current_end, base_not_ended))]


def current_minimum_price():
    price = select(EventTicketEvidence.facts['price_min'].as_float()).where(EventTicketEvidence.event_id == Event.id).scalar_subquery()
    return case((Event.ticket_evidence.has(EventTicketEvidence.checked_at >= verification_cutoff()),price),else_=Event.price_min)


@router.get('/events', response_model=EventListOutput)
def events(q: str | None = Query(None, max_length=300), city: str | None = None,
           category: str | None = None, venue: str | None = None,
           date_from: date | None = None, date_to: date | None = None,
           price_max: float | None = Query(None, ge=0), age: int | None = Query(None, ge=0, le=100),
           sort: str = Query('date', pattern='^(date|price)$'),
           page: int = Query(1, ge=1, le=1000000), page_size: int = Query(20, ge=1, le=100),
           session: Session = Depends(db)):
    conditions = public_conditions()
    if any(value and not 1900 <= value.year <= 9998 for value in [date_from, date_to]):
        raise HTTPException(422, 'Период должен быть между 1900 и 9998 годами')
    if q:
        escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        conditions.append(or_(Event.normalized_title.contains(normalize(q), autoescape=True),
                              Event.title.ilike(f'%{escaped}%', escape='\\'),
                              Event.description.ilike(f'%{escaped}%', escape='\\'),
                              Event.venue.ilike(f'%{escaped}%', escape='\\')))
    for value, column in [(city, Event.city), (category, Event.category), (venue, Event.venue)]:
        if value:
            conditions.append(column == value)
    if date_from:
        conditions.append(Event.date_start >= datetime.combine(date_from, time.min, LOCAL_TZ).astimezone(timezone.utc))
    if date_to:
        conditions.append(Event.date_start < datetime.combine(date_to + timedelta(days=1), time.min, LOCAL_TZ).astimezone(timezone.utc))
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, 'Начало периода позже окончания')
    if price_max is not None:
        # Minimum known price is a search filter, never a guaranteed group total.
        minimum = current_minimum_price()
        conditions.extend([minimum.is_not(None), minimum <= price_max])
    if age is not None:
        conditions.extend([Event.age_limit.is_not(None), Event.age_limit <= age])
    total = session.scalar(select(func.count()).select_from(Event).where(*conditions))
    ordering = [Event.date_start, Event.id] if sort == 'date' else [current_minimum_price().asc().nulls_last(), Event.date_start, Event.id]
    rows = session.scalars(select(Event).where(*conditions).order_by(*ordering).offset((page - 1) * page_size).limit(page_size))
    return {'items': [event_dict(event) for event in rows], 'total': total, 'page': page, 'page_size': page_size}


@router.get('/events/{event_id}', response_model=EventOutput)
def event_detail(event_id: str, response: Response, session: Session = Depends(db)):
    event = fetch_event(session, event_id)
    if event.status == 'draft' or not event.verified_at:
        raise HTTPException(404, 'Событие не найдено')
    response.headers['Cache-Control'] = 'public, max-age=0, must-revalidate'
    return event_dict(event)


@router.get('/cities', response_model=list[str])
@router.get('/venues', response_model=list[str])
@router.get('/categories', response_model=list[str])
def dictionaries(request: Request, response: Response, session: Session = Depends(db)):
    name = request.url.path.rsplit('/', 1)[-1]
    response.headers['Cache-Control'] = 'public, max-age=60'
    if name == 'categories':
        return CATEGORIES
    column = Event.city if name == 'cities' else Event.venue
    return session.scalars(select(column).where(*public_conditions()).distinct().order_by(column)).all()


@router.get('/health')
def health(session: Session = Depends(db)):
    session.execute(text('SELECT 1'))
    return {'status': 'ok', 'database': 'ok', 'timezone': 'Asia/Yekaterinburg',
            'llm_configured': bool(os.getenv('LLM_API_KEY')),
            'notification_transport': 'enabled' if notifications_enabled() else 'disabled'}


@router.get('/metrics')
def metrics(request: Request):
    values = request.app.state.metrics
    return {'requests': values['requests'], 'errors_5xx': values['errors_5xx'],
            'search_latency_p95_ms': sorted(values['search_ms'])[max(0, int(len(values['search_ms']) * .95) - 1)]
            if values['search_ms'] else None, 'search_samples': len(values['search_ms'])}


@router.post('/admin/login')
def login(value: LoginInput, request: Request):
    password = request.app.state.admin_password
    try:
        accounts = security.admin_accounts()
    except ValueError as error:
        raise HTTPException(503, 'Исправьте ADMIN_ACCOUNTS_JSON на сервере: вход временно отключён') from error
    if (not password and not accounts) or not request.app.state.session_secret:
        raise HTTPException(503, 'Настройте ADMIN_PASSWORD и ADMIN_SESSION_SECRET на сервере')
    account = accounts.get(value.username)
    if accounts:
        valid = account and security.verify_password(value.password, account.get('password_hash'))
        role = account['role'] if account else 'viewer'
    else:
        valid = value.username == 'admin' and hmac.compare_digest(value.password.encode(), password.encode())
        role = 'admin'
    if not valid:
        raise HTTPException(401, 'Неверный пароль')
    return {'token': security.issue_token(request.app.state.session_secret, value.username, role),
            'expires_in': 28800, 'actor': value.username, 'role': role}


admin = APIRouter(prefix='/admin', dependencies=[Depends(security.require_admin)])


@admin.get('/events', response_model=EventListOutput)
def admin_events(status: str | None = Query(None, pattern='^(published|draft|cancelled|rescheduled)$'),
                 q: str | None = Query(None, max_length=300), city: str | None = None,
                 category: str | None = None, page: int = Query(1, ge=1, le=1000000),
                 page_size: int = Query(50, ge=1, le=1000), session: Session = Depends(db)):
    conditions = [Event.status == status] if status else []
    if q:
        conditions.append(or_(Event.normalized_title.contains(normalize(q), autoescape=True),
                              Event.venue.contains(q, autoescape=True), Event.id == q))
    if city:
        conditions.append(Event.city == city)
    if category:
        conditions.append(Event.category == category)
    rows = session.scalars(select(Event).where(*conditions).order_by(Event.updated_at.desc()).offset((page - 1) * page_size).limit(page_size))
    return {'items': [event_dict(event) for event in rows],
            'total': session.scalar(select(func.count()).select_from(Event).where(*conditions)),
            'page': page, 'page_size': page_size}


@admin.post('/events', status_code=201, response_model=EventOutput)
def create_event(value: EventInput, session: Session = Depends(db)):
    # Manual creation follows the same validation and review gate as import.
    from .services import dedup_key
    if session.scalar(select(Event.id).where(Event.dedup_key == dedup_key(value))):
        raise HTTPException(409, 'Такой сеанс уже существует')
    report = import_records(session, 'json', json.dumps([value.model_dump(mode='json')], ensure_ascii=False))
    event = fetch_event(session, report['details'][0]['id'])
    audit(session, 'event.create', event.id, {'before': None, 'after': event_dict(event)})
    session.commit()
    return event_dict(event)


@admin.post('/import')
def import_data(value: ImportInput, session: Session = Depends(db)):
    try:
        return import_records(session, value.format, value.content)
    except (ValueError, json.JSONDecodeError) as error:
        session.rollback()
        raise HTTPException(422, str(error)) from error


@admin.patch('/events/{event_id}', response_model=EventOutput)
def edit_event(event_id: str, value: dict, session: Session = Depends(db)):
    event = fetch_event(session, event_id)
    writable = set(EventInput.model_fields) - {'region', 'provider_id'}
    if not value or set(value) - writable:
        raise HTTPException(422, 'Разрешено менять только поля события; verified_at и confidence задаются проверкой')
    combined = {name: getattr(event, name) for name in writable}
    # SQLite does not preserve timezone information, domain storage is UTC.
    for name in ['date_start', 'date_end']:
        if combined[name] is not None and combined[name].tzinfo is None:
            combined[name] = combined[name].replace(tzinfo=timezone.utc)
    combined.update(value)
    if event.city not in CITIES:
        combined['region'] = 'Башкортостан'
    try:
        validated = EventInput.model_validate(combined)
    except ValidationError as error:
        raise HTTPException(422, [{'field': '.'.join(map(str, err['loc'])), 'message': err['msg']}
                                  for err in error.errors(include_input=False)]) from error
    data = validated.model_dump(exclude={'region', 'provider_id'})
    substantive = any(data[name] != getattr(event, name) for name in value if name != 'status')
    previous = event_dict(event)
    invalidate_ticket_identity(session, event, {name: data[name] for name in value})
    for name in value:
        setattr(event, name, data[name])
    if not any(source.url == event.source_url for source in event.sources):
        event.sources.append(EventSource(url=event.source_url, name=urlsplit(event.source_url).hostname))
    if substantive:
        event.verified_at = None
        event.confidence = 0
        if event.status == 'published':
            event.status = 'draft'
        for source in event.sources:
            source.verified_at = None
    if event.status == 'published':
        problem = publication_problem(event)
        if problem:
            session.rollback()
            raise HTTPException(409, problem)
    event.normalized_title = normalize(event.title)
    from .services import dedup_key
    event.dedup_key = dedup_key(validated)
    audit(session, 'event.edit', event.id, {'fields': list(value), 'before': previous, 'after': event_dict(event)})
    try:
        session.commit()
    except Exception as error:
        session.rollback()
        from sqlalchemy.exc import IntegrityError
        if isinstance(error, IntegrityError):
            raise HTTPException(409, 'Такой сеанс уже существует') from error
        raise
    return event_dict(event)


@admin.post('/events/{event_id}/verify', response_model=EventOutput)
def verify_event(event_id: str, value: VerifyInput, request: Request, session: Session = Depends(db)):
    event = fetch_event(session, event_id)
    previous = event_dict(event)
    source = register_source(session, event.source_url)
    corroborating_urls = []
    if source.status in {'disabled', 'blocked'}:
        raise HTTPException(409, 'Источник отключён администратором. Сначала измените его статус в управлении источниками')
    try:
        security.validate_source_url(event.source_url)
        if value.fetch:
            from apps.worker.sources import SourceRegistry
            host_adapter = {'www.bashopera.ru': 'bashopera', 'bashopera.ru': 'bashopera',
                            'rusdram.ru': 'rusdram', 'www.rusdram.ru': 'rusdram', 'api.timepad.ru': 'timepad',
                            'pro.culture.ru': 'proculture', 'rusdramteatr.ru': 'sterlitamak'}
            adapter_id = host_adapter.get(urlsplit(event.source_url).hostname)
            if not adapter_id:
                raise ValueError('Нет адаптера для проверки сеанса: нужна явная ручная сверка модератором')
            cache = getattr(request.app.state, 'verification_cache', {})
            import time as clock
            cached = cache.get(event.source_url)
            if cached and cached[0] > clock.monotonic() - 300:
                raw = cached[1]
            else:
                raw = security.fetch_source(event.source_url)
                cache[event.source_url] = (clock.monotonic(), raw)
                request.app.state.verification_cache = cache
            candidates = SourceRegistry().adapters[adapter_id].parse(raw, event.source_url)
            matched = False
            factual_fields = ['title', 'category', 'tags', 'date_start', 'date_end', 'duration_min',
                              'city', 'venue', 'address', 'description', 'age_limit',
                              'price_min', 'price_max', 'is_free', 'language', 'lat', 'lon',
                              'org_link', 'source_url', 'source_type', 'poster_url']
            for candidate in candidates:
                parsed = EventInput.model_validate(candidate)
                if parsed.status in {'cancelled', 'rescheduled'}:
                    continue
                valid = True
                for field in factual_fields:
                    expected, actual = getattr(parsed, field), getattr(event, field)
                    if field in {'date_start', 'date_end'}:
                        if actual is not None and actual.tzinfo is None:
                            actual = actual.replace(tzinfo=timezone.utc)
                    elif field == 'tags':
                        expected = sorted(normalize(tag) for tag in expected)
                        actual = sorted(normalize(tag) for tag in actual)
                    elif field == 'source_type':
                        actual = {'страница': 'website', 'API': 'api', 'ручной': 'manual'}.get(actual, actual)
                    elif field == 'venue':
                        from .schemas import canonical_venue
                        expected, actual = canonical_venue(expected), canonical_venue(actual or '')
                    elif isinstance(expected, str) and field not in {'org_link', 'source_url', 'description'}:
                        expected, actual = normalize(expected), normalize(actual or '')
                    if expected != actual:
                        valid = False
                        break
                if valid:
                    matched = True
                    break
            if not matched:
                raise ValueError('Не подтверждены все факты сеанса: дата, длительность, место, описание, возраст, цена и другие поля должны совпадать с первоисточником')
            if adapter_id == 'sterlitamak':
                bundle = json.loads(raw)
                tariff_url = 'https://rusdramteatr.ru/bilety'
                if (bundle.get('format') == 'catalog-evidence-v1'
                        and bundle.get('documents', {}).get(tariff_url)
                        and any(part.get('url') == tariff_url for part in bundle.get('manifest', []))):
                    security.validate_source_url(tariff_url)
                    corroborating_urls.append(tariff_url)
            session.add(RawSourceRecord(payload=raw, source_url=event.source_url, source_hash=digest(raw),
                                        event_id=event.id, outcome='verified', reason=value.note))
    except (ValueError, OSError) as error:
        raise HTTPException(422, str(error)) from error
    event.verified_at = now()
    event.confidence = .95 if value.fetch else .85
    for url in corroborating_urls:
        if not any(item.url == url for item in event.sources):
            event.sources.append(EventSource(url=url, name='Официальный тариф спектакля'))
    for source in event.sources:
        if source.url == event.source_url or source.url in corroborating_urls:
            source.verified_at = event.verified_at
    source = register_source(session, event.source_url)
    source.last_checked_at = event.verified_at
    source.status = 'verified' if value.fetch else 'manually_verified'
    source.last_error = None
    audit(session, 'event.verify', event.id, {'note': value.note,
        'method': 'https_adapter_factual_match' if value.fetch else 'moderator_attestation',
        'before': previous, 'after': event_dict(event)})
    session.commit()
    return event_dict(event)


@admin.get('/audit')
def audit_list(session: Session = Depends(db)):
    rows = session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(500))
    return [{'id': row.id, 'actor': row.actor, 'action': row.action, 'event_id': row.event_id,
             'details': row.details, 'created_at': iso(row.created_at)} for row in rows]


@admin.get('/traces')
def traces(session: Session = Depends(db)):
    rows = session.scalars(select(AgentTrace).order_by(AgentTrace.created_at.desc()).limit(200))
    return [{'id': row.id, 'created_at': iso(row.created_at), **row.payload} for row in rows]


@admin.get('/sources')
def sources(session: Session = Depends(db)):
    rows = session.scalars(select(Source).order_by(Source.url))
    return [{'id': row.id, 'url': row.url, 'status': row.status, 'last_checked_at': iso(row.last_checked_at),
             'last_error': row.last_error, 'enabled': row.status not in {'disabled', 'blocked'}} for row in rows]


@admin.patch('/sources/{source_id}')
def source_update(source_id: str, value: dict, session: Session = Depends(db)):
    row = session.get(Source, source_id)
    if row is None:
        raise HTTPException(404, 'Источник не найден')
    if set(value) - {'status', 'last_error'} or value.get('status', row.status) not in {
        'awaiting_review', 'verified', 'manually_verified', 'error', 'disabled', 'blocked'}:
        raise HTTPException(422, 'Недопустимые поля источника')
    before = {'id': row.id, 'url': row.url, 'status': row.status,
              'last_error': row.last_error, 'last_checked_at': iso(row.last_checked_at)}
    row.status = value.get('status', row.status)
    row.last_error = str(value['last_error'])[:2000] if value.get('last_error') else None
    row.last_checked_at = now()
    audit(session, 'source.status', details={'id': row.id, 'before': before,
        'after': {'id': row.id, 'url': row.url, 'status': row.status,
                  'last_error': row.last_error, 'last_checked_at': iso(row.last_checked_at)}})
    session.commit()
    return {'id': row.id, 'status': row.status}


@admin.get('/feedback')
def feedback_list(session: Session = Depends(db)):
    rows = session.scalars(select(Feedback).order_by(Feedback.created_at.desc()).limit(200))
    return [{'id': row.id, 'message': row.message, 'event_id': row.event_id,
             'contact': row.contact, 'status': row.status, 'review_note': row.review_note,
             'created_at': iso(row.created_at)} for row in rows]


@admin.patch('/feedback/{feedback_id}')
def review_feedback(feedback_id: str, value: dict, session: Session = Depends(db)):
    row = session.get(Feedback, feedback_id)
    if row is None:
        raise HTTPException(404, 'Обращение не найдено')
    if set(value) - {'status', 'note'} or value.get('status') not in {'pending', 'reviewing', 'resolved', 'rejected'}:
        raise HTTPException(422, 'Выберите корректный статус обращения')
    note = clean_text(value.get('note', ''))
    if len(note) > 2000:
        raise HTTPException(422, 'Примечание слишком длинное')
    before = {'status': row.status, 'review_note': row.review_note}
    row.status, row.review_note = value['status'], note or None
    audit(session, 'feedback.review', details={'id': row.id, 'before': before,
                                              'after': {'status': row.status, 'review_note': row.review_note}})
    session.commit()
    return {'id': row.id, 'status': row.status, 'review_note': row.review_note}


@admin.post('/maintenance')
def maintenance(session: Session = Depends(db)):
    from .services import enforce_retention
    return enforce_retention(session)


def notifications_enabled():
    return os.getenv('NOTIFICATIONS_ENABLED', '0').strip().lower() in {'1', 'true', 'yes'}


@admin.post('/notifications/enqueue')
def enqueue_notifications(session: Session = Depends(db)):
    if not notifications_enabled():
        return {'queued': 0, 'status': 'disabled'}
    from sqlalchemy.exc import IntegrityError
    queued = 0
    for subscription in session.scalars(select(Subscription).where(
            Subscription.transport == 'telegram', Subscription.transport_target.is_not(None))):
        conditions = public_conditions() + [Event.city == subscription.city, Event.updated_at >= subscription.created_at]
        if subscription.category:
            conditions.append(Event.category == subscription.category)
        for event in session.scalars(select(Event).where(*conditions)):
            version = digest('|'.join([event.source_hash, event.status, iso(event.date_start)]))
            if session.scalar(select(NotificationOutbox.id).where(
                    NotificationOutbox.subscription_id == subscription.id, NotificationOutbox.event_id == event.id,
                    NotificationOutbox.version == version)):
                continue
            try:
                with session.begin_nested():
                    session.add(NotificationOutbox(subscription_id=subscription.id, event_id=event.id, version=version))
                    session.flush()
                queued += 1
            except IntegrityError:
                pass
    session.commit()
    return {'queued': queued, 'status': 'enabled'}


@admin.post('/notifications/claim')
def claim_notifications(value: NotificationClaimInput, session: Session = Depends(db)):
    if not notifications_enabled():
        return {'items': [], 'status': 'disabled'}
    session.execute(update(NotificationOutbox).where(
        NotificationOutbox.status == 'claimed', NotificationOutbox.claimed_at < now() - timedelta(minutes=5))
        .values(status='unknown', last_error='Истёк срок подтверждения: результат отправки неизвестен'))
    rows = session.scalars(select(NotificationOutbox).where(
        NotificationOutbox.status == 'pending', NotificationOutbox.attempts < 3)
        .order_by(NotificationOutbox.created_at).limit(value.limit).with_for_update(skip_locked=True)).all()
    items = []
    for row in rows:
        subscription = session.get(Subscription, row.subscription_id)
        event = session.scalar(select(Event).where(Event.id == row.event_id, *public_conditions()))
        if subscription is None or subscription.transport != 'telegram' or event is None:
            row.status = 'cancelled'
            continue
        version = digest('|'.join([event.source_hash, event.status, iso(event.date_start)]))
        if version != row.version:
            row.status = 'cancelled'
            continue
        token = secrets.token_hex(32)
        result = session.execute(update(NotificationOutbox).where(
            NotificationOutbox.id == row.id, NotificationOutbox.status == 'pending').values(
                status='claimed', claimed_at=now(), lease_token=token, attempts=NotificationOutbox.attempts + 1))
        if result.rowcount == 1:
            items.append({'id': row.id, 'subscription_id': subscription.id,
                          'chat_id': subscription.transport_target, 'event': event_dict(event),
                          'attempts': row.attempts, 'lease_token': token})
    session.commit()
    return {'items': items}


@admin.post('/notifications/{notification_id}/ack')
def ack_notification(notification_id: str, value: NotificationAckInput, session: Session = Depends(db)):
    row = session.get(NotificationOutbox, notification_id, with_for_update=True)
    if row is None:
        raise HTTPException(404, 'Уведомление не найдено')
    if not row.lease_token or not hmac.compare_digest(row.lease_token, value.lease_token):
        raise HTTPException(409, 'Подтверждение не соответствует попытке отправки')
    if row.status == 'sent' and value.success:
        return {'id': row.id, 'status': row.status}
    if row.status not in {'claimed', 'unknown'}:
        raise HTTPException(409, 'Эта попытка уже завершена')
    if value.success:
        row.status, row.sent_at, row.last_error = 'sent', now(), None
    elif value.uncertain:
        row.status, row.last_error = 'unknown', security.sanitize_trace(value.error or 'Результат отправки неизвестен')
    else:
        row.status = 'pending' if row.attempts < 3 else 'failed'
        row.last_error = security.sanitize_trace(value.error or 'Транспорт отклонил отправку')
    session.commit()
    return {'id': row.id, 'status': row.status}


@router.post('/subscriptions', status_code=201, response_model=SubscriptionOutput)
def subscribe(value: SubscriptionInput, request: Request, response: Response, session: Session = Depends(db)):
    key = request.headers.get('idempotency-key')
    response.headers['Cache-Control'] = 'no-store'
    idem_hash = None
    forwarded = getattr(request.state, 'client_identity', '')
    target = forwarded.split(':', 1)[1] if re.fullmatch(r'telegram:-?\d{1,20}', forwarded) else None

    def notification_status(row):
        return 'telegram' if row.transport == 'telegram' and notifications_enabled() else 'disabled'
    if key is not None:
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,200}', key):
            raise HTTPException(422, 'Idempotency-Key должен быть случайным секретом длиной 16–200 символов')
        if len(request.app.state.session_secret) < 32:
            raise HTTPException(503, 'Для повторяемого создания подписок нужен ADMIN_SESSION_SECRET длиной не менее 32 символов')
        idem_hash = security.hash_token('subscription:' + key)

    def capability(row_id):
        return hmac.new(request.app.state.session_secret.encode(),
                        f'subscription-delete:{row_id}:{key}'.encode(), hashlib.sha256).hexdigest()

    def existing_result(row):
        if any(getattr(row, field) != expected for field, expected in value.model_dump().items()):
            raise HTTPException(409, 'Этот ключ уже использован с другими условиями подписки')
        if row.transport_target != target:
            raise HTTPException(409, 'Ключ подписки принадлежит другому получателю')
        token = capability(row.id)
        if not hmac.compare_digest(row.token_hash, security.hash_token(token)):
            raise HTTPException(409, 'Секрет сервера изменён: сохраните исходный токен или создайте подписку с новым ключом')
        return {'id': row.id, 'delete_token': token, 'notifications': notification_status(row)}

    if idem_hash:
        existing = session.scalar(select(Subscription).where(Subscription.idem_hash == idem_hash))
        if existing:
            return existing_result(existing)
    row_id = uid()
    token = capability(row_id) if idem_hash else secrets.token_urlsafe(32)
    row = Subscription(id=row_id, **value.model_dump(), token_hash=security.hash_token(token), idem_hash=idem_hash,
                       transport='telegram' if target else None, transport_target=target)
    session.add(row)
    try:
        session.commit()
    except Exception as error:
        from sqlalchemy.exc import IntegrityError
        session.rollback()
        if idem_hash and isinstance(error, IntegrityError):
            existing = session.scalar(select(Subscription).where(Subscription.idem_hash == idem_hash))
            if existing:
                return existing_result(existing)
        raise
    return {'id': row.id, 'delete_token': token, 'notifications': notification_status(row)}


@router.delete('/subscriptions/{subscription_id}', status_code=204)
def unsubscribe(subscription_id: str, request: Request, session: Session = Depends(db)):
    header = request.headers.get('authorization', '')
    if not header.startswith('Bearer '):
        raise HTTPException(401, 'Нужен токен удаления подписки')
    row = session.get(Subscription, subscription_id)
    if row is None:
        raise HTTPException(404, 'Подписка не найдена')
    if not hmac.compare_digest(row.token_hash, security.hash_token(header[7:])):
        raise HTTPException(403, 'Неверный токен удаления')
    session.execute(delete(NotificationOutbox).where(NotificationOutbox.subscription_id == row.id))
    session.delete(row)
    session.commit()
    return Response(status_code=204)


@router.post('/feedback', status_code=201, response_model=FeedbackOutput)
def feedback(value: FeedbackInput, request: Request, session: Session = Depends(db)):
    if value.website:
        return {'id': uid(), 'status': 'received'}
    if value.event_id and session.get(Event, value.event_id) is None:
        raise HTTPException(404, 'Событие не найдено')
    key = request.headers.get('idempotency-key')
    if key and not re.fullmatch(r'[A-Za-z0-9_-]{16,200}', key):
        raise HTTPException(422, 'Некорректный Idempotency-Key')
    idem_hash = digest('feedback:' + key) if key else None

    def existing_result(row):
        if row.message != clean_text(value.message) or row.event_id != value.event_id or row.contact != value.contact:
            raise HTTPException(409, 'Ключ использован для другого обращения')
        return {'id': row.id, 'status': 'received'}

    if idem_hash:
        existing = session.scalar(select(Feedback).where(Feedback.idem_hash == idem_hash))
        if existing:
            return existing_result(existing)
    row = Feedback(message=clean_text(value.message), event_id=value.event_id, contact=value.contact, idem_hash=idem_hash)
    session.add(row)
    try:
        session.commit()
    except Exception as error:
        from sqlalchemy.exc import IntegrityError
        session.rollback()
        if idem_hash and isinstance(error, IntegrityError):
            existing = session.scalar(select(Feedback).where(Feedback.idem_hash == idem_hash))
            if existing:
                return existing_result(existing)
        raise
    return {'id': row.id, 'status': 'received'}


@admin.post('/ticket-evidence')
def ticket_evidence(value: TicketSnapshotInput, session: Session = Depends(db)):
    from .ticket_evidence import apply_snapshot
    try:
        return apply_snapshot(session,value)
    except ValueError as error:
        session.rollback()
        raise HTTPException(422,str(error)) from error


router.include_router(admin)
