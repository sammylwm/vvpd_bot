import asyncio
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InputRichMessage
from sqlalchemy import or_, select, update

from bot.handlers.deadline_buttons import deadline_button
from bot.handlers.day import get_day_schedule, markdown_text, render_day
from core.db_helper import async_session_maker
from core.modules.deadline import Deadline
from core.modules.group import Group
from core.modules.user import User

logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")


def render_tomorrow_deadlines(day: date, deadlines: list[Deadline]) -> str:
    parts = [f"# ⏰ Дедлайны на завтра · {day:%d.%m.%Y}"]
    if deadlines:
        for task in deadlines:
            parts.append(
                f"- **{markdown_text(task.subject)}** — {markdown_text(task.deadline)}"
            )
    else:
        parts.append("> ✅ Заданий на завтра пока нет.")
    return "\n\n".join(parts)


async def build_reminder_payload(
    group_id: int, group_name: str, today: date
) -> tuple[list[str], list[Deadline]]:
    try:
        schedule = await asyncio.to_thread(get_day_schedule, group_name, today)
        message = render_day(group_name, schedule, [])
    except Exception:
        logger.exception("Failed to fetch schedule for group %s", group_name)
        message = "Не удалось получить расписание на сегодня. Попробуй позже через /day."
    return [message], []


async def build_reminder_messages(group_id: int, group_name: str, today: date) -> list[str]:
    messages, _ = await build_reminder_payload(group_id, group_name, today)
    return messages


async def send_reminders(bot: Bot) -> None:
    now = datetime.now(MOSCOW)
    today = now.date()
    current_time = now.time().replace(second=0, microsecond=0, tzinfo=None)
    async with async_session_maker() as session:
        users = list(
            await session.execute(
                select(User.telegram_id, Group.id, Group.name)
                .join(Group, User.group_id == Group.id)
                .where(
                    User.reminders_enabled.is_(True),
                    User.reminder_time == current_time,
                    or_(User.last_reminder_date.is_(None), User.last_reminder_date < today),
                )
                .distinct()
            )
        )

    messages_by_group: dict[int, tuple[list[str], list[Deadline]]] = {}
    for user_id, group_id, group_name in users:
        if group_id not in messages_by_group:
            messages_by_group[group_id] = await build_reminder_payload(
                group_id, group_name, today
            )

        messages, _ = messages_by_group[group_id]
        failed = False
        for message in messages:
            while True:
                try:
                    await bot.send_rich_message(
                        user_id, InputRichMessage(markdown=message),
                        reply_markup=deadline_button(group_id, today, today + timedelta(days=1))
                    )
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after)
                    continue
                except TelegramAPIError:
                    logger.exception("Failed to send reminder to user %s", user_id)
                    failed = True
                break
            await asyncio.sleep(0.05)
            if failed:
                break

        if not failed:
            async with async_session_maker() as session:
                await session.execute(
                    update(User).where(User.telegram_id == user_id).values(last_reminder_date=today)
                )
                await session.commit()


async def run_reminders(bot: Bot) -> None:
    while True:
        try:
            await send_reminders(bot)
        except Exception:
            logger.exception("Reminder broadcast failed")
        now = datetime.now(MOSCOW)
        upcoming = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        await asyncio.sleep((upcoming - now).total_seconds())
