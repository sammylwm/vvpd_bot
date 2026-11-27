import asyncio
import unittest
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat, Document, Message, Update, User

from bot.attachments import AlbumMiddleware, send_attachment_links
from bot.handlers.add import receive_deadline
from core.api import Deadline, DeadlinePreview


def message(message_id: int, *, album: str | None = None, text: str | None = None):
    return Message(
        message_id=message_id,
        date=datetime.now(UTC),
        chat=Chat(id=123, type="private"),
        from_user=User(id=123, is_bot=False, first_name="Test"),
        media_group_id=album,
        document=Document(
            file_id=f"file-{message_id}",
            file_unique_id=f"unique-{message_id}",
            file_name=f"report-{message_id}.pdf",
            mime_type="application/pdf",
            file_size=100,
        )
        if album
        else None,
        text=text,
    )


class AttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_album_is_sorted_and_following_text_waits(self):
        middleware = AlbumMiddleware()
        collected = asyncio.Event()
        ready = asyncio.Event()
        handler = AsyncMock()

        async def collect(delay: float):
            collected.set()
            await ready.wait()

        with patch("bot.attachments.asyncio.sleep", side_effect=collect):
            first = asyncio.create_task(
                middleware(
                    handler, Update(update_id=1, message=message(2, album="x")), {}
                )
            )
            await collected.wait()
            await middleware(
                handler, Update(update_id=2, message=message(1, album="x")), {}
            )
            text = asyncio.create_task(
                middleware(
                    handler, Update(update_id=3, message=message(3, text="Задача")), {}
                )
            )
            handler.assert_not_awaited()
            ready.set()
            await asyncio.gather(first, text)

        self.assertEqual(
            [item.message_id for item in handler.await_args_list[0].args[1]["album"]],
            [1, 2],
        )
        self.assertEqual(handler.await_args_list[1].args[0].message.text, "Задача")

    async def test_file_is_retained_until_text_arrives(self):
        state = AsyncMock()
        state.get_data.return_value = {}
        api = AsyncMock()
        api.parse.return_value = DeadlinePreview(
            group="261-329", date=date(2026, 11, 19), subject="Физика", deadline="Отчёт"
        )
        file_message = message(1, album="x")
        with (
            patch("bot.handlers.add.group_for_account", return_value="261-329"),
            patch.object(Message, "answer", new_callable=AsyncMock),
        ):
            await receive_deadline(file_message, state, api)
            api.parse.assert_not_awaited()
            state.get_data.return_value = state.update_data.call_args.kwargs
            self.assertEqual(
                state.get_data.return_value["attachments"][0]["file_id"], "file-1"
            )
            await receive_deadline(message(2, text="Отчёт"), state, api)
        api.parse.assert_awaited_once_with("261-329", "Отчёт")
        self.assertEqual(
            state.update_data.await_args_list[-2].kwargs["attachments"][0]["name"],
            "report-1.pdf",
        )

    async def test_shows_api_links_with_escaped_names(self):
        task = Deadline(
            id=42,
            group="261-329",
            date=date(2026, 11, 19),
            subject="Физика",
            deadline="Отчёт",
            attachments=[
                {"name": "a&b.pdf", "size": 3, "url": "https://example.com/attachment/"}
            ],
        )
        with (
            patch.object(Message, "answer", new_callable=AsyncMock) as answer,
            patch.object(
                Message,
                "answer_document",
                new_callable=AsyncMock,
                side_effect=ValueError("invalid document"),
            ),
        ):
            await send_attachment_links(message(1), [task])
        self.assertIn(
            '<a href="https://example.com/attachment/">a&amp;b.pdf</a>',
            answer.await_args.args[0],
        )
