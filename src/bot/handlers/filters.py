from aiogram.filters import Filter
from aiogram.types import Message

from bot.identity import account_id
from bot.preferences import preferences


class UnregisteredUser(Filter):
    async def __call__(self, message: Message) -> bool:
        return (
            message.from_user is not None
            and await preferences.profile(account_id(message, message.from_user.id))
            is None
        )
