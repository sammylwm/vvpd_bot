import logging
from datetime import date

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputRichMessage, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.attachments import send_deadline_attachments
from core.modules.deadline import Deadline
from core.modules.group import Group
from core.modules.user import User

router = Router()
logger = logging.getLogger(__name__)


def deadline_button(group_id: int, start: date, end: date) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📝 Показать дедлайны",
            callback_data=f"schedule_tasks:{group_id}:{start.isoformat()}:{end.isoformat()}",
        ),
    ]])


@router.callback_query(F.data.startswith("schedule_tasks:"))
async def show_schedule_deadlines(callback: CallbackQuery, session: AsyncSession):
    from .deadlines import render_deadlines

    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    try:
        _, group_id, first, last = callback.data.split(":")
        start, end = date.fromisoformat(first), date.fromisoformat(last)
        group_id = int(group_id)
        if start > end:
            raise ValueError("Invalid date range")
    except ValueError:
        await callback.answer("Некорректная кнопка.")
        return
    try:
        group = await session.scalar(
            select(Group).join(User, User.group_id == Group.id)
            .where(User.telegram_id == callback.from_user.id, Group.id == group_id)
        )
        if group is None:
            await callback.answer("Выбери расписание своей текущей группы.", show_alert=True)
            return
        tasks = list(await session.scalars(
            select(Deadline).where(
                Deadline.group_id == group_id,
                Deadline.date >= start,
                Deadline.date <= end,
            ).order_by(Deadline.date, Deadline.subject, Deadline.id)
        ))
    except Exception:
        logger.exception("Failed to load schedule deadlines")
        await callback.answer("Не удалось загрузить дедлайны. Попробуй позже.", show_alert=True)
        return
    await callback.answer()
    text = render_deadlines(group.name, start, end, tasks).replace(
        "Дедлайны на неделю", "Дедлайны за выбранные даты"
    ).replace("на эту неделю", "на эти даты")
    ms = callback.message
    await ms.answer_rich(InputRichMessage(markdown=text))
    await send_deadline_attachments(ms.bot, ms.chat.id, tasks, ms.message_thread_id)
