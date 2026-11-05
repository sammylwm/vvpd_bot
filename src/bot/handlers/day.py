import asyncio
import json
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.types import InputRichMessage, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.attachments import send_deadline_attachments
from core.modules.deadline import Deadline
from core.modules.group import Group
from core.modules.user import User
from settings.config import config

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("reminder"), StateFilter(None))
async def command_reminder(ms: Message, session: AsyncSession):
    # Import here because the reminder service reuses this module's day renderer.
    from bot.reminders import build_reminder_payload

    if ms.from_user is None:
        return
    group = await session.scalar(
        select(Group)
        .join(User, User.group_id == Group.id)
        .where(User.telegram_id == ms.from_user.id)
    )
    if group is None:
        await ms.answer("Сначала укажи свою учебную группу.")
        return
    messages, tasks = await build_reminder_payload(
        group.id, group.name, datetime.now(ZoneInfo("Europe/Moscow")).date()
    )
    for message in messages:
        await ms.answer_rich(InputRichMessage(markdown=message))
    await send_deadline_attachments(ms.bot, ms.chat.id, tasks, ms.message_thread_id)


def get_day_schedule(
    group: str,
    day: date | None = None,
    *,
    schedule_base_url: str = config.api.path,
) -> dict:
    if re.fullmatch(r"[0-9]{3}-[0-9]{3}", group) is None:
        raise ValueError("Invalid group")
    url = f"{schedule_base_url.rstrip('/')}/schedule/{group}/day"
    if day is not None:
        url += "?" + urlencode({"day": day.isoformat()})
    with urlopen(url, timeout=60) as response:
        result = json.load(response)
    response_day = date.fromisoformat(result["date"])
    if day is not None and response_day != day:
        raise ValueError("Schedule returned a different date")
    if not isinstance(result["lessons"], list):
        raise ValueError("Invalid schedule response")
    return result


def markdown_text(value: str) -> str:
    value = " ".join(str(value).split())
    return re.sub(r"([\\`*_{}\[\]<>()#+.!|~>\-])", r"\\\1", value)


def subject_key(value: str) -> str:
    value = re.sub(r"\([^()]*\)", "", value)
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def render_day(group: str, schedule: dict, deadlines: list[Deadline]) -> str:
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
        f"📚 Занятий: **{len(lessons)}** · 📝 Заданий: **{len(deadlines)}**",
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
                    f"🕒 **{markdown_text(lesson['start'])}–{markdown_text(lesson['end'])}**",
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

    parts.extend(["---", "## 📝 Задания к этому дню"])
    tasks_by_subject = {}
    for task in deadlines:
        tasks_by_subject.setdefault(subject_key(task.subject), []).append(task)

    shown_subjects = set()
    for lesson in lessons:
        key = subject_key(lesson["subject"])
        if key in shown_subjects or key not in tasks_by_subject:
            continue
        tasks = tasks_by_subject[key]
        parts.append(f"### 📖 {markdown_text(tasks[0].subject)}")
        parts.append("\n".join(f"- {markdown_text(task.deadline)}" for task in tasks))
        shown_subjects.add(key)

    other_tasks = [
        task for task in deadlines if subject_key(task.subject) not in shown_subjects
    ]
    if other_tasks:
        parts.append("### 📌 По предметам без пар в этот день")
        parts.append(
            "\n".join(
                f"- **{markdown_text(task.subject)}** — {markdown_text(task.deadline)}"
                for task in other_tasks
            )
        )
    if not deadlines:
        parts.append("> ✅ Заданий на эту дату пока нет.")
    parts.append("---")
    return "\n\n".join(parts)


@router.message(Command("day", "nextday"), StateFilter(None))
async def command_day(ms: Message, command: CommandObject, session: AsyncSession):
    if ms.from_user is None:
        return
    day = None
    if command.command == "nextday":
        if command.args:
            await ms.answer(
                "Используй /nextday без даты — покажу расписание на завтра."
            )
            return
        day = datetime.now(ZoneInfo("Europe/Moscow")).date() + timedelta(days=1)
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
    group = await session.scalar(
        select(Group)
        .join(User, User.group_id == Group.id)
        .where(User.telegram_id == ms.from_user.id)
    )
    if group is None:
        await ms.answer("Сначала укажи свою учебную группу.")
        return
    try:
        schedule = await asyncio.to_thread(get_day_schedule, group.name, day)
        # Use the API's date, including when it selected today itself.
        schedule_day = date.fromisoformat(schedule["date"])
    except Exception:
        logger.exception("Failed to fetch daily schedule")
        await ms.answer(
            "Не удалось получить расписание: сервис недоступен или вернул ошибку. Попробуй позже."
        )
        return
    try:
        deadlines = list(
            await session.scalars(
                select(Deadline)
                .where(
                    Deadline.group_id == group.id,
                    Deadline.date == schedule_day,
                )
                .order_by(Deadline.subject, Deadline.id)
            )
        )
    except Exception:
        logger.exception("Failed to load daily deadlines")
        await ms.answer("Не удалось загрузить задания из базы данных. Попробуй позже.")
        return
    await ms.answer_rich(
        InputRichMessage(markdown=render_day(group.name, schedule, deadlines))
    )

    await send_deadline_attachments(ms.bot, ms.chat.id, deadlines, ms.message_thread_id)
