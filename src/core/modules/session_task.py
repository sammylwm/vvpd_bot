from datetime import date

from sqlalchemy import BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class SessionTask(Base):
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    semester: Mapped[str] = mapped_column(String(6))
    subject: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(nullable=True)


class SessionCompletion(Base):
    __table_args__ = (UniqueConstraint("task_id", "telegram_id", name="uq_session_completion"),)
    task_id: Mapped[int] = mapped_column(ForeignKey("sessiontasks.id", ondelete="CASCADE"))
    telegram_id: Mapped[int] = mapped_column(BigInteger)
