import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import InputRichMessage

from bot.handlers.day import render_day
from bot.handlers.deadline_buttons import deadline_button
from bot.preferences import preferences
from core.api import ApiError, PolytechApi

logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")


async def send_reminders(bot: Bot, api: PolytechApi) -> None:
    now = datetime.now(MOSCOW)
    if now.weekday() == 6:
        return
    today = now.date()
    current_time = now.time().replace(second=0, microsecond=0, tzinfo=None)
    messages_by_group: dict[str, str | None] = {}
    for profile in await preferences.due_profiles(today, current_time):
        chat_id, group = profile.telegram_id, profile.group
        topic_options = {}
        if chat_id < 0:
            chat = await preferences.topic(chat_id)
            if chat is None or not chat.active or chat.topic_id is None:
                continue
            topic_options["message_thread_id"] = chat.topic_id

        if group not in messages_by_group:
            try:
                schedule = await api.schedule(group, today, today)
                messages_by_group[group] = render_day(
                    group,
                    {"date": today.isoformat(), "lessons": schedule.lessons},
                )
            except ApiError:
                logger.exception("Failed to fetch schedule for group %s", group)
                messages_by_group[group] = None
        message = messages_by_group[group]
        if message is None:
            continue

        while True:
            try:
                await bot.send_rich_message(
                    chat_id,
                    InputRichMessage(markdown=message),
                    reply_markup=deadline_button(
                        group, today, today + timedelta(days=1)
                    ),
                    **topic_options,
                )
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
                continue
            except TelegramAPIError:
                logger.exception("Failed to send reminder to chat %s", chat_id)
            else:
                await preferences.update_profile(chat_id, last_reminder_date=today)
            break
        await asyncio.sleep(0.05)


async def run_reminders(bot: Bot, api: PolytechApi) -> None:
    while True:
        try:
            await send_reminders(bot, api)
        except Exception:
            logger.exception("Reminder broadcast failed")
        now = datetime.now(MOSCOW)
        upcoming = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        await asyncio.sleep((upcoming - now).total_seconds())
