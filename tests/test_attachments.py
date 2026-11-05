import asyncio
import unittest
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from bot.attachments import (
    AlbumMiddleware,
    message_attachments,
    send_deadline_attachments,
)
from bot.handlers.add import (
    AddDeadline,
    receive_deadline,
    receive_subject,
    save_deadline,
)
from core.modules.deadline import Deadline


def message(**kwargs):
    return Message(
        message_id=kwargs.pop("message_id", 1),
        date=datetime.now(UTC),
        chat={"id": 1, "type": "private"},
        from_user={"id": 1, "is_bot": False, "first_name": "Test"},
        **kwargs,
    )


class AttachmentsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = FSMContext(
            MemoryStorage(), StorageKey(bot_id=1, chat_id=1, user_id=1)
        )
        await self.state.set_state(AddDeadline.waiting_for_text)

    async def test_caption_and_highest_resolution_photo(self):
        ms = message(
            caption="Сдать работу до пятницы",
            photo=[
                {"file_id": "small", "file_unique_id": "s", "width": 10, "height": 10},
                {
                    "file_id": "large",
                    "file_unique_id": "l",
                    "width": 100,
                    "height": 100,
                },
            ],
        )
        self.assertTrue((~F.text.startswith("/")).resolve(ms))
        session = AsyncMock()
        session.scalar.return_value = "123-456"
        with patch("bot.handlers.add.parse_deadline", new_callable=AsyncMock) as parse:
            await receive_deadline(ms, self.state, session, [ms])
        self.assertEqual(
            (await self.state.get_data())["attachments"],
            [{"type": "photo", "file_id": "large", "file_unique_id": "l"}],
        )
        self.assertEqual(parse.call_args.args[2], ms.caption)

    async def test_file_before_text_survives_subject_clarification(self):
        file = message(document={"file_id": "doc", "file_unique_id": "d"})
        with patch.object(Message, "answer", new_callable=AsyncMock):
            await receive_deadline(file, self.state, AsyncMock(), [file])
        text = message(text="Сдать работу в пятницу")
        session = AsyncMock()
        session.scalar.return_value = "123-456"
        with patch("bot.handlers.add.parse_deadline", new_callable=AsyncMock):
            await receive_deadline(text, self.state, session, [text])
        with patch("bot.handlers.add.parse_deadline", new_callable=AsyncMock):
            await receive_subject(message(text="Физика"), self.state, session)
        self.assertEqual(
            (await self.state.get_data())["attachments"], message_attachments(file)
        )

    async def test_album_is_processed_once_and_sorted(self):
        middleware = AlbumMiddleware()
        handler = AsyncMock()
        first = message(message_id=2, media_group_id="album")
        second = message(message_id=1, media_group_id="album", caption="Задание")
        task = asyncio.create_task(middleware(handler, first, {}))
        await asyncio.sleep(0)
        await middleware(handler, second, {})
        await task
        handler.assert_awaited_once()
        self.assertEqual(handler.call_args.args[1]["album"], [second, first])
        self.assertEqual(middleware.albums, {})

    async def test_save_includes_attachments_and_legacy_default(self):
        session = AsyncMock()
        session.add = MagicMock()
        session.scalar.return_value = MagicMock(id=7)
        session.scalars.return_value = []
        files = [{"type": "document", "file_id": "doc"}]
        homework = {"date": "06-11-2026", "subject": "Физика", "homework": "Лаба"}
        self.assertEqual(
            (await save_deadline(session, "123-456", homework, files)).status, "created"
        )
        self.assertEqual(session.add.call_args.args[0].attachments, files)
        self.assertIn(
            "DEFAULT '[]'",
            str(CreateTable(Deadline.__table__).compile(dialect=postgresql.dialect())),
        )

    async def test_delivery_uses_correct_telegram_methods(self):
        bot = AsyncMock()
        deadline = Deadline(
            id=1,
            date=date(2026, 11, 6),
            subject="Физика",
            deadline="Лаба",
            attachments=[
                {"type": "photo", "file_id": "photo"},
                {"type": "document", "file_id": "doc"},
            ],
        )
        await send_deadline_attachments(bot, 42, [deadline], 10)
        bot.send_photo.assert_awaited_once_with(
            chat_id=42, photo="photo", message_thread_id=10
        )
        bot.send_document.assert_awaited_once_with(
            chat_id=42, document="doc", message_thread_id=10
        )
        self.assertIn("Физика", bot.send_message.call_args.args[1])

    async def test_old_deadline_sends_no_extra_messages(self):
        bot = AsyncMock()
        await send_deadline_attachments(bot, 42, [Deadline(attachments=[])])
        bot.send_message.assert_not_awaited()
