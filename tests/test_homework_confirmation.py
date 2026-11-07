import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.add import AddDeadline, confirm_deadline, parse_deadline
from bot.handlers.delete import delete_deadline, router as delete_router
from aiogram.types import Message


class HomeworkConfirmationTests(unittest.IsolatedAsyncioTestCase):
    def test_delete_buttons_reach_handler(self):
        button_filter = delete_router.callback_query.handlers[0].filters[0].callback
        for data in ("delete_ask:123", "delete_yes:123", "delete_no"):
            with self.subTest(data=data):
                self.assertTrue(button_filter(SimpleNamespace(data=data)))
        for data in ("profile:123", "add_yes:123", None):
            with self.subTest(data=data):
                self.assertFalse(button_filter(SimpleNamespace(data=data)))

    async def test_parsing_only_previews_without_saving(self):
        ms = AsyncMock()
        state = AsyncMock()
        state.get_data.return_value = {"attachments": [{"file_id": "photo"}]}
        homework = {"date": "10-11-2026", "subject": "Физика", "homework": "Задача 1"}
        with patch("bot.handlers.add.asyncio.to_thread", new=AsyncMock(return_value=homework)), patch("bot.handlers.add.save_deadline", new=AsyncMock()) as save:
            await parse_deadline(ms, state, "text", "123", AsyncMock())
        save.assert_not_awaited()
        state.set_state.assert_awaited_once_with(AddDeadline.waiting_for_confirmation)
        self.assertIn("Задача 1", ms.answer.call_args.args[0])

    async def test_stale_confirmation_cannot_save(self):
        callback = AsyncMock()
        callback.data = "add_yes:old"
        state = AsyncMock()
        state.get_data.return_value = {"confirmation_token": "new"}
        state.get_state.return_value = AddDeadline.waiting_for_confirmation.state
        with patch("bot.handlers.add.save_deadline", new=AsyncMock()) as save:
            await confirm_deadline(callback, state, AsyncMock())
        save.assert_not_awaited()
        state.clear.assert_not_awaited()

    async def test_delete_confirmation_restricts_query_to_user_group(self):
        ms = AsyncMock(spec=Message)
        ms.answer = AsyncMock()
        ms.edit_reply_markup = AsyncMock()
        callback = AsyncMock()
        callback.message = ms
        callback.data = "delete_yes:123"
        callback.from_user = SimpleNamespace(id=789)
        session = AsyncMock()
        # SQLAlchemy results are synchronous after the awaited execute.
        from unittest.mock import MagicMock
        session.execute.return_value = MagicMock()
        session.execute.return_value.scalar_one_or_none.return_value = None
        await delete_deadline(callback, session)
        query = session.execute.call_args.args[0]
        sql = str(query)
        self.assertIn("deadlines.group_id = (SELECT users.group_id", sql)
        self.assertEqual(query.compile().params["telegram_id_1"], 789)
        session.commit.assert_awaited_once()
        self.assertIn("недоступно", ms.answer.call_args.args[0])
