import sqlalchemy as sa
from alembic import op

revision = "c35d82e91b23"
down_revision = "b24c71d80a12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegramchats",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("topic_id", sa.BigInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("telegramchats")
