import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-private-password')
    monkeypatch.setenv('ADMIN_SESSION_SECRET', 'isolated-session-secret-that-is-long-enough')
    monkeypatch.setenv('SOURCE_ALLOWLIST', 'culture.ru,www.culture.ru,rusdram.ru')
    from apps.api.app.main import create_app
    with TestClient(create_app(f'sqlite:///{tmp_path / "test.db"}')) as client:
        yield client


@pytest.fixture
def auth(client):
    result = client.post('/api/v1/admin/login', json={'password': 'test-private-password'})
    assert result.status_code == 200
    return {'Authorization': 'Bearer ' + result.json()['token']}


def record(**updates):
    result = dict(title='Тестовый сеанс (fixture)', category='спектакль', city='Уфа',
                  venue='Тестовый зал', address='Тестовый адрес', description='Только тестовые данные',
                  date_start=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
                  source_url='https://www.culture.ru/events/123456/test', price_min=100, price_max=200,
                  age_limit=6)
    return result | updates


def upload(client, auth, rows):
    return client.post('/api/v1/admin/import', headers=auth, json={'format': 'json', 'content': json.dumps(rows)})


def test_auth_and_private_drafts(client, auth):
    assert client.get('/api/v1/admin/events').status_code == 401
    assert client.post('/api/v1/admin/login', json={'password': 'wrong'}).status_code == 401
    result = upload(client, auth, [record()])
    assert result.status_code == 200
    assert result.json()['added'] == 1
    event_id = result.json()['details'][0]['id']
    assert client.get('/api/v1/events').json()['total'] == 0
    assert client.get('/api/v1/events/' + event_id).status_code == 404
    assert client.patch('/api/v1/admin/events/' + event_id, headers=auth, json={'status': 'published'}).status_code == 409
    assert client.post('/api/v1/admin/events/' + event_id + '/verify', headers=auth, json={'note': 'Сверено модератором с первоисточником'}).status_code == 200
    assert client.patch('/api/v1/admin/events/' + event_id, headers=auth, json={'status': 'published'}).status_code == 200
    assert client.get('/api/v1/events', params={'price_max': 99}).json()['total'] == 0
    assert client.get('/api/v1/events', params={'city': 'Уфа', 'price_max': 200, 'age': 6}).json()['total'] == 1
    assert client.patch('/api/v1/admin/events/' + event_id, headers=auth, json={'status': 'cancelled'}).status_code == 200
    assert client.get('/api/v1/events').json()['total'] == 0
    assert client.get('/api/v1/events/' + event_id).json()['status'] == 'cancelled'
    assert len(client.get('/api/v1/admin/audit', headers=auth).json()) >= 4


def test_import_idempotence_sources_and_sessions(client, auth):
    item = record()
    first = upload(client, auth, [item]).json()
    assert upload(client, auth, [item]).json()['duplicate'] == 1
    other = item | {'source_url': 'https://culture.ru/events/other/test'}
    assert upload(client, auth, [other]).json()['duplicate'] == 1
    events = client.get('/api/v1/admin/events', headers=auth).json()['items']
    assert len(events) == 1 and len(events[0]['sources']) == 2
    later = item | {'date_start': (datetime.now(timezone.utc) + timedelta(days=6)).isoformat()}
    assert upload(client, auth, [later]).json()['added'] == 1
    assert first['details'][0]['id'] != upload(client, auth, [later]).json()['details'][0]['id']


@pytest.mark.parametrize('change', [dict(price_min=-1), dict(price_min=400, price_max=1), dict(age_limit=9), dict(source_url='file:///etc/passwd'), dict(date_start='2026-12-01T12:00:00'), dict(city='Москва'), dict(is_free=True, price_min=100)])
def test_rejects_invalid_records(client, auth, change):
    result = upload(client, auth, [record(**change)])
    assert result.status_code == 200
    assert result.json()['rejected'] == 1
    assert client.get('/api/v1/admin/events', headers=auth).json()['total'] == 0


