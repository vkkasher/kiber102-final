"""Optional source-backed poster URL; existing events remain unchanged.

Revision ID: a031b11c90e2
Revises: 9fa4102d6ac1
"""
from alembic import op
import sqlalchemy as sa

revision = 'a031b11c90e2'
down_revision = '9fa4102d6ac1'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('events', sa.Column('poster_url', sa.Text()))


def downgrade():
    op.drop_column('events', 'poster_url')
