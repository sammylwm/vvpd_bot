"""Add persistent user reminder preference."""

from alembic import op
import sqlalchemy as sa

revision = "8c2e91a4d607"
down_revision = "191fb005d40c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("reminders_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "reminders_enabled")
