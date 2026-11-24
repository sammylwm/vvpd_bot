import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.types import InputRichMessage, Message

from bot.handlers.deadline_buttons import deadline_button
from bot.identity import account_id
from bot.preferences import group_for_account
from core.api import ApiError, PolytechApi

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("reminder"), StateFilter(None))
async def command_reminder(ms: Message, api: PolytechApi):
    if ms.from_user is None:
        return
    group = await group_for_account(account_id(ms, ms.from_user.id))
    if group is None:
        await ms.answer("Сначала укажи свою учебную группу.")
        return
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    await send_schedule(ms, api, group, today, today + timedelta(days=1))


def markdown_text(value: str) -> str:
    value = " ".join(str(value).split())
    return re.sub(r"([\\`*_{}\[\]<>()#+.!|~>\-])", r"\\\1", value)


def render_day(group: str, schedule: dict) -> str:
    day = date.fromisoformat(schedule["date"])
    weekdays = (
        "понедельник",
        "вторник",
        "среда",
        "четверг",
        "пятница",
        "суббота",
        "воскресенье",
    )
    lessons = sorted(
        schedule["lessons"],
        key=lambda lesson: (lesson["pair"], lesson["start"], lesson["subject"]),
    )
    parts = [
        "# 🗓 Твой учебный день",
        f"## {day:%d.%m.%Y} · {weekdays[day.weekday()].capitalize()}",
        f"🎓 **Группа {markdown_text(group)}**",
        f"📚 Занятий: **{len(lessons)}**",
        "---",
        "## 📚 Расписание занятий",
    ]
    if lessons:
        for index, lesson in enumerate(lessons):
            if index:
                parts.append("---")
            parts.extend(
                [
                    f"### {lesson['pair']} пара",
                    f"🕒 **{markdown_text(lesson['start'])}-{markdown_text(lesson['end'])}**",
                    f"**{markdown_text(lesson['subject'])}**",
                ]
            )
            if lesson.get("type"):
                parts.append(f"📖 {markdown_text(lesson['type'])}")
            parts.append(
                f"👤 {markdown_text(lesson.get('teacher') or 'Преподаватель не указан')}"
            )
            rooms = ", ".join(lesson.get("rooms") or [])
            parts.append(f"📍 {markdown_text(rooms or 'Аудитория не указана')}")
    else:
        parts.append("> ☕ На эту дату занятий нет.")

    parts.append("---")
    return "\n\n".join(parts)


@router.message(Command("day", "nextday"), StateFilter(None))
async def command_day(ms: Message, command: CommandObject, api: PolytechApi):
    if ms.from_user is None:
        return
    day = datetime.now(ZoneInfo("Europe/Moscow")).date()
    if command.command == "nextday":
        if command.args:
            await ms.answer(
                "Используй /nextday без даты - покажу расписание на завтра."
            )
            return
        day += timedelta(days=1)
    elif command.args:
        try:
            if (
                re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", command.args.strip())
                is None
            ):
                raise ValueError("Invalid date format")
            day = date.fromisoformat(command.args.strip())
        except ValueError:
            await ms.answer(
                "Укажи дату в формате /day ГГГГ-ММ-ДД, например /day 2026-11-03."
            )
            return
    group = await group_for_account(account_id(ms, ms.from_user.id))
    if group is None:
        await ms.answer("Сначала укажи свою учебную группу.")
        return
    await send_schedule(ms, api, group, day, day)


async def send_schedule(
    ms: Message, api: PolytechApi, group: str, day: date, deadline_end: date
):
    try:
        schedule = await api.schedule(group, day, day)
    except ApiError:
        logger.exception("Failed to fetch daily schedule")
        await ms.answer("Не удалось получить расписание. Попробуй позже.")
        return
    await ms.answer_rich(
        InputRichMessage(
            markdown=render_day(
                group, {"date": day.isoformat(), "lessons": schedule.lessons}
            )
        ),
        reply_markup=deadline_button(group, day, deadline_end),
    )
