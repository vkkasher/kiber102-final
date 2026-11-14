"""Upgrade configured database, preserving the known unversioned prototype schema."""
import os
from pathlib import Path
import sys

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect


def reconcile_native_ticket_schema(engine, config):
    """Recognize only the exact already-created SQLite c15 schema, not drift."""
    if engine.dialect.name != 'sqlite':
        return
    from apps.api.app.models import Base

    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={'compare_type': True})
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if context.get_current_revision() != 'b412f0257ce8' or 'event_ticket_evidence' not in tables:
            return
        differences = compare_metadata(context, Base.metadata)
        # Alembic does not detect every primary-key alteration. Check those
        # explicitly across the schema as well as columns, FKs and indexes.
        expected_tables = set(Base.metadata.tables)
        exact_primary_keys = all(
            inspector.get_pk_constraint(name)['constrained_columns']
            == [column.name for column in Base.metadata.tables[name].primary_key.columns]
            for name in expected_tables if name in tables)
        if differences or tables != expected_tables | {'alembic_version'} or not exact_primary_keys:
            raise RuntimeError('Native SQLite schema differs from the verified c15 schema; review before migration.')
    command.stamp(config, 'c15b9e042d73')


def main():
    root = Path(__file__).resolve().parents[1]
    # Direct execution starts with scripts/ on sys.path, before Alembic has
    # loaded its environment. Resolve repository imports for reconciliation.
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    load_dotenv(root / '.env', encoding='utf-8-sig')
    config = Config(str(root / 'alembic.ini'))
    engine = create_engine(os.environ.get('DATABASE_URL', 'sqlite:///./kiber102.db'))
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    baseline_tables = {'agent_traces', 'audit_log', 'cost_usage', 'events', 'feedback',
                       'sources', 'subscriptions', 'event_sources', 'raw_source_records'}
    if 'events' in tables and 'alembic_version' not in tables:
        if tables != baseline_tables:
            raise RuntimeError('Unversioned database has an unexpected schema; review before migration.')
        columns = {c['name'] for c in inspector.get_columns('subscriptions')}
        if columns != {'id', 'token_hash', 'city', 'category', 'period', 'created_at'}:
            raise RuntimeError('Unversioned subscriptions schema differs from the verified baseline.')
        command.stamp(config, '698ea745edf5')
    reconcile_native_ticket_schema(engine, config)
    engine.dispose()
    command.upgrade(config, 'head')
    print('Database migrations are current.')


if __name__ == '__main__':
    main()
