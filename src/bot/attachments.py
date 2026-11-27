import asyncio
import logging
from html import escape
from urllib.parse import urlsplit

from aiohttp import ClientError
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import LinkPreviewOptions, Message, URLInputFile, Update

from core.api import Deadline

MAX_FILES = 5
MAX_FILE_SIZE = 20 * 1024 * 1024
logger = logging.getLogger(__name__)


class AlbumMiddleware(BaseMiddleware):
    def __init__(self):
        self.albums: dict[tuple[int, str], list[Message]] = {}
        self.pending: dict[tuple[int, int | None], asyncio.Future[None]] = {}

    async def __call__(self, handler, event: Update, data):
        message = event.message
        if message is None:
            return await handler(event, data)
        sender = (message.chat.id, message.from_user.id if message.from_user else None)
        previous = self.pending.get(sender)
        if not message.media_group_id:
            if previous is not None:
                await asyncio.shield(previous)
            return await handler(event, data)
        key = (message.chat.id, message.media_group_id)
        if key in self.albums:
            self.albums[key].append(message)
            return
        messages = self.albums[key] = [message]
        completed = self.pending[sender] = asyncio.get_running_loop().create_future()
        try:
            await asyncio.sleep(1)
            self.albums.pop(key, None)
            if previous is not None:
                await asyncio.shield(previous)
            data["album"] = sorted(messages, key=lambda item: item.message_id)
            return await handler(event, data)
        finally:
            if self.albums.get(key) is messages:
                self.albums.pop(key)
            if self.pending.get(sender) is completed:
                self.pending.pop(sender)
            completed.set_result(None)


def attachment_from_message(message: Message) -> dict | None:
    media = message.document or (message.photo[-1] if message.photo else None)
    if media is None:
        return None
    if message.document:
        name = message.document.file_name or "document"
        content_type = message.document.mime_type or "application/octet-stream"
    else:
        name = f"photo-{message.message_id}.jpg"
        content_type = "image/jpeg"
    return {
        "file_id": media.file_id,
        "unique_id": media.file_unique_id,
        "name": name,
        "content_type": content_type,
        "size": media.file_size or 0,
    }


async def send_attachment_links(message: Message, deadlines: list[Deadline]) -> None:
    for deadline in deadlines:
        for attachment in deadline.attachments:
            attachment_url = attachment.url
            if not urlsplit(attachment_url).scheme:
                attachment_url = f"http://{attachment_url.lstrip('/')}"
            url = urlsplit(attachment_url)
            if url.scheme not in {"http", "https"} or not url.netloc:
                continue
            try:
                await message.answer_document(
                    URLInputFile(attachment_url, filename=attachment.name),
                    caption=(
                        f"📎 {escape(deadline.subject)} · "
                        f"{deadline.date:%d.%m.%Y} · {escape(attachment.name)}"
                    ),
                )
            except (ClientError, TelegramAPIError, OSError, ValueError):
                logger.exception(
                    "Failed to send deadline attachment %s", attachment.name
                )
                await message.answer(
                    f'Не удалось прикрепить файл: '
                    f'<a href="{escape(attachment_url)}">{escape(attachment.name)}</a>',
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
