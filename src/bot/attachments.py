"""Telegram media collection and delivery without downloading files."""

import asyncio
import logging
from html import escape

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import Message

from core.modules.deadline import Deadline

logger = logging.getLogger(__name__)
MEDIA_TYPES = (
    "photo",
    "document",
    "video",
    "audio",
    "voice",
    "animation",
    "video_note",
    "sticker",
)


def message_attachments(message: Message) -> list[dict[str, str]]:
    for kind in MEDIA_TYPES:
        media = getattr(message, kind)
        if media:
            if kind == "photo":
                media = media[-1]
            return [
                {
                    "type": kind,
                    "file_id": media.file_id,
                    "file_unique_id": media.file_unique_id,
                }
            ]
    return []


class AlbumMiddleware(BaseMiddleware):
    """Collect the separate updates Telegram sends for a single album."""

    def __init__(self):
        self.albums: dict[tuple[int, str], list[Message]] = {}

    async def __call__(self, handler, event: Message, data):
        if not event.media_group_id:
            data["album"] = [event]
            return await handler(event, data)
        key = (event.chat.id, event.media_group_id)
        if key in self.albums:
            self.albums[key].append(event)
            return
        messages = self.albums[key] = [event]
        try:
            await asyncio.sleep(1)
        finally:
            self.albums.pop(key, None)
        data["album"] = sorted(messages, key=lambda message: message.message_id)
        return await handler(event, data)


async def send_deadline_attachments(
    bot: Bot,
    chat_id: int,
    deadlines: list[Deadline],
    message_thread_id: int | None = None,
) -> None:
    for task in deadlines:
        attachments = task.attachments or []
        if not attachments:
            continue
        # Keep the full task in a separate message: Telegram captions have a smaller limit.
        await bot.send_message(
            chat_id,
            f"📎 {escape(task.subject)} · {task.date:%d.%m.%Y}\n"
            f"{escape(task.deadline)}",
            parse_mode="HTML",
            message_thread_id=message_thread_id,
        )
        for attachment in attachments:
            kind = attachment["type"]
            while True:
                try:
                    await getattr(bot, f"send_{kind}")(
                        chat_id=chat_id,
                        **{kind: attachment["file_id"]},
                        message_thread_id=message_thread_id,
                    )
                    break
                except TelegramRetryAfter as exc:
                    await asyncio.sleep(exc.retry_after)
                except TelegramAPIError:
                    logger.exception(
                        "Failed to send attachment for deadline %s", task.id
                    )
                    await bot.send_message(
                        chat_id,
                        "Не удалось отправить вложение. Попробуй позже.",
                        message_thread_id=message_thread_id,
                    )
                    break
