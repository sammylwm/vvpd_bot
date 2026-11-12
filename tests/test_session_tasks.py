import unittest
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from bot.handlers.session_tasks import (
    AddSessionTask, current_semester, receive_session_date, render_session,
    save_session_task, set_session_completion, skip_session_date,
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

    def test_render_personal_progress_and_optional_date(self):
        tasks = [SessionTask(id=1, group_id=7, semester="2026-2", subject="Алгебра", text="Сдать расчёт", due_date=None),
                 SessionTask(id=2, group_id=7, semester="2026-2", subject="Алгебра", text="Закрыть контрольную", due_date=date(2026, 12, 20))]
        first = render_session("261-329", "2026-2", tasks, {1}, 42).model_dump_json()
        second = render_session("261-329", "2026-2", tasks, set(), 43).model_dump_json()
        for value in ("Выполнено: 1 из 2", "Без срока", "20.12.2026", "session_set:42:2026-2:1:0", "session_set:42:2026-2:2:1"):
            self.assertIn(value, first)
        self.assertIn("Выполнено: 0 из 2", second)

    async def test_invalid_date_keeps_state(self):
        await self.state.set_state(AddSessionTask.due_date)
        self.message.text = "31.02.2026"
        await receive_session_date(self.message, self.state, self.session)
        self.assertEqual(await self.state.get_state(), AddSessionTask.due_date.state)
        self.session.add.assert_not_called()

    async def test_save_no_date_clears_state(self):
        await self.state.update_data(group_id=7, semester="2026-2", subject="Алгебра", text="Сдать расчёт")
        self.session.scalar.return_value = MagicMock(id=7)
        await save_session_task(self.message, 42, self.state, self.session, None)
        task = self.session.add.call_args.args[0]
        self.assertIsNone(task.due_date)
        self.assertEqual(task.group_id, 7)
        self.session.commit.assert_awaited_once()
        self.assertIsNone(await self.state.get_state())

    async def test_changed_group_cannot_save(self):
        await self.state.update_data(group_id=7)
        self.session.scalar.return_value = MagicMock(id=8)
        await save_session_task(self.message, 42, self.state, self.session, None)
        self.session.add.assert_not_called()
        self.assertIsNone(await self.state.get_state())

    async def test_stale_skip_cannot_save(self):
        callback = MagicMock(data="session_skip:old", answer=AsyncMock())
        await self.state.set_state(AddSessionTask.due_date)
        await self.state.update_data(token="new")
        await skip_session_date(callback, self.state, self.session)
        self.session.add.assert_not_called()

    async def test_other_person_cannot_change_progress(self):
        from aiogram.types import Message
        callback = MagicMock(data="session_set:42:2026-2:1:1", answer=AsyncMock())
        callback.message = MagicMock(spec=Message)
        callback.from_user.id = 43
        await set_session_completion(callback, self.session)
        self.session.scalar.assert_not_awaited()
        self.assertIn("чужой прогресс", callback.answer.call_args.args[0])

    async def test_completion_saves_actual_person_id(self):
        from unittest.mock import patch
        from aiogram.types import Message
        callback = MagicMock(data="session_set:42:2026-2:1:1", answer=AsyncMock())
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        callback.from_user.id = 42
        self.session.scalar.side_effect = [MagicMock(id=7), MagicMock(id=1)]
        self.session.execute = AsyncMock()
        with patch("bot.handlers.session_tasks.session_view", new=AsyncMock(return_value=MagicMock())):
            await set_session_completion(callback, self.session)
        statement = self.session.execute.call_args.args[0]
        self.assertEqual(statement.compile().params["telegram_id"], 42)
        self.assertIn("ON CONFLICT", str(statement))
        self.session.commit.assert_awaited_once()
        callback.message.edit_text.assert_awaited_once()

    async def test_save_failure_preserves_input(self):
        await self.state.set_state(AddSessionTask.due_date)
        await self.state.update_data(group_id=7, semester="2026-2", subject="Алгебра", text="Сдать расчёт")
        self.session.scalar.return_value = MagicMock(id=7)
        self.session.commit.side_effect = RuntimeError("Database unavailable")
        with self.assertLogs("bot.handlers.session_tasks", level="ERROR"):
            await save_session_task(self.message, 42, self.state, self.session, None)
        self.session.rollback.assert_awaited_once()
        self.assertEqual(await self.state.get_state(), AddSessionTask.due_date.state)
