import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.api.test_api import record, upload


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-private-password')
    monkeypatch.setenv('ADMIN_SESSION_SECRET', 'isolated-session-secret-that-is-long-enough')
    monkeypatch.setenv('SOURCE_ALLOWLIST', 'culture.ru,www.culture.ru,rusdram.ru')
    from apps.api.app.main import create_app
    with TestClient(create_app(f'sqlite:///{tmp_path / "finish.db"}')) as value:
        yield value


@pytest.fixture
def auth(client):
    return {'Authorization': 'Bearer ' + client.post('/api/v1/admin/login', json={'password': 'test-private-password'}).json()['token']}


def publish(client, auth, item):
    event_id = upload(client, auth, [item]).json()['details'][0]['id']
    assert client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth, json={'note': 'Сверено с источником'}).status_code == 200
    assert client.patch(f'/api/v1/admin/events/{event_id}', headers=auth, json={'status': 'published'}).status_code == 200
    return event_id


def test_secondary_cancellation_withdraws_and_replay_never_republishes(client, auth):
    item = record()
    event_id = publish(client, auth, item)
    cancellation = item | {'source_url': 'https://rusdram.ru/cancelled/123', 'status': 'cancelled'}
    result = upload(client, auth, [cancellation]).json()
    assert result['requires_review'] == 1
    assert client.get('/api/v1/events').json()['total'] == 0
    assert client.get(f'/api/v1/events/{event_id}').json()['status'] == 'cancelled'
    upload(client, auth, [cancellation])
    upload(client, auth, [item])
    assert client.get('/api/v1/events').json()['total'] == 0


