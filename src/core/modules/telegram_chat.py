from sqlalchemy import BigInteger, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class TelegramChat(Base):
    topic_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
