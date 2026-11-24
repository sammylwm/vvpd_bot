import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

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

from bot.identity import account_id
from bot.preferences import group_for_account
from core.api import PolytechApi

from .week import lesson_blocks

router = Router()
logger = logging.getLogger(__name__)
WEEKDAYS = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)


def lesson_weekday(lesson: dict) -> int:
    if lesson.get("weekday") is None and lesson.get("date"):
        return date.fromisoformat(lesson["date"]).isoweekday()
    weekday = lesson["weekday"]
    if type(weekday) is not int or not 1 <= weekday <= 7:
        raise ValueError("Invalid weekday")
    return weekday


def semester_bounds(day: date) -> tuple[date, date]:
    if 2 <= day.month <= 8:
        return date(day.year, 2, 1), date(day.year, 8, 31)
    year = day.year - (day.month == 1)
    return date(year, 9, 1), date(year + 1, 1, 31)


async def get_semester_schedule(api: PolytechApi, group: str, day: date) -> dict:
    schedule = await api.schedule(group, *semester_bounds(day))
    unique_lessons = {}
    for occurrence in schedule.lessons:
        lesson = dict(occurrence)
        if lesson.get("weekday") is not None:
            lesson["date"] = None
        key = (
            lesson_weekday(lesson),
            lesson["pair"],
            lesson["subject"],
            lesson.get("type"),
            lesson.get("teacher"),
            tuple(lesson.get("rooms") or []),
            lesson.get("link"),
            lesson.get("date"),
            lesson.get("date_from"),
            lesson.get("date_to"),
        )
        unique_lessons[key] = lesson
    return {"lessons": list(unique_lessons.values())}


def render_semester(group: str, schedule: dict, *, owner: int) -> InputRichMessage:
    blocks = [
        InputRichBlockSectionHeading(text="🗓 Расписание на семестр", size=1),
        InputRichBlockParagraph(
            text=f"Группа {group}"
            + (
                f" · подгруппа {schedule['subgroup']}"
                if schedule.get("subgroup")
                else ""
            )
        ),
        InputRichBlockParagraph(
            text="Раскрой день недели. Под каждым занятием указан период его проведения."
        ),
        InputRichBlockDivider(),
    ]
    for weekday, name in enumerate(WEEKDAYS, start=1):
        lessons = sorted(
            (
                lesson
                for lesson in schedule["lessons"]
                if lesson_weekday(lesson) == weekday
            ),
            key=lambda lesson: (
                lesson["start"],
                lesson["subject"],
                lesson.get("date_from") or "",
            ),
        )
        blocks.append(
            InputRichBlockDetails(
                summary=f"{name} · занятий: {len(lessons)}"
                if lessons
                else f"{name} · нет занятий",
                blocks=lesson_blocks(lessons, show_dates=True),
                is_open=False,
            )
        )
    blocks.append(InputRichBlockDivider())
    blocks.append(
        InputRichBlockButtons(
            buttons=[
                RichMessageButton(
                    text="Закрыть",
                    callback_data=f"semester:{owner}:close",
                    style="danger",
                )
            ],
            align="center",
        )
    )
    return InputRichMessage(blocks=blocks)


@router.message(Command("semester"), StateFilter(None))
async def command_semester(ms: Message, command: CommandObject, api: PolytechApi):
    if ms.from_user is None:
        return
    if command.args:
        await ms.answer(
            "Используй /semester без аргументов для получения полного расписания текущего семестра."
        )
        return
    owner = account_id(ms, ms.from_user.id)
    group = await group_for_account(owner)
    if group is None:
        await ms.answer("Сначала укажи свою учебную группу.")
        return
    try:
        schedule = await get_semester_schedule(
            api, group, datetime.now(ZoneInfo("Europe/Moscow")).date()
        )
        message = render_semester(group, schedule, owner=owner)
    except Exception:
        logger.exception("Failed to fetch semester schedule")
        await ms.answer(
            "Не удалось получить расписание на семестр. Сервис недоступен или вернул ошибку. Попробуй позже."
        )
        return
    await ms.answer_rich(message)


@router.callback_query(F.data.startswith("semester:"))
async def close_semester(callback: CallbackQuery):
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    if (
        callback.data
        != f"semester:{account_id(callback.message, callback.from_user.id)}:close"
    ):
        await callback.answer(
            "Открой своё расписание через /semester.", show_alert=True
        )
        return
    await callback.answer()
    await callback.message.delete()
