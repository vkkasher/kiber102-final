"""Import and catalog domain operations shared by HTTP and scheduled work."""
import csv
from collections import Counter
import hashlib
import io
import json
import os
import math
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from .models import AuditLog, Event, EventSource, ProviderIdentity, RawSourceRecord, Source, now
from .schemas import EventInput, normalize


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def dedup_key(item):
    parts = [normalize(item.title), normalize(item.city), normalize(item.venue),
             item.date_start.astimezone(timezone.utc).isoformat()]
    return digest('|'.join(parts))


def iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def backfill_ticket_end_times(connection):
    """Normalize existing derived facts without changing snapshots or checks."""
    from sqlalchemy import JSON, String, column, table, update

    evidence = table('event_ticket_evidence', column('event_id', String), column('facts', JSON))
    rows = connection.execute(select(evidence).where(
        evidence.c.facts['date_end_utc'].as_string().is_(None),
        evidence.c.facts['date_end'].as_string().is_not(None))).mappings()
    changed = 0
    for row in rows:
        facts = row['facts']
        if not isinstance(facts, dict) or not isinstance(facts.get('date_end'), str):
            continue
        try:
            end = datetime.fromisoformat(facts['date_end'])
            if end.tzinfo is None or end.utcoffset() is None:
                continue
            normalized = end.astimezone(timezone.utc).isoformat(timespec='microseconds')
        except (ValueError, OverflowError):
            continue
        connection.execute(update(evidence).where(evidence.c.event_id == row['event_id']).values(
            facts={**facts, 'date_end_utc': normalized}))
        changed += 1
    return changed


def invalidate_ticket_identity(session, event, values):
    """Detach current observations after a session moves; keep immutable history."""
    evidence = event.ticket_evidence
    if evidence is None:
        return
    changed = any(name in values and normalize(values[name]) != normalize(getattr(event, name))
                  for name in ['title', 'city', 'venue'])
    if 'date_start' in values:
        previous_start = event.date_start
        if previous_start.tzinfo is None:
            previous_start = previous_start.replace(tzinfo=timezone.utc)
        changed = changed or values['date_start'] != previous_start
    if changed:
        audit(session, 'ticket.invalidate', event.id, {'source_url': evidence.source_url,
              'source_hash': evidence.source_hash, 'reason': 'session_identity_changed'})
        for source in event.sources:
            if source.url == evidence.source_url:
                source.verified_at = None
        event.ticket_evidence = None


def event_dict(event):
    result = {column.name: getattr(event, column.name) for column in Event.__table__.columns
              if column.name not in {'dedup_key', 'source_hash'}}
    for name in ['date_start', 'date_end', 'verified_at', 'created_at', 'updated_at']:
        result[name] = iso(result[name])
    result['sources'] = [{'url': row.url, 'name': row.name, 'verified_at': iso(row.verified_at)}
                         for row in event.sources]
    verified = event.verified_at
    if verified and verified.tzinfo is None:
        verified = verified.replace(tzinfo=timezone.utc)
    evidence = event.ticket_evidence
    result.update(image_url=None,image_source_url=None,ticket_availability=None,ticket_checked_at=None)
    if evidence:
        checked = evidence.checked_at.replace(tzinfo=timezone.utc) if evidence.checked_at.tzinfo is None else evidence.checked_at
        verified = max(verified,checked) if verified else checked
        result['verified_at'] = iso(verified)
        result.update(image_url=evidence.image_url,image_source_url=evidence.source_url,
                      ticket_availability=evidence.availability,ticket_checked_at=iso(evidence.checked_at))
        if checked >= verification_cutoff():
            for field in ['price_min','price_max','date_end']:
                # A missing maximum stays unknown, even if an older source had a tariff.
                if field in {'price_min','price_max'} or evidence.facts.get(field) is not None:
                    result[field] = evidence.facts.get(field)
            # Free entry requires a current complete zero tariff, not a lower
            # bound of zero with an unknown maximum or an older free flag.
            result['is_free'] = all(result.get(field) == 0 for field in ['price_min', 'price_max'])
    result['verification_expired'] = verified is None or verified < verification_cutoff()
    from .tickets import ticket_provider
    result['ticket_links'] = ticket_provider.links(event)
    if evidence and evidence.availability not in {'sold_out','off_sale','cancelled','rescheduled'}:
        result['ticket_links'].append({'url':evidence.source_url,'label':'Билеты на этот сеанс','provider':'kassy'})
    if evidence and evidence.availability in {'sold_out','off_sale','cancelled','rescheduled'}:
        result['ticket_links'] = []
    return result