def test_safe_verification_and_unknown_price(client, auth):
    item = record(source_url='http://127.0.0.1/private', price_min=None, price_max=None)
    result = upload(client, auth, [item]).json()
    event_id = result['details'][0]['id']
    assert client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth, json={'note': 'checked source'}).status_code == 422
    assert client.patch(f'/api/v1/admin/events/{event_id}', headers=auth, json={'verified_at': datetime.now(timezone.utc).isoformat(), 'status': 'published'}).status_code == 422


def test_subscription_capability_and_feedback(client):
    response = client.post('/api/v1/subscriptions', json={'city': 'Уфа'})
    assert response.status_code == 201
    value = response.json()
    assert client.delete('/api/v1/subscriptions/' + value['id']).status_code == 401
    assert client.delete('/api/v1/subscriptions/' + value['id'], headers={'Authorization': 'Bearer invalid'}).status_code == 403
    assert client.delete('/api/v1/subscriptions/' + value['id'], headers={'Authorization': 'Bearer ' + value['delete_token']}).status_code == 204
    assert client.post('/api/v1/feedback', json={'message': 'Нужно исправить адрес'}).status_code == 201
    assert client.post('/api/v1/feedback', json={'message': 'spam message', 'website': 'https://spam.test'}).status_code == 201


def test_api_errors_and_health(client):
    assert client.get('/api/v1/health').json()['status'] == 'ok'
    response = client.get('/api/v1/events', params={'price_max': -1})
    assert response.status_code == 422
    assert {'code', 'message', 'details', 'request_id'} <= response.json().keys()
    assert client.get('/openapi.json').json()['openapi'].startswith('3.1')

def test_fetched_verification_rejects_invented_session(client, auth, monkeypatch):
    item = record(source_url='https://rusdram.ru/afisha.html')
    event_id = upload(client, auth, [item]).json()['details'][0]['id']
    from apps.api.app import security
    from apps.worker.sources import SourceRegistry
    monkeypatch.setattr(security, 'fetch_source', lambda url: '<html>source fixture</html>')
    class Parser:
        def parse(self, raw, url):
            return [item | {'date_start': (datetime.now(timezone.utc) + timedelta(days=20)).isoformat()}]
    monkeypatch.setattr(SourceRegistry, '__init__', lambda self: setattr(self, 'adapters', {'rusdram': Parser()}))
    result = client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth,
                         json={'note': 'Проверка даты и фактов', 'fetch': True})
    assert result.status_code == 422
    assert 'сеанс' in result.json()['message']
    assert client.patch(f'/api/v1/admin/events/{event_id}', headers=auth, json={'status': 'published'}).status_code == 409


def test_source_changes_need_reverification(client, auth):
    item = record()
    event_id = upload(client, auth, [item]).json()['details'][0]['id']
    client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth, json={'note': 'Проверено вручную'})
    client.patch(f'/api/v1/admin/events/{event_id}', headers=auth, json={'status': 'published'})
    result = client.patch(f'/api/v1/admin/events/{event_id}', headers=auth,
                          json={'price_min': 50, 'source_url': 'https://culture.ru/events/new'})
    assert result.status_code == 200
    event = result.json()
    assert event['status'] == 'draft' and event['verified_at'] is None
    assert any(source['url'] == 'https://culture.ru/events/new' for source in event['sources'])


def test_100_row_import_and_raw_retention(client, auth):
    from apps.api.app.models import RawSourceRecord
    from sqlalchemy import func, select
    base = record()
    rows = [base | {'title': f'Тестовый спектакль fixture {number}'} for number in range(100)]
    first = upload(client, auth, rows).json()
    second = upload(client, auth, rows).json()
    assert first['added'] == 100 and first['rejected'] == 0
    assert second['duplicate'] == 100 and second['added'] == 0
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RawSourceRecord)) == 200


def test_csv_and_update_reset_publication(client, auth):
    import csv
    import io
    item = record()
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(item))
    writer.writeheader()
    writer.writerow(item)
    result = client.post('/api/v1/admin/import', headers=auth, json={'format': 'csv', 'content': stream.getvalue()})
    assert result.json()['added'] == 1
    event_id = result.json()['details'][0]['id']
    client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth, json={'note': 'Проверено вручную'})
    client.patch(f'/api/v1/admin/events/{event_id}', headers=auth, json={'status': 'published'})
    changed = item | {'description': 'Изменённое описание'}
    assert upload(client, auth, [changed]).json()['updated'] == 1
    assert client.get('/api/v1/events').json()['total'] == 0


