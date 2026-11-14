"""Stable provider sessions, durable dialog/action budgets and delivery outbox.

Revision ID: 9fa4102d6ac1
Revises: 74b8f221cd50
"""
from alembic import op
import sqlalchemy as sa

revision = '9fa4102d6ac1'
down_revision = '74b8f221cd50'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('provider_identities',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('source_scope', sa.String(300), nullable=False),
        sa.Column('provider_id', sa.String(300), nullable=False),
        sa.Column('event_id', sa.String(36), sa.ForeignKey('events.id'), nullable=False),
        sa.UniqueConstraint('source_scope', 'provider_id'))
    op.create_index('ix_provider_identities_event_id', 'provider_identities', ['event_id'])
    op.add_column('subscriptions', sa.Column('transport', sa.String(20)))
    op.add_column('subscriptions', sa.Column('transport_target', sa.String(160)))
    op.add_column('feedback', sa.Column('idem_hash', sa.String(64)))
    op.create_index('ix_feedback_idem_hash', 'feedback', ['idem_hash'], unique=True)
    op.add_column('feedback', sa.Column('status', sa.String(20), nullable=False, server_default='pending'))
    op.add_column('feedback', sa.Column('review_note', sa.Text()))
    op.create_table('agent_dialogs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('dialog_key', sa.String(100), nullable=False, unique=True),
        sa.Column('identity_hash', sa.String(64), nullable=False),
        sa.Column('currency', sa.String(8), nullable=False),
        sa.Column('reserved_cost', sa.Float(), nullable=False),
        sa.Column('reserved_tokens', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_agent_dialogs_identity_hash', 'agent_dialogs', ['identity_hash'])
    op.create_table('agent_actions',
        sa.Column('nonce', sa.String(64), primary_key=True),
        sa.Column('identity_hash', sa.String(64), nullable=False),
        sa.Column('tool', sa.String(40), nullable=False),
        sa.Column('arguments', sa.JSON(), nullable=False),
        sa.Column('result', sa.JSON()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_agent_actions_identity_hash', 'agent_actions', ['identity_hash'])
    op.create_table('notification_outbox',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('subscription_id', sa.String(36), sa.ForeignKey('subscriptions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('event_id', sa.String(36), sa.ForeignKey('events.id'), nullable=False),
        sa.Column('version', sa.String(64), nullable=False),
        sa.Column('lease_token', sa.String(64)),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('claimed_at', sa.DateTime(timezone=True)),
        sa.Column('sent_at', sa.DateTime(timezone=True)),
        sa.Column('last_error', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('subscription_id', 'event_id', 'version'))
    op.create_index('ix_notification_outbox_subscription_id', 'notification_outbox', ['subscription_id'])
    op.create_index('ix_notification_outbox_status', 'notification_outbox', ['status'])


def downgrade():
    op.drop_table('notification_outbox')
    op.drop_table('agent_actions')
    op.drop_table('agent_dialogs')
    op.drop_index('ix_feedback_idem_hash', table_name='feedback')
    for name in ['review_note', 'status', 'idem_hash']:
        op.drop_column('feedback', name)
    op.drop_column('subscriptions', 'transport_target')
    op.drop_column('subscriptions', 'transport')
    op.drop_table('provider_identities')
