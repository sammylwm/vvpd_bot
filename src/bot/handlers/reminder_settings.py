from aiogram import Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.types import Message
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.modules.user import User

router = Router()


@router.message(Command("reminders"), StateFilter(None))
async def command_reminders(ms: Message, command: CommandObject, session: AsyncSession):
    if ms.from_user is None:
        return
    action = (command.args or "").strip().lower()
    if action not in ("", "on", "off"):
        await ms.answer("Используй /reminders, /reminders on или /reminders off.")
        return
    enabled = await session.scalar(
        select(User.reminders_enabled).where(User.telegram_id == ms.from_user.id)
    )
    if enabled is None:
        await ms.answer("Сначала укажи свою учебную группу.")
        return
    if action:
        enabled = action == "on"
        await session.execute(
            update(User)
            .where(User.telegram_id == ms.from_user.id)
            .values(reminders_enabled=enabled)
        )
        await session.commit()
    status = "включены (ежедневно в 08:00 по Москве)" if enabled else "отключены"
    await ms.answer(
        f"⏰ Утренние напоминания {status}.\n\n"
        "/reminders on — включить\n"
        "/reminders off — отключить\n"
        "/reminder — получить расписание и дедлайны сейчас"
    )
