"""Backfill UTC end-time comparison facts for previously observed tickets."""
from alembic import op

revision = 'd7f3198a624e'
down_revision = 'c15b9e042d73'
branch_labels = None
depends_on = None


def upgrade():
    from apps.api.app.services import backfill_ticket_end_times

    backfill_ticket_end_times(op.get_bind())


def downgrade():
    # The derived JSON key is backward compatible. Preserve provenance and
    # leave it available to older readers, which ignore unknown fact keys.
    pass
