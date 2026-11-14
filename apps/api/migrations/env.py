"""Migration environment: DATABASE_URL is the only production credential source."""
import os
from alembic import context
from sqlalchemy import create_engine, pool
from apps.api.app.models import Base

config = context.config
url = os.getenv('DATABASE_URL', config.get_main_option('sqlalchemy.url'))
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={'paramstyle': 'named'})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
