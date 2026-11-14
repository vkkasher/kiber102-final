"""Historical ticket facts upgrade without fetching sources or changing provenance."""
from datetime import datetime, timezone

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
import pytest

from apps.api.app.models import AuditLog, EventTicketEvidence, RawSourceRecord
from tests.api.test_finish import auth as auth, client as client, publish
from tests.api.test_api import record
from tests.api.test_ticket_evidence import START, page, snapshot


def legacy_ticket(client, auth):
    event_id = publish(client, auth, record(title='Палата бизнес-класса', venue='Русский драматический театр РБ',
                       date_start=START.isoformat()))
    body = snapshot(page())
    client.post('/api/v1/admin/ticket-evidence', headers=auth, json=body)
    with client.app.state.session_factory() as session:
        evidence = session.get(EventTicketEvidence, event_id)
        facts = dict(evidence.facts)
        facts.pop('date_end_utc', None)
        evidence.facts = facts
        session.commit()
        provenance = (evidence.source_hash, evidence.checked_at, evidence.availability,
                      session.scalar(select(func.count()).select_from(AuditLog)),
                      session.scalar(select(func.count()).select_from(RawSourceRecord)))
    return event_id, facts, provenance


def assert_backfilled(client, event_id, original, provenance):
    expected = datetime.fromisoformat(original['date_end']).astimezone(timezone.utc).isoformat(timespec='microseconds')
    with client.app.state.session_factory() as session:
        evidence = session.get(EventTicketEvidence, event_id)
        assert evidence.facts == original | {'date_end_utc': expected}
        assert provenance == (evidence.source_hash, evidence.checked_at, evidence.availability,
                              session.scalar(select(func.count()).select_from(AuditLog)),
                              session.scalar(select(func.count()).select_from(RawSourceRecord)))


def test_existing_sqlite_startup_backfills_ticket_end_without_replaying_sources(client, auth):
    from apps.api.app.main import create_app

    event_id, original, provenance = legacy_ticket(client, auth)
    with TestClient(create_app(str(client.app.state.engine.url))):
        assert_backfilled(client, event_id, original, provenance)
    with TestClient(create_app(str(client.app.state.engine.url))):
        assert_backfilled(client, event_id, original, provenance)


def test_alembic_backfills_existing_ticket_facts_and_is_idempotent(client, auth, monkeypatch):
    event_id, original, provenance = legacy_ticket(client, auth)
    monkeypatch.setenv('DATABASE_URL', str(client.app.state.engine.url))
    config = Config('alembic.ini')
    command.stamp(config, 'c15b9e042d73')
    command.upgrade(config, 'head')
    assert_backfilled(client, event_id, original, provenance)
    command.upgrade(config, 'head')
    assert_backfilled(client, event_id, original, provenance)


def test_fresh_migrations_reach_ticket_backfill_revision(tmp_path, monkeypatch):
    from alembic.runtime.migration import MigrationContext

    url = 'sqlite:///'+str(tmp_path / 'fresh.db')
    monkeypatch.setenv('DATABASE_URL', url)
    config = Config('alembic.ini')
    command.upgrade(config, 'head')
    engine = create_engine(url)
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == 'd7f3198a624e'
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(EventTicketEvidence)) == 0
    engine.dispose()


@pytest.mark.parametrize('end', [None, 'not-a-date', '2026-11-19T19:00:00', '9999-12-31T23:59:59-12:00'])
def test_backfill_preserves_unknown_invalid_and_unrepresentable_historical_dates(client, auth, end):
    from apps.api.app.services import backfill_ticket_end_times

    event_id, original, _ = legacy_ticket(client, auth)
    original['date_end'] = end
    with client.app.state.session_factory() as session:
        session.get(EventTicketEvidence, event_id).facts = original
        session.commit()
    with client.app.state.engine.begin() as connection:
        assert backfill_ticket_end_times(connection) == 0
    with client.app.state.session_factory() as session:
        assert session.get(EventTicketEvidence, event_id).facts == original