def test_provider_identity_survives_rename_move_and_source_url_change(client, auth):
    item = record(provider_id='session-1')
    event_id = publish(client, auth, item)
    moved = item | {'title': 'Новое название сеанса', 'date_start': (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(), 'venue': 'Другой зал', 'source_url': 'https://www.culture.ru/events/renamed'}
    report = upload(client, auth, [moved]).json()
    assert report['updated'] == 1 and report['added'] == 0
    assert report['details'][0]['id'] == event_id
    rows = client.get('/api/v1/admin/events', headers=auth).json()
    assert rows['total'] == 1
    assert rows['items'][0]['status'] != 'published'
    assert rows['items'][0]['title'] == moved['title']
    # IDs from another source never collide.
    other = moved | {'provider_id': 'session-1', 'source_url': 'https://rusdram.ru/session-1', 'title': 'Другой организатор'}
    assert upload(client, auth, [other]).json()['added'] == 1


@pytest.mark.parametrize('params', [{'page': 10**30}, {'date_to': '9999-12-31'}, {'date_from': '0001-01-01'}])
def test_extreme_dates_pages_are_controlled(client, params):
    response = client.get('/api/v1/events', params=params)
    assert response.status_code == 422
    assert response.json()['code'] == 'validation_error'


def test_empty_source_url_is_rejected_per_row(client, auth):
    result = upload(client, auth, [record(source_url=''), record(source_url='https://culture.ru/good')])
    assert result.status_code == 200
    assert result.json()['rejected'] == 1 and result.json()['added'] == 1


def test_manual_create_search_pagination_and_complete_audit(client, auth):
    created = client.post('/api/v1/admin/events', headers=auth, json=record())
    assert created.status_code == 201
    event_id = created.json()['id']
    assert created.json()['status'] == 'draft'
    edited = client.patch(f'/api/v1/admin/events/{event_id}', headers=auth, json={'title': 'Искомое новое название'})
    assert edited.status_code == 200
    result = client.get('/api/v1/admin/events', headers=auth, params={'q': 'Искомое', 'city': 'Уфа', 'page_size': 1})
    assert result.json()['total'] == 1
    assert client.get('/api/v1/admin/events', headers=auth, params={'q': 'нет такого'}).json()['total'] == 0
    change = next(row for row in client.get('/api/v1/admin/audit', headers=auth).json() if row['action'] == 'event.edit')
    assert change['details']['before']['title'] == record()['title']
    assert change['details']['after']['title'] == 'Искомое новое название'


def test_canonical_alias_and_strict_source_type(client, auth):
    result = upload(client, auth, [record(city='г. Уфа', venue='Башкирский государственный театр оперы и балета'), record(source_type='made-up')]).json()
    assert result['added'] == 1 and result['rejected'] == 1
    event = client.get('/api/v1/admin/events', headers=auth).json()['items'][0]
    assert event['city'] == 'Уфа'
    assert event['venue'] == 'Башкирский театр оперы и балета'


def signed(identity, secret='a-test-proxy-secret-with-32-characters'):
    import hashlib
    import hmac
    import time
    stamp = str(int(time.time()))
    return {'X-Client-Identity': identity, 'X-Client-Timestamp': stamp,
            'X-Client-Signature': hmac.new(secret.encode(), f'{stamp}\n{identity}'.encode(), hashlib.sha256).hexdigest()}


def test_proxy_identity_separates_rate_limits_and_rejects_spoof(client, monkeypatch):
    monkeypatch.setenv('TRUSTED_PROXY_SECRET', 'a-test-proxy-secret-with-32-characters')
    one = signed('web:aaaaaaaaaaaaaaaaaaaaaaaa')
    two = signed('web:bbbbbbbbbbbbbbbbbbbbbbbb')
    for _ in range(10):
        assert client.post('/api/v1/admin/login', headers=one, json={'password': 'incorrect'}).status_code == 401
    assert client.post('/api/v1/admin/login', headers=one, json={'password': 'incorrect'}).status_code == 429
    assert client.post('/api/v1/admin/login', headers=two, json={'password': 'incorrect'}).status_code == 401
    spoof = two | {'X-Client-Signature': 'bad'}
    assert client.get('/api/v1/health', headers=spoof).status_code == 401


def test_safe_request_id_all_request_log_and_bounded_stream(client):
    import io
    import logging
    handler = next(handler for handler in logging.getLogger('kiber102.api').handlers
                   if getattr(handler, '_kiber102_api_handler', False))
    stream = io.StringIO()
    original = handler.setStream(stream)
    try:
        response = client.get('/api/v1/health', headers={'X-Request-ID': 'test-request-1234'})
    finally:
        handler.setStream(original)
    assert response.headers['X-Request-ID'] == 'test-request-1234'
    entry = json.loads(stream.getvalue().splitlines()[0])
    assert entry['request_id'] == 'test-request-1234' and entry['status'] == 200
    assert 'password' not in entry and 'query' not in entry
    response = client.post('/api/v1/feedback', content=iter([b'a' * 3_000_000, b'b' * 3_000_000]), headers={'Content-Type': 'application/json'})
    assert response.status_code == 413
    assert response.headers['X-Request-ID'] == response.json()['request_id']


def test_named_accounts_roles_audit_and_feedback_lifecycle(client, monkeypatch):
    import hashlib
    password = 'separate-secure-password'
    encoded = 'pbkdf2_sha256$600000$test-random-salt$' + hashlib.pbkdf2_hmac('sha256', password.encode(), b'test-random-salt', 600000).hex()
    monkeypatch.setenv('ADMIN_ACCOUNTS_JSON', json.dumps({'olga': {'password_hash': encoded, 'role': 'moderator'}, 'reader': {'password_hash': encoded, 'role': 'viewer'}}))
    login = client.post('/api/v1/admin/login', json={'username': 'olga', 'password': password})
    assert login.status_code == 200 and login.json()['actor'] == 'olga' and login.json()['role'] == 'moderator'
    headers = {'Authorization': 'Bearer ' + login.json()['token']}
    created = client.post('/api/v1/admin/events', headers=headers, json=record())
    assert created.status_code == 201
    assert client.get('/api/v1/admin/audit', headers=headers).json()[0]['actor'] == 'olga'
    viewer = client.post('/api/v1/admin/login', json={'username': 'reader', 'password': password}).json()
    view_headers = {'Authorization': 'Bearer ' + viewer['token']}
    assert client.get('/api/v1/admin/events', headers=view_headers).status_code == 200
    assert client.post('/api/v1/admin/events', headers=view_headers, json=record()).status_code == 403
    response = client.post('/api/v1/feedback', json={'message': 'Заявка организатора на исправление'})
    feedback_id = response.json()['id']
    assert client.patch('/api/v1/admin/feedback/' + feedback_id, headers=headers, json={'status': 'resolved', 'note': 'Адрес сверили и исправили'}).status_code == 200
    assert client.get('/api/v1/admin/feedback', headers=headers).json()[0]['status'] == 'resolved'


def test_feedback_action_retries_are_idempotent(client):
    headers = {'Idempotency-Key': 'a-random-action-key-123456789'}
    value = {'message': 'Адрес необходимо проверить'}
    first = client.post('/api/v1/feedback', json=value, headers=headers)
    second = client.post('/api/v1/feedback', json=value, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()['id'] == second.json()['id']
    assert client.post('/api/v1/feedback', json={'message': 'Совсем другое сообщение'}, headers=headers).status_code == 409


def test_dialog_budget_is_durable_identity_bound_and_cannot_reset(client, monkeypatch):
    from apps.api.app.main import create_app
    monkeypatch.setenv('LLM_DAILY_MAX_COST', '20')
    monkeypatch.setenv('LLM_DAILY_REQUEST_LIMIT', '100')
    identity = 'a' * 64
    dialog = client.app.state.ensure_agent_dialog(identity, None)
    assert client.app.state.ensure_agent_dialog(identity, None) == dialog
    for _ in range(3):
        assert client.app.state.reserve_llm_dialog(identity, dialog, 1, 100, 'RUB')
    assert not client.app.state.reserve_llm_dialog(identity, dialog, .01, 100, 'RUB')
    assert not client.app.state.reserve_llm_dialog('b' * 64, dialog, .01, 100, 'RUB')
    with TestClient(create_app(str(client.app.state.engine.url))) as restarted:
        assert restarted.app.state.ensure_agent_dialog(identity, None) == dialog
        assert not restarted.app.state.reserve_llm_dialog(identity, dialog, .01, 100, 'RUB')


def test_pending_actions_are_durable_expiring_identity_bound(client):
    identity = 'c' * 64
    action = client.app.state.prepare_agent_action(identity, 'subscribe', {'city': 'Уфа'})
    assert client.app.state.get_agent_action('d' * 64, action['nonce']) is None
    assert client.app.state.get_agent_action(identity, action['nonce'])['arguments'] == {'city': 'Уфа'}
    client.app.state.complete_agent_action(identity, action['nonce'], {'id': 'created'})
    assert client.app.state.get_agent_action(identity, action['nonce'])['result'] == {'id': 'created'}


def test_notification_outbox_is_signed_target_bound_durable_and_no_duplicate_send(client, auth, monkeypatch):
    from apps.api.app.models import Subscription
    from sqlalchemy import select
    monkeypatch.setenv('TRUSTED_PROXY_SECRET', 'a-test-proxy-secret-with-32-characters')
    monkeypatch.setenv('NOTIFICATIONS_ENABLED', '1')
    # Raw public payload cannot nominate an arbitrary chat recipient.
    assert client.post('/api/v1/subscriptions', json={'city': 'Уфа', 'chat_id': '123'}).status_code == 422
    sub = client.post('/api/v1/subscriptions', headers=signed('telegram:123456789'), json={'city': 'Уфа'})
    assert sub.status_code == 201 and sub.json()['notifications'] == 'telegram'
    browser = client.post('/api/v1/subscriptions', json={'city': 'Уфа'}).json()
    assert browser['notifications'] == 'disabled'
    event_id = publish(client, auth, record())
    enqueue = client.post('/api/v1/admin/notifications/enqueue', headers=auth)
    assert enqueue.status_code == 200 and enqueue.json()['queued'] == 1
    assert client.post('/api/v1/admin/notifications/enqueue', headers=auth).json()['queued'] == 0
    assert client.post('/api/v1/admin/notifications/claim', json={'limit': 5}).status_code == 401
    claimed = client.post('/api/v1/admin/notifications/claim', headers=auth, json={'limit': 5}).json()['items']
    assert len(claimed) == 1 and claimed[0]['chat_id'] == '123456789'
    assert claimed[0]['event']['id'] == event_id
    assert client.post('/api/v1/admin/notifications/claim', headers=auth, json={'limit': 5}).json()['items'] == []
    item = claimed[0]
    assert client.post(f'/api/v1/admin/notifications/{item["id"]}/ack', headers=auth, json={'lease_token': 'bad', 'success': True}).status_code == 409
    assert client.post(f'/api/v1/admin/notifications/{item["id"]}/ack', headers=auth, json={'lease_token': item['lease_token'], 'success': True}).json()['status'] == 'sent'
    assert client.post('/api/v1/admin/notifications/enqueue', headers=auth).json()['queued'] == 0
    with client.app.state.session_factory() as session:
        assert session.scalar(select(Subscription).where(Subscription.id == sub.json()['id'])).transport_target == '123456789'


def test_unknown_delivery_outcome_never_auto_retries(client, auth, monkeypatch):
    from apps.api.app.models import NotificationOutbox
    from sqlalchemy import select
    monkeypatch.setenv('TRUSTED_PROXY_SECRET', 'a-test-proxy-secret-with-32-characters')
    monkeypatch.setenv('NOTIFICATIONS_ENABLED', '1')
    client.post('/api/v1/subscriptions', headers=signed('telegram:987654321'), json={'city': 'Уфа'})
    publish(client, auth, record())
    client.post('/api/v1/admin/notifications/enqueue', headers=auth)
    claim = client.post('/api/v1/admin/notifications/claim', headers=auth, json={'limit': 5}).json()['items'][0]
    with client.app.state.session_factory() as session:
        row = session.get(NotificationOutbox, claim['id'])
        row.claimed_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        session.commit()
    assert client.post('/api/v1/admin/notifications/claim', headers=auth, json={'limit': 5}).json()['items'] == []
    with client.app.state.session_factory() as session:
        assert session.scalar(select(NotificationOutbox)).status == 'unknown'


def test_approved_poster_and_connected_ticket_provider(client, auth, monkeypatch):
    monkeypatch.setenv('POSTER_ALLOWED_HOSTS', 'images.culture.ru')
    image = 'https://images.culture.ru/real-source-poster.jpg'
    item = record(poster_url=image, poster_rights='Разрешение правообладателя, тестовая фикстура', org_link='https://ufa.kassy.ru/events/session-123')
    event = client.post('/api/v1/admin/events', headers=auth, json=item)
    assert event.status_code == 201
    assert event.json()['poster_url'] == image
    assert event.json()['poster_rights'] == item['poster_rights']
    assert client.post('/api/v1/admin/events', headers=auth, json=item | {'poster_rights': None}).status_code == 422
    assert event.json()['ticket_links'] == [{'url': item['org_link'], 'label': 'Страница продажи билетов', 'provider': 'kassy'}]
    for url in ['javascript:alert(1)', 'http://images.culture.ru/a.jpg', 'https://unapproved.test/a.jpg']:
        assert client.post('/api/v1/admin/events', headers=auth, json=item | {'poster_url': url}).status_code == 422


def test_provider_concurrent_import_and_conflicting_session_do_not_crash(client, auth):
    from concurrent.futures import ThreadPoolExecutor
    item = record(provider_id='session-shared-concurrency')
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: upload(client, auth, [item]), range(6)))
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    assert sum(response.json()['added'] for response in responses) == 1
    # A provider rename into another existing session cannot overwrite or merge it silently.
    other = item | {'provider_id': 'another-session', 'title': 'Другая существующая карточка'}
    assert upload(client, auth, [other]).json()['added'] == 1
    conflict = other | {'provider_id': item['provider_id']}
    response = upload(client, auth, [conflict])
    assert response.status_code == 200
    assert response.json()['requires_review'] == 1
    assert client.get('/api/v1/admin/events', headers=auth).json()['total'] == 2


