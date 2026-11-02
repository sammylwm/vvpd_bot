import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.types import InputRichMessage, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.modules.deadline import Deadline
from core.modules.group import Group
from core.modules.user import User

from .day import get_day_schedule, markdown_text, subject_key

router = Router()
logger = logging.getLogger(__name__)


def week_dates(day: date) -> list[date]:
    monday = day - timedelta(days=day.weekday())
    return [monday + timedelta(days=offset) for offset in range(6)]


async def get_week_schedule(group: str, days: list[date]) -> list[dict]:
    semaphore = asyncio.Semaphore(3)

    async def fetch(day: date) -> dict:
        async with semaphore:
            return await asyncio.to_thread(get_day_schedule, group, day)

    results = await asyncio.gather(
        *(fetch(day) for day in days), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


def render_week(
    group: str, days: list[date], schedules: list[dict], deadlines: list[Deadline]
) -> str:
    names = (
        "Понедельник",
        "Вторник",
        "Среда",
        "Четверг",
        "Пятница",
        "Суббота",
    )
    by_date = {date.fromisoformat(item["date"]): item["lessons"] for item in schedules}
    tasks_by_date = {day: [] for day in days}
    for task in deadlines:
        if task.date in tasks_by_date:
            tasks_by_date[task.date].append(task)
    parts = [
        "# 🗓 Твоя учебная неделя",
        f"## {days[0]:%d.%m.%Y} — {days[-1]:%d.%m.%Y}",
        f"🎓 **Группа {markdown_text(group)}**",
        f"📚 Занятий: **{sum(len(by_date.get(day, [])) for day in days)}** · 📝 Заданий: **{sum(map(len, tasks_by_date.values()))}**",
        "---",
    ]
    headers = ["Пара"] + [f"{names[day.weekday()]} {day:%d.%m}" for day in days]
    rows = ["| " + " | ".join(headers) + " |", "| :---: | " + " :--- |" * 7]
    pairs = sorted({lesson["pair"] for day in days for lesson in by_date.get(day, [])})
    attached = {day: set() for day in days}
    for pair in pairs:
        cells = [f"**{pair}**"]
        for day in days:
            lessons = sorted(
                (lesson for lesson in by_date.get(day, []) if lesson["pair"] == pair),
                key=lambda lesson: (lesson["start"], lesson["subject"]),
            )
            blocks = []
            for lesson in lessons:
                details = [
                    f"🕒 {markdown_text(lesson['start'])}–{markdown_text(lesson['end'])}",
                    f"**{markdown_text(lesson['subject'])}**",
                ]
                if lesson.get("type"):
                    details.append(f"📖 {markdown_text(lesson['type'])}")
                details.append(
                    f"👤 {markdown_text(lesson.get('teacher') or 'Не указан')}"
                )
                details.append(
                    f"📍 {markdown_text(', '.join(lesson.get('rooms') or []) or 'Не указана')}"
                )
                key = subject_key(lesson["subject"])
                for index, task in enumerate(tasks_by_date[day]):
                    if index not in attached[day] and subject_key(task.subject) == key:
                        details.append(f"📝 {markdown_text(task.deadline)}")
                        attached[day].add(index)
                blocks.append(" · ".join(details))
            cells.append(" / ".join(blocks) or "—")
        rows.append("| " + " | ".join(cells) + " |")
    if not pairs:
        rows.append("| Занятия | " + " | ".join("☕ Нет пар" for _ in days) + " |")
    # Keep deadlines visible even if their subject has no lesson on that date.
    remaining = [
        [
            task
            for index, task in enumerate(tasks_by_date[day])
            if index not in attached[day]
        ]
        for day in days
    ]
    if any(remaining):
        cells = ["**📝 Другие задания**"]
        for tasks in remaining:
            cells.append(
                " · ".join(
                    f"**{markdown_text(task.subject)}**: {markdown_text(task.deadline)}"
                    for task in tasks
                )
                or "—"
            )
        rows.append("| " + " | ".join(cells) + " |")
    parts.append("\n".join(rows))
    parts.append("---")
    if not any(tasks_by_date.values()):
        parts.append("> ✅ Заданий на эту неделю пока нет.")
    return "\n\n".join(parts)


@router.message(Command("week"), StateFilter(None))
async def command_week(ms: Message, command: CommandObject, session: AsyncSession):
    if ms.from_user is None:
        return
    day = datetime.now(ZoneInfo("Europe/Moscow")).date()
    if command.args:
        try:
            if (
                re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", command.args.strip())
                is None
            ):
                raise ValueError("Invalid date")
            day = date.fromisoformat(command.args.strip())
        except ValueError:
            await ms.answer(
                "Используй /week или /week ГГГГ-ММ-ДД для недели с указанной датой."
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
    days = week_dates(day)
    try:
        schedules = await get_week_schedule(group.name, days)
    except Exception:
        logger.exception("Failed to fetch weekly schedule")
        await ms.answer(
            "Не удалось получить расписание на неделю. Сервис недоступен или вернул ошибку. Попробуй позже."
        )
        return
    try:
        deadlines = list(
            await session.scalars(
                select(Deadline)
                .where(
                    Deadline.group_id == group.id,
                    Deadline.date >= days[0],
                    Deadline.date <= days[-1],
                )
                .order_by(Deadline.date, Deadline.subject, Deadline.id)
            )
        )
    except Exception:
        logger.exception("Failed to load weekly deadlines")
        await ms.answer("Не удалось загрузить задания из базы данных. Попробуй позже.")
        return
    await ms.answer_rich(
        InputRichMessage(markdown=render_week(group.name, days, schedules, deadlines))
    )
