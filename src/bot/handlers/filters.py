from aiogram.filters import Filter
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.identity import account_id
from core.modules.user import User


class UnregisteredUser(Filter):
    async def __call__(
        self,
        message: Message,
        session: AsyncSession,
    ) -> bool:
        if message.from_user is None:
            return False

        user_exists = await session.scalar(
            select(
                select(User)
                .where(User.telegram_id == account_id(message, message.from_user.id))
                .exists()
            )
        )
        return not user_exists
