import sqlalchemy as sa
from alembic import op

revision = "d46e93fa2c34"
down_revision = "c35d82e91b23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessiontasks",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("group_id", sa.BigInteger(), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("semester", sa.String(6), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
    )
    op.create_index("ix_sessiontasks_group_id", "sessiontasks", ["group_id"])
    op.create_table(
        "sessioncompletions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("task_id", sa.BigInteger(), sa.ForeignKey("sessiontasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("task_id", "telegram_id", name="uq_session_completion"),
    )


def downgrade() -> None:
    op.drop_table("sessioncompletions")
    op.drop_index("ix_sessiontasks_group_id", table_name="sessiontasks")
    op.drop_table("sessiontasks")
