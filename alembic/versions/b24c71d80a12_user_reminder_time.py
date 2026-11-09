import sqlalchemy as sa
from alembic import op

revision = "b24c71d80a12"
down_revision = "a14b72c90e31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("reminder_time", sa.Time(), server_default="08:00:00", nullable=False))
    op.add_column("users", sa.Column("last_reminder_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_reminder_date")
    op.drop_column("users", "reminder_time")
