from __future__ import annotations

from datetime import date as Date
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .group import Group


class Deadline(Base):
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    group: Mapped[Group] = relationship(back_populates="deadlines")
    date: Mapped[Date]
    subject: Mapped[str] = mapped_column(String)
    deadline: Mapped[str] = mapped_column(Text)
