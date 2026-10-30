from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .deadline import Deadline
    from .user import User


class Group(Base):
    name: Mapped[str] = mapped_column(String(64), unique=True)
    users: Mapped[list[User]] = relationship(back_populates="group")
    deadlines: Mapped[list[Deadline]] = relationship(back_populates="group")
