"""Atomic durable per-identity daily LLM reservations.

Revision ID: 74b8f221cd50
Revises: 32a1d810be21
"""
from alembic import op
import sqlalchemy as sa

revision = '74b8f221cd50'
down_revision = '32a1d810be21'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('llm_quotas',
        sa.Column('identity_hash', sa.String(64), nullable=False),
        sa.Column('day', sa.String(10), nullable=False),
        sa.Column('currency', sa.String(8), nullable=False),
        sa.Column('reserved_cost', sa.Float(), nullable=False),
        sa.Column('requests', sa.Integer(), nullable=False),
        sa.Column('reserved_tokens', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('identity_hash', 'day'))


def downgrade():
    op.drop_table('llm_quotas')
