import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.types import (
    CallbackQuery, InputRichBlockButtons, InputRichBlockDetails,
    InputRichBlockParagraph, InputRichBlockSectionHeading, InputRichMessage,
    Message, RichMessageButton,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.deadline_buttons import deadline_button
from bot.identity import account_id
from core.modules.deadline import Deadline
from core.modules.group import Group
from core.modules.user import User

from .day import get_day_schedule

router = Router()
logger = logging.getLogger(__name__)


def week_dates(day: date) -> list[date]:
    monday = day - timedelta(days=day.weekday())
    return [monday + timedelta(days=offset) for offset in range(7)]


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
    group: str, days: list[date], schedules: list[dict], deadlines: list[Deadline],
    *, owner: int = 0, selected: int | None = None,
) -> InputRichMessage:
    names = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")
    short_names = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    by_date = {date.fromisoformat(item["date"]): item["lessons"] for item in schedules}
    prefix = f"week:{owner}:{days[0].isoformat()}"
    subgroup = next((item.get("subgroup") for item in schedules if item.get("subgroup")), None)
    blocks = [
        InputRichBlockSectionHeading(text="Неделя", size=1),
        InputRichBlockParagraph(text=f"{days[0]:%d.%m}-{days[-1]:%d.%m.%Y}"),
        InputRichBlockParagraph(text=f"Группа {group}" + (f" · подгруппа {subgroup}" if subgroup else "")),
    ]
    buttons = [RichMessageButton(text=f"{short_names[day.weekday()]} {day.day}",
                                callback_data=f"{prefix}:day{index}")
               for index, day in enumerate(days)]
    blocks.extend(InputRichBlockButtons(buttons=buttons[start:start + count], align="center")
                  for start, count in ((0, 4), (4, 3)))
    blocks.append(InputRichBlockButtons(buttons=[RichMessageButton(
        text="Вся неделя", callback_data=f"{prefix}:all")], align="center"))
    for index, day in enumerate(days):
        if selected is not None and selected != index:
            continue
        lessons = sorted(by_date.get(day, []), key=lambda lesson: (lesson["start"], lesson["subject"]))
        contents = []
        for lesson in lessons:
            contents.append(InputRichBlockSectionHeading(text=f"{lesson['start']}-{lesson['end']}", size=3))
            subject = lesson["subject"]
            if lesson.get("type"):
                subject += f" ({lesson['type']})"
            contents.append(InputRichBlockParagraph(text=subject))
            if lesson.get("teacher"):
                contents.append(InputRichBlockParagraph(text=f"Преподаватель: {lesson['teacher']}"))
            if lesson.get("rooms"):
                contents.append(InputRichBlockParagraph(text=f"Аудитория: {', '.join(lesson['rooms'])}"))
            link = lesson.get("url") or lesson.get("link")
            if isinstance(link, str) and link.startswith(("https://", "http://")):
                contents.append(InputRichBlockButtons(buttons=[RichMessageButton(text="Подключиться", url=link)]))
        if not lessons:
            contents.append(InputRichBlockParagraph(text="Занятий нет"))
        count = f"занятий: {len(lessons)}" if lessons else "нет занятий"
        blocks.append(InputRichBlockDetails(
            summary=f"{names[day.weekday()]}, {day:%d.%m} · {count}",
            blocks=contents, is_open=selected is not None,
        ))
    blocks.append(InputRichBlockButtons(buttons=[
        RichMessageButton(text="‹", callback_data=f"{prefix}:prev"),
        RichMessageButton(text="Эта неделя", callback_data=f"{prefix}:today"),
        RichMessageButton(text="›", callback_data=f"{prefix}:next"),
    ], align="center"))
    blocks.append(InputRichBlockButtons(buttons=[RichMessageButton(
        text="Закрыть", callback_data=f"{prefix}:close", style="danger")], align="center"))
    return InputRichMessage(blocks=blocks)


@router.callback_query(F.data.startswith("week:"))
async def week_action(callback: CallbackQuery, session: AsyncSession):
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    try:
        _, owner, first, action = (callback.data or "").split(":")
        owner = int(owner)
        day = date.fromisoformat(first)
        if action not in {"prev", "next", "today", "close", "all", *(f"day{i}" for i in range(7))}:
            raise ValueError("Invalid action")
    except ValueError:
        await callback.answer("Некорректная кнопка.")
        return
    if owner != account_id(callback.message, callback.from_user.id):
        await callback.answer("Открой своё расписание через /week.", show_alert=True)
        return
    if action == "close":
        await callback.answer()
        await callback.message.delete()
        return
    group = await session.scalar(
        select(Group).join(User, User.group_id == Group.id).where(User.telegram_id == owner)
    )
    if group is None:
        await callback.answer("Сначала укажи свою учебную группу.", show_alert=True)
        return
    await callback.answer()
    if action == "today":
        day = datetime.now(ZoneInfo("Europe/Moscow")).date()
    elif action in {"prev", "next"}:
        day += timedelta(days=-7 if action == "prev" else 7)
    days = week_dates(day)
    try:
        schedules = await get_week_schedule(group.name, days)
    except Exception:
        logger.exception("Failed to fetch weekly schedule")
        await callback.message.answer("Не удалось получить расписание на неделю. Попробуй позже.")
        return
    await callback.message.edit_text(
        rich_message=render_week(group.name, days, schedules, [], owner=owner,
                                 selected=int(action[3:]) if action.startswith("day") else None),
        reply_markup=deadline_button(group.id, days[0], days[-1]),
    )


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
        .where(User.telegram_id == account_id(ms, ms.from_user.id))
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
    await ms.answer_rich(
        render_week(group.name, days, schedules, [], owner=account_id(ms, ms.from_user.id)),
        reply_markup=deadline_button(group.id, days[0], days[-1]),
    )