def test_xss_is_removed_and_rate_limit_login(client, auth):
    item = record(description='<script>alert(1)</script><p>Текст события</p>')
    upload(client, auth, [item])
    event = client.get('/api/v1/admin/events', headers=auth).json()['items'][0]
    assert event['description'] == 'Текст события'
    responses = [client.post('/api/v1/admin/login', json={'password': 'bad'}).status_code for _ in range(12)]
    assert 429 in responses


def test_unknown_price_excluded_from_budget_and_timezone(client, auth):
    item = record(price_min=None, price_max=None, date_start='2099-01-02T00:30:00+05:00')
    event_id = upload(client, auth, [item]).json()['details'][0]['id']
    client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth, json={'note': 'Проверено вручную'})
    client.patch(f'/api/v1/admin/events/{event_id}', headers=auth, json={'status': 'published'})
    assert client.get('/api/v1/events', params={'price_max': 100000}).json()['total'] == 0
    assert client.get('/api/v1/events', params={'date_from': '2099-01-02', 'date_to': '2099-01-02'}).json()['total'] == 1
    assert client.get('/api/v1/events', params={'date_to': '2099-01-01'}).json()['total'] == 0


def test_verified_cancellation_preserves_permanent_card(client, auth):
    item = record()
    event_id = upload(client, auth, [item]).json()['details'][0]['id']
    client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth, json={'note': 'Подтверждено вручную'})
    client.patch(f'/api/v1/admin/events/{event_id}', headers=auth, json={'status': 'published'})
    upload(client, auth, [item | {'status': 'cancelled'}])
    assert client.get('/api/v1/events').json()['total'] == 0
    response = client.get(f'/api/v1/events/{event_id}')
    assert response.status_code == 200
    assert response.json()['status'] == 'cancelled'


def test_concurrent_import_is_idempotent(client, auth):
    from concurrent.futures import ThreadPoolExecutor
    item = record()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: upload(client, auth, [item]), range(4)))
    assert all(response.status_code == 200 for response in results), [r.text for r in results]
    assert sum(response.json()['added'] for response in results) == 1
    assert client.get('/api/v1/admin/events', headers=auth).json()['total'] == 1


def test_database_migration_clean_and_idempotent(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect
    database_url = f'sqlite:///{tmp_path / "migration.db"}'
    monkeypatch.setenv('DATABASE_URL', database_url)
    config = Config('alembic.ini')
    command.upgrade(config, 'head')
    command.upgrade(config, 'head')
    engine = create_engine(database_url)
    assert {'events', 'raw_source_records', 'audit_log', 'agent_traces', 'subscriptions'} <= set(inspect(engine).get_table_names())
    command.downgrade(config, 'base')
    assert 'events' not in inspect(engine).get_table_names()
    engine.dispose()

def test_cyrillic_case_insensitive_search(client, auth):
    item = record(title='Щелкунчик fixture')
    event_id = upload(client, auth, [item]).json()['details'][0]['id']
    client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth, json={'note': 'Подтверждено вручную'})
    client.patch(f'/api/v1/admin/events/{event_id}', headers=auth, json={'status': 'published'})
    result = client.get('/api/v1/events', params={'q': 'щелкунчик'})
    assert result.json()['total'] == 1

def test_doh_resolution_rejects_private_answers(monkeypatch):
    from apps.api.app.security import resolve_public_addresses
    monkeypatch.setenv('SOURCE_DNS_MODE', 'doh')
    import httpx
    def fake_get(*args, **kwargs):
        return httpx.Response(200, json={'Status': 0, 'Answer': [{'type': 1, 'data': '127.0.0.1'}]},
                              request=httpx.Request('GET', 'https://dns.google/resolve'))
    monkeypatch.setattr(httpx, 'get', fake_get)
    with pytest.raises(ValueError, match='непубличный'):
        resolve_public_addresses('rusdram.ru')


