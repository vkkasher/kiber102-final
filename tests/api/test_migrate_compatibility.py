"""Reconcile only the exact SQLite schema already created by the local API."""
import os
from pathlib import Path
import subprocess
import sys

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
import pytest

from apps.api.app.models import Base
from scripts import migrate


@pytest.fixture
def native_database(tmp_path, monkeypatch):
    url = 'sqlite:///'+str(tmp_path/'native.db')
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setattr(migrate, 'load_dotenv', lambda *a, **k: None)
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    command.stamp(Config('alembic.ini'), 'b412f0257ce8')
    yield engine
    engine.dispose()


def revision(engine):
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def test_native_existing_ticket_table_is_reconciled_and_backfilled_without_data_loss(native_database):
    from apps.api.app.models import EventTicketEvidence

    engine = native_database
    # SQLite fixtures do not enforce FK links; only the independently stored
    # ticket row is necessary to demonstrate that reconciliation preserves it.
    with engine.begin() as connection:
        connection.execute(EventTicketEvidence.__table__.insert().values(
            event_id='existing-session', source_url='https://ufa.kassy.ru/events/teatr/2-1/',
            source_hash='a'*64, availability='available',
            facts={'date_end': '2026-11-18T21:00:00+05:00'}))
    migrate.main()
    assert revision(engine) == 'd7f3198a624e'
    with engine.connect() as connection:
        row = connection.execute(EventTicketEvidence.__table__.select()).mappings().one()
        assert row['source_hash'] == 'a'*64 and row['availability'] == 'available'
        assert row['facts'] == {'date_end': '2026-11-18T21:00:00+05:00',
                                'date_end_utc': '2026-11-18T16:00:00.000000+00:00'}
    migrate.main()
    assert revision(engine) == 'd7f3198a624e'


def test_direct_migration_cli_resolves_repository_imports_without_pythonpath(native_database):
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    environment['DATABASE_URL'] = str(native_database.url)
    environment['PYTHON_DOTENV_DISABLED'] = '1'
    result = subprocess.run([sys.executable, 'scripts/migrate.py'],
                            cwd=Path(__file__).resolve().parents[2], env=environment,
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert revision(native_database) == 'd7f3198a624e'


@pytest.mark.parametrize('mutation', [
    'ALTER TABLE event_ticket_evidence ADD COLUMN unexpected TEXT',
    'DROP INDEX ix_event_ticket_evidence_availability',
    'ALTER TABLE events ADD COLUMN unexpected TEXT',
])
def test_native_schema_mismatch_fails_closed_without_changing_revision(native_database, mutation):
    with native_database.begin() as connection:
        connection.execute(text(mutation))
    with pytest.raises(RuntimeError, match='review before migration'):
        migrate.main()
    assert revision(native_database) == 'b412f0257ce8'


def test_native_missing_ticket_primary_key_fails_closed(native_database):
    with native_database.begin() as connection:
        connection.execute(text('DROP TABLE event_ticket_evidence'))
        connection.execute(text('''CREATE TABLE event_ticket_evidence (
            event_id VARCHAR(36) NOT NULL REFERENCES events(id), source_url TEXT NOT NULL,
            source_hash VARCHAR(64) NOT NULL, availability VARCHAR(20), image_url TEXT,
            facts JSON NOT NULL, checked_at DATETIME NOT NULL)'''))
        connection.execute(text('CREATE INDEX ix_event_ticket_evidence_availability ON event_ticket_evidence (availability)'))
    with pytest.raises(RuntimeError, match='review before migration'):
        migrate.main()
    assert revision(native_database) == 'b412f0257ce8'
