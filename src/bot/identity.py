from aiogram.enums import ChatType
from aiogram.types import Message


def account_id(message: Message, user_id: int) -> int:
    chat = getattr(message, "chat", None)
    if chat is not None and chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return chat.id
    return user_id