def verification_cutoff():
    hours = max(1, int(os.getenv('SOURCE_VERIFICATION_MAX_AGE_HOURS', '48')))
    return now() - timedelta(hours=hours)


def audit(session, action, event_id=None, details=None):
    session.add(AuditLog(actor=session.info.get('actor', 'system'), action=action, event_id=event_id, details=details or {}))


def provider_scope(url):
    parsed = urlsplit(url)
    # www is an alias, whereas unrelated hosts keep independent ID namespaces.
    return (parsed.hostname or '').lower().removeprefix('www.')


def register_source(session, url):
    parsed = urlsplit(url)
    base = f'{parsed.scheme}://{parsed.netloc}'
    source = session.scalar(select(Source).where(Source.url == base))
    if source is None:
        try:
            with session.begin_nested():
                source = Source(url=base)
                session.add(source)
                session.flush()
        except IntegrityError:
            source = session.scalar(select(Source).where(Source.url == base))
    return source


def parse_rows(format, content):
    if format == 'json':
        rows = json.loads(content)
        if isinstance(rows, dict) and isinstance(rows.get('events'), list):
            rows = rows['events']
        if not isinstance(rows, list):
            raise ValueError('JSON должен содержать массив событий')
    else:
        text_content = content.lstrip('\ufeff')
        first_line = text_content.split('\n', 1)[0]
        try:
            delimiter = csv.Sniffer().sniff(first_line, delimiters=',;\t').delimiter
        except csv.Error:
            delimiter = ','
        try:
            reader = csv.DictReader(io.StringIO(text_content, newline=''), delimiter=delimiter,
                                    quotechar='"', doublequote=True, strict=True)
            headers = [name.strip() for name in (reader.fieldnames or [])]
            if not headers or any(not name for name in headers):
                raise ValueError('CSV должен содержать строку с названиями столбцов')
            duplicates = [name for name, count in Counter(headers).items() if count > 1]
            if duplicates:
                raise ValueError('Повторяются столбцы CSV: ' + ', '.join(duplicates))
            reader.fieldnames = headers
            rows = []
            for row in reader:
                if None in row:
                    raise ValueError(f'В строке CSV {reader.line_num} значений больше, чем столбцов')
                rows.append(row)
                if len(rows) > 1000:
                    raise ValueError('Не более 1000 записей за один импорт')
        except csv.Error as error:
            raise ValueError('Повреждён CSV: проверьте кавычки и разделители строк') from error
        for row in rows:
            for key in list(row):
                if row[key] == '':
                    row[key] = None
            if row.get('tags') and isinstance(row['tags'], str):
                row['tags'] = [tag.strip() for tag in row['tags'].split('|') if tag.strip()]
            if row.get('is_free') is None:
                row.pop('is_free', None)
    if len(rows) > 1000:
        raise ValueError('Не более 1000 записей за один импорт')
    return rows


