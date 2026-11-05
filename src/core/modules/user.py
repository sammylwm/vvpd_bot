from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .group import Group


class User(Base):
    reminders_enabled: Mapped[bool] = mapped_column(default=True, server_default=true())
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    group: Mapped[Group] = relationship(back_populates="users")
