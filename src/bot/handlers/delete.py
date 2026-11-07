import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.modules.deadline import Deadline
from core.modules.user import User

router = Router()
logger = logging.getLogger(__name__)


def group_for_user(telegram_id: int):
    return select(User.group_id).where(User.telegram_id == telegram_id).scalar_subquery()


@router.message(Command("delete_deadline"), StateFilter(None))
async def command_delete_deadline(ms: Message, session: AsyncSession):
    if ms.from_user is None:
        return
    deadlines = list(await session.scalars(
        select(Deadline).where(Deadline.group_id == group_for_user(ms.from_user.id))
        .order_by(Deadline.date, Deadline.subject, Deadline.id)
    ))
    if not deadlines:
        await ms.answer("В твоей группе нет ДЗ для удаления. Проверь группу: /profile.")
        return
    await ms.answer("Выбери ДЗ для удаления:")
    # Separate cards keep long assignment lists within Telegram message limits.
    for task in deadlines:
        await ms.answer(
            f"{task.date:%d.%m.%Y} — {escape(task.subject)}\n{escape(task.deadline)}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Удалить", callback_data=f"delete_ask:{task.id}"),
            ]]),
        )


@router.callback_query(F.data.startswith("delete_ask:") | F.data.startswith("delete_yes:") | F.data == "delete_no")
async def delete_deadline(callback: CallbackQuery, session: AsyncSession):
    ms = callback.message
    if not isinstance(ms, Message):
        await callback.answer()
        return
    if callback.data == "delete_no":
        await callback.answer("Удаление отменено.")
        await ms.edit_reply_markup(reply_markup=None)
        return
    try:
        deadline_id = int(callback.data.split(":", 1)[1])
    except ValueError:
        await callback.answer("Некорректное задание.")
        return
    condition = (Deadline.id == deadline_id, Deadline.group_id == group_for_user(callback.from_user.id))
    try:
        if callback.data.startswith("delete_ask:"):
            task = await session.scalar(select(Deadline).where(*condition))
            if task is None:
                await callback.answer("ДЗ уже удалено или недоступно.")
                return
            await callback.answer()
            await ms.answer(
                f"Удалить ДЗ для всей группы?\n{task.date:%d.%m.%Y} — "
                f"{escape(task.subject)}\n{escape(task.deadline)}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Да, удалить", callback_data=f"delete_yes:{task.id}"),
                    InlineKeyboardButton(text="Отмена", callback_data="delete_no"),
                ]]),
            )
            return
        result = await session.execute(delete(Deadline).where(*condition).returning(Deadline.id))
        deleted_id = result.scalar_one_or_none()
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Failed to delete deadline")
        await callback.answer("Не удалось удалить ДЗ. Попробуй позже.", show_alert=True)
        return
    await callback.answer("ДЗ удалено." if deleted_id is not None else "ДЗ уже удалено или недоступно.")
    await ms.edit_reply_markup(reply_markup=None)
    await ms.answer("ДЗ удалено." if deleted_id is not None else "ДЗ уже удалено или недоступно.")