def test_doh_resolution_uses_only_fixed_provider_and_public_a(monkeypatch):
    from apps.api.app.security import resolve_public_addresses
    monkeypatch.setenv('SOURCE_DNS_MODE', 'doh')
    import httpx
    def fake_get(url, **kwargs):
        assert url == 'https://dns.google/resolve'
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        assert kwargs['params']['name'] == 'rusdram.ru'
        return httpx.Response(200, json={'Status': 0, 'Answer': [{'type': 5, 'data': 'example.org'},
                                {'type': 1, 'data': '92.53.96.212'}]}, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'get', fake_get)
    assert resolve_public_addresses('rusdram.ru') == ['92.53.96.212']

def test_openapi_has_typed_event_contract(client):
    document = client.get('/openapi.json').json()
    list_schema = document['paths']['/api/v1/events']['get']['responses']['200']['content']['application/json']['schema']
    assert '$ref' in list_schema
    event_schema = document['components']['schemas']['EventOutput']
    assert {'title', 'date_start', 'source_url', 'status', 'verified_at', 'sources'} <= event_schema['properties'].keys()


def test_stale_verification_hidden_from_listing_but_permanent_card_available(client, auth):
    from apps.api.app.models import Event
    item = record()
    event_id = upload(client, auth, [item]).json()['details'][0]['id']
    client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth, json={'note': 'Подтверждено вручную'})
    client.patch(f'/api/v1/admin/events/{event_id}', headers=auth, json={'status': 'published'})
    with client.app.state.session_factory() as session:
        session.get(Event, event_id).verified_at = datetime.now(timezone.utc) - timedelta(days=3)
        session.commit()
    assert client.get('/api/v1/events').json()['total'] == 0
    detail = client.get(f'/api/v1/events/{event_id}')
    assert detail.status_code == 200 and detail.json()['verification_expired'] is True


def test_malformed_content_length_rejected(client):
    response = client.post('/api/v1/feedback', content='{}', headers={'Content-Length': 'not-a-number', 'Content-Type': 'application/json'})
    assert response.status_code == 400

def test_trace_redaction_preserves_numeric_json_and_removes_contacts():
    from apps.api.app.security import sanitize_trace
    value = {'latency_ms': 12345.6789012345, 'tokens': 123456789012,
             'query': 'Телефон +7 (999) 123-45-67; test@example.com',
             'calls': [{'id': 'abc-def', 'cost': None}], 'credential': 'Bearer token_private'}
    output = sanitize_trace(value)
    assert output['latency_ms'] == value['latency_ms'] and output['tokens'] == value['tokens']
    assert 'test@example.com' not in output['query'] and '999' not in output['query']
    assert output['calls'] == value['calls'] and 'token_private' not in output['credential']

def test_subscription_idempotency_capability_replays_token_without_storing_it(client):
    from apps.api.app.models import Subscription
    headers = {'Idempotency-Key': 'test_random_capability_very_long_123456'}
    first = client.post('/api/v1/subscriptions', json={'city': 'Уфа'}, headers=headers)
    second = client.post('/api/v1/subscriptions', json={'city': 'Уфа'}, headers=headers)
    assert first.status_code == second.status_code == 201
    assert first.json()['id'] == second.json()['id']
    assert first.json()['delete_token'] == second.json()['delete_token']
    assert client.post('/api/v1/subscriptions', json={'city': 'Салават'}, headers=headers).status_code == 409
    with client.app.state.session_factory() as session:
        row = session.get(Subscription, first.json()['id'])
        assert row.token_hash != first.json()['delete_token']
        assert row.idem_hash != headers['Idempotency-Key']
    response = client.delete('/api/v1/subscriptions/' + first.json()['id'],
                             headers={'Authorization': 'Bearer ' + second.json()['delete_token']})
    assert response.status_code == 204


def test_concurrent_subscription_idempotency(client):
    from concurrent.futures import ThreadPoolExecutor
    from apps.api.app.models import Subscription
    from sqlalchemy import select, func
    def create(_):
        return client.post('/api/v1/subscriptions', json={'city': 'Уфа'},
                           headers={'Idempotency-Key': 'another_random_capability_1234567890'})
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(create, range(4)))
    assert all(response.status_code == 201 for response in responses)
    assert len({response.json()['id'] for response in responses}) == 1
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Subscription)) == 1

