"""Store only a unique hash of the subscription retry capability.

Revision ID: 32a1d810be21
Revises: 698ea745edf5
"""
from alembic import op
import sqlalchemy as sa

revision = '32a1d810be21'
down_revision = '698ea745edf5'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('subscriptions', sa.Column('idem_hash', sa.String(64), nullable=True))
    op.create_index('ix_subscriptions_idem_hash', 'subscriptions', ['idem_hash'], unique=True)


def downgrade():
    op.drop_index('ix_subscriptions_idem_hash', table_name='subscriptions')
    op.drop_column('subscriptions', 'idem_hash')
