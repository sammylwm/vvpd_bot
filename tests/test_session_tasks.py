import unittest
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from bot.handlers.session_tasks import (
    AddSessionTask, current_semester, render_session, obsolete_session_completion,
)
from core.modules.session_task import SessionTask


class SessionTasksTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=42, user_id=42))
        self.message = MagicMock()
        self.message.from_user.id = 42
        self.message.answer = AsyncMock()
        self.session = MagicMock()
        self.session.scalar = AsyncMock()
        self.session.commit = AsyncMock()
        self.session.rollback = AsyncMock()

    def test_semester_boundaries(self):
        for day, expected in ((date(2027, 1, 31), "2026-2"), (date(2027, 2, 1), "2027-1"),
                              (date(2027, 8, 31), "2027-1"), (date(2027, 9, 1), "2027-2")):
            self.assertEqual(current_semester(day), expected)

    def test_render_tasks_without_completion_controls(self):
        tasks = [SessionTask(id=1, group_id=7, semester="2026-2", subject="Алгебра", text="Сдать расчёт", due_date=None),
                 SessionTask(id=2, group_id=7, semester="2026-2", subject="Алгебра", text="Закрыть контрольную", due_date=date(2026, 12, 20))]
        payload = render_session("261-329", "2026-2", tasks, 42).model_dump_json()
        for value in ("Сдать расчёт", "Закрыть контрольную", "Без срока", "20.12.2026", "session_delete:ask:42:2026-2:1"):
            self.assertIn(value, payload)
        for value in ("Выполнено", "Вернуть в работу", "Отметить выполненным", "session_set:"):
            self.assertNotIn(value, payload)

    async def test_old_completion_button_only_shows_notice(self):
        callback = MagicMock(answer=AsyncMock())
        await obsolete_session_completion(callback)
        self.assertIn("убраны", callback.answer.call_args.args[0])

    async def test_delete_requires_confirmation(self):
        from aiogram.types import Message
        from bot.handlers.session_tasks import delete_session_task
        callback = MagicMock(data="session_delete:ask:42:2026-2:1", answer=AsyncMock())
        callback.from_user.id = 42
        callback.message = MagicMock(spec=Message)
        callback.message.answer = AsyncMock()
        self.session.scalar.side_effect = [MagicMock(id=7), SessionTask(subject="Алгебра", text="Сдать расчёт")]
        self.session.execute = AsyncMock()
        await delete_session_task(callback, self.session)
        self.session.execute.assert_not_awaited()
        buttons = callback.message.answer.call_args.kwargs["reply_markup"].inline_keyboard[0]
        self.assertEqual(buttons[0].callback_data, "session_delete:yes:42:2026-2:1")
        self.assertIn("для всей группы", callback.message.answer.call_args.args[0])

    async def test_delete_limits_to_current_group_and_semester(self):
        from unittest.mock import patch
        from aiogram.types import Message
        from bot.handlers.session_tasks import delete_session_task
        callback = MagicMock(data="session_delete:yes:42:2026-2:1", answer=AsyncMock())
        callback.from_user.id = 42
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        callback.message.answer_rich = AsyncMock()
        self.session.scalar.return_value = MagicMock(id=7)
        result = MagicMock()
        result.scalar_one_or_none.return_value = 1
        self.session.execute = AsyncMock(return_value=result)
        with patch("bot.handlers.session_tasks.session_view", new=AsyncMock(return_value=MagicMock())):
            await delete_session_task(callback, self.session)
        params = self.session.execute.call_args.args[0].compile().params
        self.assertEqual(set(params.values()), {1, 7, "2026-2"})
        self.session.commit.assert_awaited_once()
        callback.message.answer_rich.assert_awaited_once()

    async def test_other_user_cannot_confirm_deletion(self):
        from aiogram.types import Message
        from bot.handlers.session_tasks import delete_session_task
        callback = MagicMock(data="session_delete:yes:42:2026-2:1", answer=AsyncMock())
        callback.from_user.id = 43
        callback.message = MagicMock(spec=Message)
        await delete_session_task(callback, self.session)
        self.session.scalar.assert_not_awaited()
        self.session.commit.assert_not_awaited()

    async def test_cancel_delete_does_not_touch_database(self):
        from aiogram.types import Message
        from bot.handlers.session_tasks import delete_session_task
        callback = MagicMock(data="session_delete:no:42:2026-2:1", answer=AsyncMock())
        callback.from_user.id = 42
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        await delete_session_task(callback, self.session)
        self.session.scalar.assert_not_awaited()
        self.session.commit.assert_not_awaited()
