"""Store Telegram attachment identifiers on deadlines."""

import sqlalchemy as sa

from alembic import op

revision = "a14b72c90e31"
down_revision = "8c2e91a4d607"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deadlines",
        sa.Column("attachments", sa.JSON(), server_default="[]", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("deadlines", "attachments")