def import_records(session, format, content):
    rows = parse_rows(format, content)
    report = dict(added=0, updated=0, rejected=0, duplicate=0, requires_review=0, details=[])
    for index, raw in enumerate(rows):
        payload = json.dumps(raw, ensure_ascii=False, sort_keys=True)
        source_hash = digest(payload)
        raw_record = RawSourceRecord(payload=payload, source_hash=source_hash, outcome='rejected',
                                     source_url=raw.get('source_url') if isinstance(raw, dict) else None)
        session.add(raw_record)
        try:
            item = EventInput.model_validate(raw)
        except ValidationError as error:
            reason = '; '.join(err['msg'] for err in error.errors(include_url=False, include_input=False))
            raw_record.reason = reason
            report['rejected'] += 1
            report['details'].append({'row': index + 1, 'status': 'rejected', 'reason': reason})
            continue
        key = dedup_key(item)
        if item.provider_id and session.bind.dialect.name == 'postgresql':
            # Also serialize the first observation, when no identity row exists
            # to lock yet. This transaction-scoped lock releases on rollback.
            number = int(digest(provider_scope(item.source_url) + ':' + item.provider_id)[:16], 16)
            if number >= 2**63:
                number -= 2**64
            session.execute(text('SELECT pg_advisory_xact_lock(:identity_lock)'), {'identity_lock': number})
        identity = session.scalar(select(ProviderIdentity).where(
            ProviderIdentity.source_scope == provider_scope(item.source_url),
            ProviderIdentity.provider_id == item.provider_id).with_for_update()) if item.provider_id else None
        event = session.get(Event, identity.event_id, with_for_update=True) if identity else None
        if event is None:
            event = session.scalar(select(Event).where(Event.dedup_key == key).with_for_update())
        before = event_dict(event) if event is not None else None
        conflicting = session.scalar(select(Event).where(Event.dedup_key == key)) if identity else None
        if conflicting is not None and conflicting.id != event.id:
            reason = 'ID провайдера указывает на другую карточку с теми же местом и временем; требуется ручное решение'
            event.status, event.verified_at, event.confidence = 'draft', None, 0
            for source in event.sources:
                source.verified_at = None
            raw_record.event_id, raw_record.reason = event.id, reason
            report['rejected'] += 1
            report['requires_review'] += 1
            report['details'].append({'row': index + 1, 'status': 'rejected', 'id': event.id, 'reason': reason})
            audit(session, 'import.identity_conflict', event.id, {'before': before, 'after': event_dict(event),
                                                                'conflicting_event_id': conflicting.id, 'reason': reason})
            continue
        values = item.model_dump(exclude={'region', 'provider_id', 'status'})
        values['normalized_title'] = normalize(item.title)
        values['source_hash'] = source_hash
        # Compare with the latest imported observation from this particular
        # source, not any historical match: A -> B -> A is a real update.
        previous = session.scalar(select(RawSourceRecord).where(
            RawSourceRecord.event_id == event.id,
            RawSourceRecord.source_url == item.source_url,
            RawSourceRecord.outcome.in_(['added', 'updated', 'duplicate']))
            .order_by(RawSourceRecord.created_at.desc()).limit(1)) if event is not None else None
        reason = 'Требуется проверка первоисточника и публикация модератором'
        outcome = 'added'
        if event is not None:
            source_known = any(source.url == item.source_url for source in event.sources)
            withdrawal = item.status in {'cancelled', 'rescheduled'}
            if withdrawal and (event.status != item.status or not source_known):
                outcome = 'updated'
                event.status = item.status
                reason = 'Источник сообщает об отмене или переносе; снято с выдачи до решения модератора'
            elif (previous is not None and previous.source_hash == source_hash) or (not source_known and identity is None):
                outcome = 'duplicate'
                reason = 'Совпадают название, город, площадка и точное время сеанса'
            else:
                outcome = 'updated'
                # Never allow importer to republish or silently change a published record.
                if withdrawal:
                    # Withdraw immediately and preserve the permanent, previously verified card.
                    event.status = item.status
                else:
                    invalidate_ticket_identity(session, event, values)
                    for name, value in values.items():
                        setattr(event, name, value)
                    # An import cannot undo a withdrawal. Only an explicit moderator
                    # decision after source verification may restore publication.
                    if event.status not in {'cancelled', 'rescheduled'}:
                        event.status = 'draft'
                    event.verified_at = None
                    event.confidence = 0
                    for source in event.sources:
                        source.verified_at = None
                    event.dedup_key = key
        else:
            candidates = session.scalars(select(Event).where(Event.city == item.city,
                                         Event.date_start == item.date_start, Event.venue == item.venue)).all()
            if any(SequenceMatcher(None, normalize(item.title), row.normalized_title).ratio() > .85
                   for row in candidates):
                reason = 'Возможный дубль: близкое название и совпадающие место и время; нужно ручное решение'
            event = Event(**values, dedup_key=key, status='draft', confidence=0)
            try:
                with session.begin_nested():
                    session.add(event)
                    session.flush()
            except IntegrityError:
                event = session.scalar(select(Event).where(Event.dedup_key == key))
                outcome = 'duplicate'
                reason = 'Повтор конкурирующего импорта'
        if not any(source.url == item.source_url for source in event.sources):
            event.sources.append(EventSource(url=item.source_url, name=urlsplit(item.source_url).hostname))
        register_source(session, item.source_url)
        if item.provider_id and identity is None:
            with session.begin_nested():
                session.add(ProviderIdentity(source_scope=provider_scope(item.source_url), provider_id=item.provider_id, event_id=event.id))
                session.flush()
        # Each source's observation timestamps form a strictly increasing
        # sequence. UUIDs cannot be used as chronological tie breakers.
        observed_at = now()
        if previous is not None:
            prior_at = previous.created_at
            if prior_at.tzinfo is None:
                prior_at = prior_at.replace(tzinfo=timezone.utc)
            if observed_at <= prior_at:
                observed_at = prior_at + timedelta(microseconds=1)
        raw_record.created_at = observed_at
        raw_record.event_id = event.id
        raw_record.outcome = outcome
        raw_record.reason = reason
        report[outcome] += 1
        if outcome in {'added', 'updated'}:
            report['requires_review'] += 1
        report['details'].append({'row': index + 1, 'status': outcome, 'id': event.id, 'reason': reason})
        audit(session, 'import.' + outcome, event.id, {'source_hash': source_hash, 'reason': reason,
                                                      'before': before, 'after': event_dict(event)})
    session.commit()
    return report