def test_verify_and_source_changes_have_full_audit_before_after(client, auth):
    identifier = upload(client, auth, [record()]).json()['details'][0]['id']
    client.post(f'/api/v1/admin/events/{identifier}/verify', headers=auth, json={'note': 'Полная ручная проверка'})
    source = client.get('/api/v1/admin/sources', headers=auth).json()[0]
    client.patch('/api/v1/admin/sources/' + source['id'], headers=auth, json={'status': 'disabled'})
    logs = client.get('/api/v1/admin/audit', headers=auth).json()
    verified = next(row for row in logs if row['action'] == 'event.verify')
    assert verified['details']['before']['verified_at'] is None
    assert verified['details']['after']['confidence'] == .85
    changed = next(row for row in logs if row['action'] == 'source.status')
    assert changed['details']['before']['status'] == 'manually_verified'
    assert changed['details']['after']['status'] == 'disabled'


def test_source_disable_cannot_be_undone_by_event_verification(client, auth):
    identifier = upload(client, auth, [record()]).json()['details'][0]['id']
    source = client.get('/api/v1/admin/sources', headers=auth).json()[0]
    assert client.patch('/api/v1/admin/sources/' + source['id'], headers=auth, json={'status': 'blocked'}).status_code == 200
    assert client.post(f'/api/v1/admin/events/{identifier}/verify', headers=auth, json={'note': 'Ручная проверка источника'}).status_code == 409
    assert client.get('/api/v1/admin/sources', headers=auth).json()[0]['status'] == 'blocked'


