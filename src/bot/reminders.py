import asyncio
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InputRichMessage
from sqlalchemy import select

from bot.attachments import send_deadline_attachments
from bot.handlers.day import get_day_schedule, markdown_text, render_day
from core.db_helper import async_session_maker
from core.modules.deadline import Deadline
from core.modules.group import Group
from core.modules.user import User

logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")


def next_reminder_at(now: datetime) -> datetime:
    local_now = now.astimezone(MOSCOW)
    upcoming = local_now.replace(hour=8, minute=0, second=0, microsecond=0)
    if upcoming <= local_now:
        upcoming += timedelta(days=1)
    return upcoming


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
    tomorrow = today + timedelta(days=1)
    try:
        async with async_session_maker() as session:
            tasks = list(
                await session.scalars(
                    select(Deadline)
                    .where(
                        Deadline.group_id == group_id,
                        Deadline.date.in_([today, tomorrow]),
                    )
                    .order_by(Deadline.subject, Deadline.id)
                )
            )
    except Exception:
        logger.exception("Failed to load reminders for group %s", group_name)
        return [
            "Не удалось загрузить задания. Попробуй проверить их позже через /day."
        ], []
    else:
        try:
            schedule = await asyncio.to_thread(
                get_day_schedule, group_name, today
            )
            daily_message = render_day(
                group_name,
                schedule,
                [task for task in tasks if task.date == today],
            )
        except Exception:
            logger.exception(
                "Failed to fetch schedule for group %s", group_name
            )
            daily_message = (
                "Не удалось получить расписание на сегодня. "
                "Попробуй позже через /day."
            )
        return [
            daily_message,
            render_tomorrow_deadlines(
                tomorrow, [task for task in tasks if task.date == tomorrow]
            ),
        ], tasks


async def build_reminder_messages(group_id: int, group_name: str, today: date) -> list[str]:
    messages, _ = await build_reminder_payload(group_id, group_name, today)
    return messages


async def send_reminders(bot: Bot) -> None:
    today = datetime.now(MOSCOW).date()
    async with async_session_maker() as session:
        users = list(
            await session.execute(
                select(User.telegram_id, Group.id, Group.name)
                .join(Group, User.group_id == Group.id)
                .where(User.reminders_enabled.is_(True))
                .distinct()
            )
        )

    # Fetch the schedule and assignments once per group for this broadcast.
    messages_by_group: dict[int, tuple[list[str], list[Deadline]]] = {}
    for user_id, group_id, group_name in users:
        if group_id not in messages_by_group:
            messages_by_group[group_id] = await build_reminder_payload(
                group_id, group_name, today
            )

        messages, tasks = messages_by_group[group_id]
        for message in messages:
            failed = False
            while True:
                try:
                    await bot.send_rich_message(
                        user_id, InputRichMessage(markdown=message)
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
            try:
                await send_deadline_attachments(bot, user_id, tasks)
            except TelegramAPIError:
                logger.exception("Failed to send reminder attachments to user %s", user_id)


async def run_reminders(bot: Bot) -> None:
    while True:
        now = datetime.now(MOSCOW)
        upcoming = next_reminder_at(now)
        await asyncio.sleep((upcoming - now).total_seconds())
        try:
            await send_reminders(bot)
        except Exception:
            logger.exception("Daily reminder broadcast failed")
