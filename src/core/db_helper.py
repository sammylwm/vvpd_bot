from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from settings.config import config

from .modules import User

db_url = config.database.async_url
engine = create_async_engine(url=db_url)
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


class DbSessionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        async with async_session_maker() as session:
            try:
                data["session"] = session
                return await handler(event, data)
            finally:
                await session.close()


async def user_db(session: AsyncSession, uid: int) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == uid))

    if not user:
        user = User(telegram_id=uid)
        session.add(user)
        await session.flush()

    return user


async def chat_db(session: AsyncSession, cid: int) -> Chat:
    chat = await session.scalar(select(Chat).where(Chat.telegram_id == cid))

    if not chat:
        chat = Chat(telegram_id=cid)
        session.add(chat)
        await session.flush()

    return chat


async def link_user_to_chat(
    session: AsyncSession,
    user: User,
    chat: Chat,
) -> None:
    await session.execute(
        insert(chat_users)
        .values(chat_id=chat.id, user_id=user.id)
        .on_conflict_do_nothing()
    )
