import asyncio
import json
import logging
import re
from datetime import date
from urllib.request import urlopen

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.types import (
    CallbackQuery,
    InputRichBlockButtons,
    InputRichBlockDetails,
    InputRichBlockDivider,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichMessage,
    Message,
    RichMessageButton,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.identity import account_id
from core.modules.group import Group
from core.modules.user import User
from settings.config import config

from .week import lesson_blocks

router = Router()
logger = logging.getLogger(__name__)
WEEKDAYS = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота")


def lesson_weekday(lesson: dict) -> int:
    if lesson.get("date"):
        return date.fromisoformat(lesson["date"]).isoweekday()
    weekday = lesson["weekday"]
    if type(weekday) is not int or not 1 <= weekday <= 7:
        raise ValueError("Invalid weekday")
    return weekday


def get_semester_schedule(group: str, *, schedule_base_url: str = config.api.path) -> dict:
    if re.fullmatch(r"[0-9]{3}-[0-9]{3}", group) is None:
        raise ValueError("Invalid group")
    url = f"{schedule_base_url.rstrip('/')}/schedule/{group}"
    with urlopen(url, timeout=60) as response:
        schedule = json.load(response)
    if not isinstance(schedule["lessons"], list):
        raise ValueError("Invalid schedule response")
    for lesson in schedule["lessons"]:
        lesson_weekday(lesson)
        for key in ("date", "date_from", "date_to"):
            if lesson.get(key):
                date.fromisoformat(lesson[key])
    return schedule


def render_semester(group: str, schedule: dict, *, owner: int) -> InputRichMessage:
    blocks = [
        InputRichBlockSectionHeading(text="🗓 Расписание на семестр", size=1),
        InputRichBlockParagraph(text=f"Группа {group}" + (
            f" · подгруппа {schedule['subgroup']}" if schedule.get("subgroup") else ""
        )),
        InputRichBlockParagraph(text="Раскрой день недели. Под каждым занятием указан период его проведения."),
        InputRichBlockDivider(),
    ]
    for weekday, name in enumerate(WEEKDAYS, start=1):
        lessons = sorted(
            (lesson for lesson in schedule["lessons"] if lesson_weekday(lesson) == weekday),
            key=lambda lesson: (lesson["start"], lesson["subject"], lesson.get("date_from") or ""),
        )
        blocks.append(InputRichBlockDetails(
            summary=f"{name} · занятий: {len(lessons)}" if lessons else f"{name} · нет занятий",
            blocks=lesson_blocks(lessons, show_dates=True),
            is_open=False,
        ))
    blocks.append(InputRichBlockDivider())
    blocks.append(InputRichBlockButtons(buttons=[RichMessageButton(
        text="Закрыть", callback_data=f"semester:{owner}:close", style="danger",
    )], align="center"))
    return InputRichMessage(blocks=blocks)


@router.message(Command("semester"), StateFilter(None))
async def command_semester(ms: Message, command: CommandObject, session: AsyncSession):
    if ms.from_user is None:
        return
    if command.args:
        await ms.answer("Используй /semester без аргументов для получения полного расписания текущего семестра.")
        return
    owner = account_id(ms, ms.from_user.id)
    group = await session.scalar(
        select(Group).join(User, User.group_id == Group.id).where(User.telegram_id == owner)
    )
    if group is None:
        await ms.answer("Сначала укажи свою учебную группу.")
        return
    try:
        schedule = await asyncio.to_thread(get_semester_schedule, group.name)
        message = render_semester(group.name, schedule, owner=owner)
    except Exception:
        logger.exception("Failed to fetch semester schedule")
        await ms.answer("Не удалось получить расписание на семестр. Сервис недоступен или вернул ошибку. Попробуй позже.")
        return
    await ms.answer_rich(message)


@router.callback_query(F.data.startswith("semester:"))
async def close_semester(callback: CallbackQuery):
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    if callback.data != f"semester:{account_id(callback.message, callback.from_user.id)}:close":
        await callback.answer("Открой своё расписание через /semester.", show_alert=True)
        return
    await callback.answer()
    await callback.message.delete()