def publication_problem(event):
    if not event.verified_at or event.confidence < .8:
        return 'Сначала подтвердите первоисточник'
    if not event.address or not event.description or event.age_limit is None:
        return 'Для публикации нужны адрес, описание и возрастное ограничение'
    start = event.date_end or event.date_start
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if start < now():
        return 'Завершившееся событие нельзя публиковать как актуальное'
    return None


def reserve_llm_usage(factory, identity_hash, reservation_cost, token_limit, currency, dialog_id=None):
    """Reserve the worst-case charge before a provider call, without refundable credit.

    A conditional SQL UPDATE is the concurrency boundary on PostgreSQL and SQLite.
    Failure/timeout still consumes the reservation to prevent ambiguous double spend.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from sqlalchemy import update
    from .models import AgentDialog, CostUsage, LLMQuota, uid
    from .llm_config import is_local_llm_config
    try:
        daily_max = float(os.getenv('LLM_DAILY_MAX_COST', '0'))
        daily_requests = int(os.getenv('LLM_DAILY_REQUEST_LIMIT', '30'))
        daily_tokens = int(os.getenv('LLM_DAILY_TOKEN_LIMIT', '100000'))
        local_free = is_local_llm_config() and reservation_cost == 0 and currency == 'RUB'
        if (not isinstance(identity_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', identity_hash)
                or not math.isfinite(reservation_cost) or reservation_cost < 0
                or (reservation_cost == 0 and not local_free)
                or not math.isfinite(daily_max) or daily_max < 0
                or (daily_max == 0 and not local_free)
                or not isinstance(token_limit, int) or not 0 < token_limit <= 1_000_000
                or not re.fullmatch(r'[A-Z]{3,8}', currency)):
            return False
    except (ValueError, TypeError):
        return False
    day = datetime.now(ZoneInfo('Asia/Yekaterinburg')).date().isoformat()
    with factory() as session:
        if dialog_id:
            try:
                conversion = 1.0 if currency == 'RUB' else float(os.getenv('LLM_RUB_PER_CURRENCY_UNIT', '0'))
                if not math.isfinite(conversion) or conversion <= 0:
                    return False
                rubles = reservation_cost * conversion
                maximum = min(3.0, float(os.getenv('LLM_DIALOG_MAX_RUB', '3')))
                if not 0 < maximum <= 3:
                    return False
            except (ValueError, TypeError):
                return False
            if session.execute(update(AgentDialog).where(
                    AgentDialog.id == dialog_id, AgentDialog.identity_hash == identity_hash,
                    AgentDialog.reserved_cost + rubles <= maximum).values(
                        reserved_cost=AgentDialog.reserved_cost + rubles,
                        reserved_tokens=AgentDialog.reserved_tokens + token_limit)).rowcount != 1:
                session.rollback()
                return False
        if session.get(LLMQuota, (identity_hash, day)) is None:
            try:
                with session.begin_nested():
                    session.add(LLMQuota(identity_hash=identity_hash, day=day, currency=currency,
                                         requests=0, reserved_cost=0, reserved_tokens=0))
                    session.flush()
            except IntegrityError:
                pass  # Another request initialized this identity/day before us.
        # Switching away from a free local model must preserve request/token
        # counters. Only a zero monetary balance can change currency safely.
        session.execute(update(LLMQuota).where(
            LLMQuota.identity_hash == identity_hash, LLMQuota.day == day,
            LLMQuota.currency != currency, LLMQuota.reserved_cost == 0
        ).values(currency=currency))
        statement = update(LLMQuota).where(
            LLMQuota.identity_hash == identity_hash, LLMQuota.day == day, LLMQuota.currency == currency,
            LLMQuota.reserved_cost + reservation_cost <= daily_max,
            LLMQuota.requests + 1 <= daily_requests,
            LLMQuota.reserved_tokens + token_limit <= daily_tokens).values(
                reserved_cost=LLMQuota.reserved_cost + reservation_cost,
                requests=LLMQuota.requests + 1, reserved_tokens=LLMQuota.reserved_tokens + token_limit)
        if session.execute(statement).rowcount != 1:
            session.rollback()
            return False
        session.add(CostUsage(trace_id=uid(), tokens=token_limit, estimated_cost=reservation_cost))
        session.commit()
    return True


def configure_agent_state(app, factory):
    """Durable, identity-bound action intents and server-issued dialog budgets."""
    import hmac
    import secrets
    from fastapi import HTTPException
    from .models import AgentAction, AgentDialog

    def sign_dialog(row_id, identity):
        secret = app.state.session_secret
        if len(secret) < 32:
            # No valid signed budget means external LLM use fails closed.
            return None
        signature = hmac.new(secret.encode(), f'dialog:{identity}:{row_id}'.encode(), hashlib.sha256).hexdigest()
        return row_id + '.' + signature

    def dialog_row_id(identity, token):
        if not isinstance(token, str) or '.' not in token:
            return None
        row_id = token.split('.')[0]
        expected = sign_dialog(row_id, identity)
        return row_id if expected and hmac.compare_digest(token, expected) else None

    def ensure_dialog(identity, token):
        if token:
            row_id = dialog_row_id(identity, token)
            with factory() as session:
                row = session.get(AgentDialog, row_id) if row_id else None
                if row is None or row.identity_hash != identity:
                    raise HTTPException(422, 'Диалог недействителен или принадлежит другому пользователю')
                return token
        # Omitting/resetting client state cannot reset a day's monetary budget.
        dialog_key = identity + ':' + now().date().isoformat()
        with factory() as session:
            row = session.scalar(select(AgentDialog).where(AgentDialog.dialog_key == dialog_key))
            if row is None:
                try:
                    with session.begin_nested():
                        row = AgentDialog(identity_hash=identity, dialog_key=dialog_key)
                        session.add(row)
                        session.flush()
                except IntegrityError:
                    row = session.scalar(select(AgentDialog).where(AgentDialog.dialog_key == dialog_key))
            session.commit()
            return sign_dialog(row.id, identity)

    def reserve_dialog(identity, token, cost, tokens, currency):
        row_id = dialog_row_id(identity, token)
        if not row_id:
            return False
        try:
            return reserve_llm_usage(factory, identity, cost, tokens, currency, dialog_id=row_id)
        except Exception:
            # A budget store outage cannot permit an unreserved external call.
            return False

    def prepare(identity, tool, arguments):
        if tool not in {'subscribe', 'save_feedback'} or len(json.dumps(arguments)) > 10000:
            raise ValueError('Недопустимое действие')
        with factory() as session:
            row = AgentAction(nonce=secrets.token_urlsafe(32), identity_hash=identity, tool=tool,
                              arguments=arguments, expires_at=now() + timedelta(minutes=10))
            session.add(row)
            session.commit()
            return {'nonce': row.nonce, 'tool': row.tool, 'arguments': row.arguments,
                    'expires_at': iso(row.expires_at), 'result': None}

    def get_action(identity, nonce):
        with factory() as session:
            row = session.get(AgentAction, nonce)
            if row is None or row.identity_hash != identity:
                return None
            expires = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
            if expires < now() and row.result is None:
                return None
            return {'nonce': row.nonce, 'tool': row.tool, 'arguments': row.arguments,
                    'expires_at': iso(row.expires_at), 'result': row.result}

    def complete(identity, nonce, result):
        with factory() as session:
            row = session.get(AgentAction, nonce, with_for_update=True)
            if row is None or row.identity_hash != identity:
                raise ValueError('Действие не найдено')
            if row.result is None:
                row.result = result
                session.commit()

    app.state.ensure_agent_dialog = ensure_dialog
    app.state.reserve_llm_dialog = reserve_dialog
    app.state.prepare_agent_action = prepare
    app.state.get_agent_action = get_action
    app.state.complete_agent_action = complete


def enforce_retention(session):
    from sqlalchemy import delete
    from .models import AgentAction, AgentDialog, AgentTrace, CostUsage, Feedback, LLMQuota, NotificationOutbox, Subscription
    policies = {AgentTrace: 30, AgentAction: 30, AgentDialog: 30, Feedback: 90,
                NotificationOutbox: 30, Subscription: 180, CostUsage: 30, RawSourceRecord: 90}
    deleted = {}
    # SQLite's local fallback may predate enabled FK enforcement. Remove child
    # delivery intents explicitly before removing an expired subscription.
    expired_subscriptions = select(Subscription.id).where(Subscription.created_at < now() - timedelta(days=180))
    orphaned_deliveries = session.execute(delete(NotificationOutbox).where(
        NotificationOutbox.subscription_id.in_(expired_subscriptions))).rowcount
    for model, days in policies.items():
        deleted[model.__tablename__] = session.execute(
            delete(model).where(model.created_at < now() - timedelta(days=days))).rowcount
    deleted['notification_outbox'] += orphaned_deliveries
    deleted['llm_quotas'] = session.execute(delete(LLMQuota).where(
        LLMQuota.day < (now() - timedelta(days=30)).date().isoformat())).rowcount
    audit(session, 'maintenance.retention', details={'deleted': deleted})
    session.commit()
    return {'deleted': deleted, 'retention_days': {model.__tablename__: days for model, days in policies.items()}}
