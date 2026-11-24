import logging
from datetime import date

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.identity import account_id
from bot.preferences import group_for_account
from core.api import ApiError, PolytechApi

from .add import deadline_details

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("delete_deadline"), StateFilter(None))
async def command_delete_deadline(ms: Message, api: PolytechApi):
    if ms.from_user is None:
        return
    group = await group_for_account(account_id(ms, ms.from_user.id))
    if group is None:
        await ms.answer("Сначала укажи свою учебную группу.")
        return
    try:
        deadlines = await api.deadlines(group, date.min, date.max)
    except ApiError:
        logger.exception("Failed to load deadlines for deletion")
        await ms.answer("Не удалось загрузить ДЗ. Попробуй позже.")
        return
    if not deadlines:
        await ms.answer("В твоей группе нет ДЗ для удаления.")
        return
    await ms.answer("Выбери ДЗ для удаления:")
    for task in deadlines:
        await ms.answer(
            deadline_details(task),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Удалить",
                            callback_data=f"delete_ask:{task.id}:{task.date}",
                        ),
                    ]
                ]
            ),
        )


@router.callback_query(
    F.data.startswith("delete_ask:")
    | F.data.startswith("delete_yes:")
    | (F.data == "delete_no")
)
async def delete_deadline(callback: CallbackQuery, api: PolytechApi):
    ms = callback.message
    if not isinstance(ms, Message):
        await callback.answer()
        return
    if callback.data == "delete_no":
        await callback.answer("Удаление отменено.")
        await ms.edit_reply_markup(reply_markup=None)
        return
    try:
        action, raw_id, raw_date = callback.data.split(":")
        deadline_id, day = int(raw_id), date.fromisoformat(raw_date)
    except ValueError:
        await callback.answer("Кнопка устарела. Открой /delete_deadline.")
        return

    group = await group_for_account(account_id(ms, callback.from_user.id))
    await callback.answer()
    try:
        tasks = await api.deadlines(group, day, day) if group else []
        task = next(
            (task for task in tasks if task.id == deadline_id and task.group == group),
            None,
        )
        if task is None:
            await ms.answer("ДЗ уже удалено или недоступно.")
            return
        if action == "delete_ask":
            await ms.answer(
                f"Удалить ДЗ для всей группы?\n{deadline_details(task)}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Да, удалить",
                                callback_data=f"delete_yes:{task.id}:{task.date}",
                            ),
                            InlineKeyboardButton(
                                text="Отмена", callback_data="delete_no"
                            ),
                        ]
                    ]
                ),
            )
            return
        await api.delete(deadline_id)
    except ApiError as exc:
        if exc.status != 404:
            logger.exception("Failed to delete deadline")
            await ms.answer("Не удалось удалить ДЗ. Попробуй позже.")
            return
        await ms.edit_reply_markup(reply_markup=None)
        await ms.answer("ДЗ уже удалено или недоступно.")
        return
    await ms.edit_reply_markup(reply_markup=None)
    await ms.answer("ДЗ удалено.")
