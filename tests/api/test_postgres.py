"""Real PostgreSQL integration, always in a fresh disposable schema.

Run with POSTGRES_TEST_URL. The application catalog and public schema are untouched.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text, MetaData, Table
from sqlalchemy.engine import make_url

from apps.api.app.models import Event, ProviderIdentity
from tests.api.test_api import record, upload
from tests.api.test_finish import publish, signed


@pytest.fixture
def pg_client(monkeypatch):
    database_url = os.getenv('POSTGRES_TEST_URL')
    if not database_url:
        pytest.skip('Set POSTGRES_TEST_URL for isolated live PostgreSQL verification')
    schema = 'kiber102_test_' + uuid4().hex
    admin_engine = create_engine(database_url, pool_pre_ping=True)
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(database_url)
    query = dict(url.query)
    query['options'] = f'-csearch_path={schema}'
    isolated_url = url.set(query=query).render_as_string(hide_password=False)
    monkeypatch.setenv('DATABASE_URL', isolated_url)
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-private-password')
    monkeypatch.setenv('ADMIN_SESSION_SECRET', 'isolated-session-secret-that-is-long-enough')
    monkeypatch.setenv('SOURCE_ALLOWLIST', 'culture.ru,www.culture.ru,rusdram.ru')
    config = Config('alembic.ini')
    try:
        command.upgrade(config, 'head')
        command.upgrade(config, 'head')
        from apps.api.app.main import create_app
        with TestClient(create_app(isolated_url)) as client:
            yield client
    finally:
        # The random schema is the only deletion target, never the database/public.
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def pg_auth(client):
    return {'Authorization': 'Bearer ' + client.post('/api/v1/admin/login', json={'password': 'test-private-password'}).json()['token']}


def test_postgres_ticket_end_backfill_preserves_historical_provenance(pg_client):
    from tests.api.test_ticket_backfill import legacy_ticket, assert_backfilled

    client = pg_client
    event_id, original, provenance = legacy_ticket(client, pg_auth(client))
    config = Config('alembic.ini')
    command.downgrade(config, 'c15b9e042d73')
    command.upgrade(config, 'head')
    assert_backfilled(client, event_id, original, provenance)
    command.upgrade(config, 'head')
    assert_backfilled(client, event_id, original, provenance)


def test_postgres_migrations_import_concurrency_withdrawal_and_stable_identity(pg_client):
    client = pg_client
    auth = pg_auth(client)
    item = record()
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: upload(client, auth, [item]), range(6)))
    assert all(response.status_code == 200 for response in responses), [r.status_code for r in responses]
    assert sum(response.json()['added'] for response in responses) == 1
    event_id = publish(client, auth, item)
    cancelled = item | {'source_url': 'https://rusdram.ru/cancellation', 'status': 'cancelled'}
    assert upload(client, auth, [cancelled]).json()['requires_review'] == 1
    assert client.get('/api/v1/events').json()['total'] == 0
    assert client.get('/api/v1/events/' + event_id).json()['status'] == 'cancelled'
    provider_item = record(provider_id='pg-provider-session', title='Другой сеанс PostgreSQL')
    with ThreadPoolExecutor(max_workers=6) as pool:
        provider_results = list(pool.map(lambda _: upload(client, auth, [provider_item]), range(6)))
    assert all(response.status_code == 200 for response in provider_results)
    assert sum(response.json()['added'] for response in provider_results) == 1
    identifier = provider_results[0].json()['details'][0]['id']
    renamed = provider_item | {'title': 'Переименованный сеанс PostgreSQL', 'source_url': 'https://culture.ru/new-url'}
    updated = upload(client, auth, [renamed])
    assert updated.status_code == 200 and updated.json()['details'][0]['id'] == identifier
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Event)) == 2
        assert session.scalar(select(func.count()).select_from(ProviderIdentity)) == 1
        assert {'agent_dialogs', 'agent_actions', 'notification_outbox'} <= set(inspect(session.bind).get_table_names())


def test_postgres_quotas_actions_and_concurrent_idempotency(pg_client, monkeypatch):
    client = pg_client
    monkeypatch.setenv('LLM_DAILY_MAX_COST', '20')
    monkeypatch.setenv('LLM_DAILY_REQUEST_LIMIT', '100')
    identity = 'e' * 64
    dialog = client.app.state.ensure_agent_dialog(identity, None)
    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = list(pool.map(lambda _: client.app.state.reserve_llm_dialog(identity, dialog, .8, 100, 'RUB'), range(8)))
    assert sum(reservations) == 3
    assert client.app.state.ensure_agent_dialog(identity, None) == dialog
    key = {'Idempotency-Key': 'postgres-concurrent-action-capability-1234'}
    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(pool.map(lambda _: client.post('/api/v1/feedback', headers=key, json={'message': 'Проверить адрес на PostgreSQL'}), range(5)))
    assert all(response.status_code == 201 for response in responses)
    assert len({response.json()['id'] for response in responses}) == 1


def test_postgres_upgrade_preserves_preexisting_published_data(pg_client):
    from apps.api.app.models import now
    from apps.api.app.schemas import EventInput, normalize
    from apps.api.app.services import dedup_key, digest
    client = pg_client
    config = Config('alembic.ini')
    command.downgrade(config, '74b8f221cd50')
    item = EventInput.model_validate(record(title='Существующая карточка до миграции'))
    values = item.model_dump(exclude={'region', 'provider_id', 'poster_url', 'poster_rights'})
    identifier = str(uuid4())
    values.update(id=identifier, normalized_title=normalize(item.title), dedup_key=dedup_key(item),
                  source_hash=digest('old-real-record'), verified_at=now(), confidence=.95,
                  status='published', created_at=now(), updated_at=now())
    with client.app.state.engine.begin() as connection:
        events_table = Table('events', MetaData(), autoload_with=connection)
        connection.execute(events_table.insert().values(**values))
    command.upgrade(config, 'head')
    command.upgrade(config, 'head')
    response = client.get('/api/v1/events/' + identifier)
    assert response.status_code == 200
    assert response.json()['title'] == item.title and response.json()['status'] == 'published'
    assert response.json()['poster_url'] is None and response.json()['poster_rights'] is None
    assert client.get('/api/v1/events').json()['total'] == 1


def test_postgres_outbox_concurrent_claims_send_each_intent_once(pg_client, monkeypatch):
    client = pg_client
    auth = pg_auth(client)
    monkeypatch.setenv('TRUSTED_PROXY_SECRET', 'a-test-proxy-secret-with-32-characters')
    monkeypatch.setenv('NOTIFICATIONS_ENABLED', '1')
    response = client.post('/api/v1/subscriptions', headers=signed('telegram:123456789'), json={'city': 'Уфа'})
    assert response.status_code == 201
    for index in range(4):
        publish(client, auth, record(title=f'Новый сеанс PostgreSQL {index}'))
    assert client.post('/api/v1/admin/notifications/enqueue', headers=auth).json()['queued'] == 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda _: client.post('/api/v1/admin/notifications/claim', headers=auth, json={'limit': 2}), range(4)))
    assert all(row.status_code == 200 for row in claimed)
    ids = [item['id'] for response in claimed for item in response.json()['items']]
    assert len(ids) == len(set(ids)) == 4
