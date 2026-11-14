"""Record source permission or license evidence before displaying a poster."""
from alembic import op
import sqlalchemy as sa

revision = 'b412f0257ce8'
down_revision = 'a031b11c90e2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('events', sa.Column('poster_rights', sa.Text()))


def downgrade():
    op.drop_column('events', 'poster_rights')