def test_llm_reservations_are_durable_atomic_and_fail_closed(client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    hook = getattr(client.app.state, 'reserve_llm_usage', None)
    assert callable(hook)
    identity = 'a' * 64
    assert hook(identity, 0.1, 100, 'RUB') is False
    monkeypatch.setenv('LLM_DAILY_MAX_COST', '1.0')
    monkeypatch.setenv('LLM_DAILY_REQUEST_LIMIT', '100')
    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(lambda _: hook(identity, 0.3, 100, 'RUB'), range(8)))
    assert sum(reservations) == 3
    assert hook(identity, 0.3, 100, 'RUB') is False
    assert hook('b' * 64, -1, 100, 'RUB') is False
    assert hook(identity, 0.01, 100, 'USD') is False
    from apps.api.app.models import LLMQuota
    with client.app.state.session_factory() as session:
        quota = session.get(LLMQuota, (identity, datetime.now(ZoneInfo('Asia/Yekaterinburg')).date().isoformat()))
        assert quota is not None and quota.requests == 3
        assert quota.reserved_cost <= 1


def test_retention_maintenance_is_authorized_and_removes_expired_rows(client, auth):
    from apps.api.app.models import AgentTrace, Feedback, RawSourceRecord, Subscription
    from sqlalchemy import func, select
    old = datetime.now(timezone.utc) - timedelta(days=200)
    with client.app.state.session_factory() as session:
        session.add(AgentTrace(payload={'query': 'expired'}, created_at=old))
        session.add(AgentTrace(payload={'query': 'current'}))
        session.add(Feedback(message='expired contact', created_at=old))
        session.add(Subscription(city='Уфа', token_hash='a' * 64, created_at=old))
        session.add(RawSourceRecord(payload='expired raw', source_hash='b' * 64, outcome='rejected', created_at=old))
        session.commit()
    assert client.post('/api/v1/admin/maintenance').status_code == 401
    result = client.post('/api/v1/admin/maintenance', headers=auth)
    assert result.status_code == 200
    assert result.json()['deleted']['agent_traces'] == 1
    assert result.json()['deleted']['feedback'] == 1
    assert result.json()['deleted']['subscriptions'] == 1
    assert result.json()['deleted']['raw_source_records'] == 1
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentTrace)) == 1

def test_trace_keeps_iso_dates_uuid_and_source_urls():
    from apps.api.app.security import sanitize_trace
    value = {'date_from': '2099-01-03', 'date_to': '2099-01-04',
             'id': '12345678-1234-4123-8123-123456789012',
             'query': 'С 2099-01-03 по 2099-01-04, телефон +7 (999) 123-45-67',
             'source_ids': ['https://culture.ru/events/123456789012/test']}
    result = sanitize_trace(value)
    assert result['date_from'] == value['date_from'] and result['date_to'] == value['date_to']
    assert result['id'] == value['id'] and result['source_ids'] == value['source_ids']
    assert '2099-01-03' in result['query'] and '2099-01-04' in result['query']
    assert '999' not in result['query']

def test_unknown_route_uses_same_error_envelope(client):
    response = client.get('/api/v1/this-route-does-not-exist')
    assert response.status_code == 404
    assert response.json()['code'] == 'not_found'
    assert response.json()['request_id'] == response.headers['X-Request-ID']

def test_source_import_can_revert_to_earlier_payload(client, auth, monkeypatch):
    from apps.api.app import services
    frozen = datetime.now(timezone.utc)
    monkeypatch.setattr(services, 'now', lambda: frozen)
    first = record(description='Первая редакция источника', price_min=100)
    second = first | {'description': 'Вторая редакция источника', 'price_min': 150}
    event_id = upload(client, auth, [first]).json()['details'][0]['id']
    assert upload(client, auth, [second]).json()['updated'] == 1
    third = upload(client, auth, [first]).json()
    assert third['updated'] == 1 and third['duplicate'] == 0
    event = client.get('/api/v1/admin/events', headers=auth).json()['items'][0]
    assert event['id'] == event_id and event['description'] == first['description'] and event['price_min'] == 100
    assert upload(client, auth, [first]).json()['duplicate'] == 1


