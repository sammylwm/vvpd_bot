import logging
from datetime import date

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
)

from bot.attachments import send_attachment_links
from bot.identity import account_id
from bot.preferences import group_for_account
from core.api import ApiError, PolytechApi

router = Router()
logger = logging.getLogger(__name__)


def deadline_button(group: str, start: date, end: date) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📝 Показать дедлайны",
                    callback_data=f"schedule_tasks:{group}:{start.isoformat()}:{end.isoformat()}",
                ),
            ],
        ]
    )


@router.callback_query(F.data == "hide_deadlines")
async def hide_deadlines(callback: CallbackQuery):
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    try:
        await callback.message.delete()
    except TelegramAPIError:
        logger.exception("Failed to hide deadlines message")
        await callback.answer("Не удалось удалить сообщение.", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data.startswith("schedule_tasks:"))
async def show_schedule_deadlines(callback: CallbackQuery, api: PolytechApi):
    from .deadlines import render_deadlines

    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    try:
        _, requested_group, first, last = callback.data.split(":")
        start, end = date.fromisoformat(first), date.fromisoformat(last)
        if start > end:
            raise ValueError("Invalid date range")
    except ValueError:
        await callback.answer("Некорректная кнопка.")
        return
    group = await group_for_account(account_id(callback.message, callback.from_user.id))
    if group != requested_group:
        await callback.answer(
            "Выбери расписание своей текущей группы.", show_alert=True
        )
        return
    await callback.answer()
    try:
        tasks = await api.deadlines(group, start, end)
    except ApiError:
        logger.exception("Failed to load schedule deadlines")
        await callback.message.answer("Не удалось загрузить дедлайны. Попробуй позже.")
        return
    text = (
        render_deadlines(group, start, end, tasks)
        .replace("Дедлайны на неделю", "Дедлайны за выбранные даты")
        .replace("на эту неделю", "на эти даты")
    )
    ms = callback.message
    await ms.answer_rich(
        InputRichMessage(markdown=text),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Скрыть", callback_data="hide_deadlines")],
            ]
        ),
    )
    await send_attachment_links(ms, tasks)