def test_feedback_html_empty_is_not_a_valid_message(client):
    assert client.post('/api/v1/feedback', json={'message': '<p> </p>'}).status_code == 422


def test_retention_removes_action_private_payload_and_subscription_outbox(client, auth):
    from apps.api.app.models import AgentAction, AgentDialog, NotificationOutbox, Subscription
    from sqlalchemy import func, select
    identifier = upload(client, auth, [record()]).json()['details'][0]['id']
    old = datetime.now(timezone.utc) - timedelta(days=200)
    with client.app.state.session_factory() as session:
        sub = Subscription(city='Уфа', token_hash='1' * 64, created_at=old)
        session.add(sub)
        session.flush()
        session.add(NotificationOutbox(subscription_id=sub.id, event_id=identifier, version='1' * 64, created_at=old))
        session.add(AgentAction(nonce='old-action', identity_hash='1' * 64, tool='save_feedback', arguments={'contact': 'old-private-contact'}, created_at=old, expires_at=old))
        session.add(AgentDialog(dialog_key='old-dialog', identity_hash='1' * 64, created_at=old))
        session.commit()
    result = client.post('/api/v1/admin/maintenance', headers=auth)
    assert result.status_code == 200
    assert result.json()['deleted']['agent_actions'] == 1
    assert result.json()['deleted']['agent_dialogs'] == 1
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(NotificationOutbox)) == 0