def test_unchanged_secondary_source_never_overwrites_canonical(client, auth):
    primary = record(description='Подтверждаемый основной текст')
    secondary = primary | {'source_url': 'https://culture.ru/events/another', 'description': 'Другой текст вторичного источника'}
    upload(client, auth, [primary])
    assert upload(client, auth, [secondary]).json()['duplicate'] == 1
    assert upload(client, auth, [secondary]).json()['duplicate'] == 1
    event = client.get('/api/v1/admin/events', headers=auth).json()['items'][0]
    assert event['description'] == primary['description'] and event['source_url'] == primary['source_url']
    assert len(event['sources']) == 2


def test_repeated_import_cancellation_stays_cancelled_and_revert_needs_review(client, auth):
    original = record()
    event_id = upload(client, auth, [original]).json()['details'][0]['id']
    client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth, json={'note': 'Проверено вручную'})
    client.patch(f'/api/v1/admin/events/{event_id}', headers=auth, json={'status': 'published'})
    cancellation = original | {'status': 'cancelled'}
    assert upload(client, auth, [cancellation]).json()['updated'] == 1
    assert upload(client, auth, [cancellation]).json()['duplicate'] == 1
    assert client.get(f'/api/v1/events/{event_id}').json()['status'] == 'cancelled'
    assert upload(client, auth, [original]).json()['updated'] == 1
    assert client.get('/api/v1/events').json()['total'] == 0
    assert client.patch(f'/api/v1/admin/events/{event_id}', headers=auth, json={'status': 'published'}).status_code == 409


@pytest.mark.parametrize('fabrication', [
    {'duration_min': 120}, {'description': 'Выдуманный бесплатный ужин с артистами'},
    {'date_end': '2099-01-03T22:00:00+05:00'}, {'category': 'концерт'},
    {'tags': ['несуществующая премьера']}, {'language': 'башкирский'}, {'lat': 54.1, 'lon': 55.1},
])
def test_fetch_verification_rejects_unproven_canonical_facts(client, auth, monkeypatch, fabrication):
    from apps.api.app import security
    from apps.worker.sources import SourceRegistry
    canonical = record(source_url='https://rusdram.ru/afisha.html', date_start='2099-01-03T19:00:00+05:00')
    imported = canonical | fabrication
    event_id = upload(client, auth, [imported]).json()['details'][0]['id']
    monkeypatch.setattr(security, 'fetch_source', lambda url: '<html>test-only source snapshot</html>')
    class Parser:
        def parse(self, raw, url):
            return [canonical]
    monkeypatch.setattr(SourceRegistry, '__init__', lambda self: setattr(self, 'adapters', {'rusdram': Parser()}))
    verified = client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth,
                           json={'note': 'Автоматическая сверка источника', 'fetch': True})
    assert verified.status_code == 422
    assert client.patch(f'/api/v1/admin/events/{event_id}', headers=auth, json={'status': 'published'}).status_code == 409
    manual = client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth,
                         json={'note': 'Дополнительные сведения отдельно проверены модератором', 'fetch': False})
    assert manual.status_code == 200 and manual.json()['confidence'] == .85


def test_fetch_verification_accepts_complete_canonical_adapter_record(client, auth, monkeypatch):
    from apps.api.app import security
    from apps.worker.sources import SourceRegistry
    canonical = record(source_url='https://rusdram.ru/afisha.html', date_start='2099-01-03T19:00:00+05:00',
                       date_end='2099-01-03T21:00:00+05:00', duration_min=120, tags=['драма'], language='русский')
    event_id = upload(client, auth, [canonical]).json()['details'][0]['id']
    monkeypatch.setattr(security, 'fetch_source', lambda url: '<html>test-only source snapshot</html>')
    class Parser:
        def parse(self, raw, url):
            return [canonical]
    monkeypatch.setattr(SourceRegistry, '__init__', lambda self: setattr(self, 'adapters', {'rusdram': Parser()}))
    response = client.post(f'/api/v1/admin/events/{event_id}/verify', headers=auth,
                           json={'note': 'Все факты подтверждены адаптером', 'fetch': True})
    assert response.status_code == 200 and response.json()['confidence'] == .95
