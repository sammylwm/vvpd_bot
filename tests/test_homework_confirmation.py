import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import Message

from bot.handlers.add import AddDeadline, confirm_deadline, receive_deadline
from bot.handlers.delete import delete_deadline
from core.api import ApiError, Confirmation, Deadline, DeadlinePreview


class HomeworkConfirmationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.message = AsyncMock(spec=Message)
        self.message.answer = AsyncMock()
        self.message.edit_reply_markup = AsyncMock()
        self.message.text = "Задача 1"
        self.message.caption = None
        self.message.photo = None
        self.message.document = None
        self.message.from_user = SimpleNamespace(id=42)
        self.preview = DeadlinePreview(
            group="261-329",
            date=date(2026, 11, 10),
            subject="Физика",
            deadline="Задача 1",
        )
        self.api = AsyncMock()
        self.api.parse.return_value = self.preview
        self.state = AsyncMock()
        self.state.get_data.return_value = {}

    def callback(self, data):
        return SimpleNamespace(
            message=self.message,
            data=data,
            from_user=self.message.from_user,
            answer=AsyncMock(),
        )

    async def test_parse_previews_before_confirming(self):
        with patch("bot.handlers.add.group_for_account", return_value="261-329"):
            await receive_deadline(self.message, self.state, self.api)
            self.api.parse.assert_awaited_once_with("261-329", "Задача 1")
            self.api.confirm.assert_not_awaited()
            self.state.set_state.assert_awaited_once_with(
                AddDeadline.waiting_for_confirmation
            )
            self.assertIn("Задача 1", self.message.answer.call_args.args[0])

            data = self.state.update_data.call_args.kwargs
            self.state.get_data.return_value = data
            self.state.get_state.return_value = (
                AddDeadline.waiting_for_confirmation.state
            )
            self.api.confirm.return_value = Confirmation(
                status="updated", deadline=Deadline(id=123, **self.preview.model_dump())
            )
            await confirm_deadline(
                self.callback(f"add_yes:{data['confirmation_token']}"),
                self.state,
                self.api,
            )
        self.api.confirm.assert_awaited_once_with(self.preview)
        self.state.clear.assert_awaited_once()
        self.assertIn("Дедлайн обновлён", self.message.answer.call_args.args[0])

    async def test_clarification_reuses_original_text(self):
        self.api.parse.side_effect = ApiError(422, "Уточни предмет")
        with patch("bot.handlers.add.group_for_account", return_value="261-329"):
            await receive_deadline(self.message, self.state, self.api)
            self.state.get_data.return_value = self.state.update_data.call_args.kwargs
            self.message.text = "Физика"
            self.api.parse.side_effect = None
            await receive_deadline(self.message, self.state, self.api)
        self.api.parse.assert_awaited_with("261-329", "Задача 1\nУточнение: Физика")

    async def test_stale_confirmation_cannot_save(self):
        self.state.get_data.return_value = {"confirmation_token": "new"}
        self.state.get_state.return_value = AddDeadline.waiting_for_confirmation.state
        await confirm_deadline(self.callback("add_yes:old"), self.state, self.api)
        self.api.confirm.assert_not_awaited()
        self.state.clear.assert_not_awaited()

    async def test_delete_confirmation_checks_the_current_group(self):
        self.api.deadlines.return_value = []
        with patch("bot.handlers.delete.group_for_account", return_value="261-329"):
            await delete_deadline(self.callback("delete_yes:123:2026-11-10"), self.api)
        self.api.deadlines.assert_awaited_once_with(
            "261-329", self.preview.date, self.preview.date
        )
        self.api.delete.assert_not_awaited()
        self.assertIn("недоступно", self.message.answer.call_args.args[0])

    async def test_conflict_offers_explicit_confirmation_without_files(self):
        self.message.bot = SimpleNamespace(download=AsyncMock())
        self.state.get_data.return_value = {
            "confirmation_token": "token",
            "preview": self.preview.model_dump(mode="json"),
            "attachments": [
                {
                    "file_id": "file-1",
                    "name": "report.pdf",
                    "content_type": "application/pdf",
                }
            ],
        }
        self.state.get_state.return_value = AddDeadline.waiting_for_confirmation.state
        self.api.confirm.side_effect = ApiError(409, "Дедлайн уже существует.")

        with patch("bot.handlers.add.group_for_account", return_value="261-329"):
            await confirm_deadline(self.callback("add_yes:token"), self.state, self.api)
            self.message.bot.download.assert_awaited_once()
            self.state.clear.assert_not_awaited()
            self.assertEqual(
                self.api.confirm.await_args.args[1][0][:2],
                ("report.pdf", "application/pdf"),
            )
            self.assertTrue(self.api.confirm.await_args.args[1][0][2].closed)
            buttons = self.message.edit_reply_markup.await_args.kwargs[
                "reply_markup"
            ].inline_keyboard[0]
            self.assertEqual(buttons[0].callback_data, "add_text:token")

            self.api.confirm.side_effect = None
            self.api.confirm.return_value = Confirmation(
                status="unchanged",
                deadline=Deadline(id=123, **self.preview.model_dump()),
            )
            await confirm_deadline(
                self.callback("add_text:token"), self.state, self.api
            )

        self.api.confirm.assert_awaited_with(self.preview)
        self.state.clear.assert_awaited_once()
