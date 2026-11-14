"""Keep session-matched ticket observations separate from moderated catalogue facts."""
from alembic import op
import sqlalchemy as sa

revision = 'c15b9e042d73'
down_revision = 'b412f0257ce8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('event_ticket_evidence',
                    sa.Column('event_id',sa.String(36),sa.ForeignKey('events.id'),primary_key=True),
                    sa.Column('source_url',sa.Text(),nullable=False),
                    sa.Column('source_hash',sa.String(64),nullable=False),
                    sa.Column('availability',sa.String(20)),
                    sa.Column('image_url',sa.Text()),
                    sa.Column('facts',sa.JSON(),nullable=False),
                    sa.Column('checked_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_event_ticket_evidence_availability','event_ticket_evidence',['availability'])


def downgrade():
    op.drop_table('event_ticket_evidence')