def test_notification_boolean_config_and_early_telegram_identifier(client, monkeypatch):
    monkeypatch.setenv('TRUSTED_PROXY_SECRET', 'a-test-proxy-secret-with-32-characters')
    monkeypatch.setenv('NOTIFICATIONS_ENABLED', 'true')
    response = client.post('/api/v1/subscriptions', headers=signed('telegram:1'), json={'city': 'Уфа'})
    assert response.status_code == 201 and response.json()['notifications'] == 'telegram'
    assert client.get('/api/v1/health').json()['notification_transport'] == 'enabled'


@pytest.mark.parametrize('configuration', ['{invalid', '[]', '{}', '{"admin":{"role":"superuser"}}'])
def test_invalid_named_account_configuration_never_falls_back_to_shared_admin(client, monkeypatch, configuration):
    monkeypatch.setenv('ADMIN_ACCOUNTS_JSON', configuration)
    result = client.post('/api/v1/admin/login', json={'password': 'test-private-password'})
    assert result.status_code == 503
    assert result.json()['code'] == 'unavailable'


def test_verified_tariff_is_an_active_deduplicated_card_source(client, auth, monkeypatch):
    from apps.api.app import security
    from apps.worker.sources import SourceRegistry
    monkeypatch.setenv('SOURCE_ALLOWLIST', 'rusdramteatr.ru')
    item = record(source_url='https://rusdramteatr.ru/afisha')
    event_id = upload(client, auth, [item]).json()['details'][0]['id']
    raw = json.dumps({'format': 'catalog-evidence-v1', 'listing': 'fixture',
                      'documents': {'https://rusdramteatr.ru/bilety': 'tariff'},
                      'manifest': [{'url': 'https://rusdramteatr.ru/bilety', 'sha256': 'a' * 64}]})
    monkeypatch.setattr(security, 'fetch_source', lambda url: raw)
    monkeypatch.setattr(security, 'validate_source_url', lambda url: None)
    monkeypatch.setattr(type(SourceRegistry().adapters['sterlitamak']), 'parse', lambda *args: [item])
    for _ in range(2):
        response = client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth,
                               json={'fetch': True, 'note': 'Fixture-only source verification'})
        assert response.status_code == 200, response.text
        sources = response.json()['sources']
        tariffs = [source for source in sources if source['url'] == 'https://rusdramteatr.ru/bilety']
        assert len(tariffs) == 1 and tariffs[0]['verified_at']
