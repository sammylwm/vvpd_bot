import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from ai.ai import HomeworkMerge, SubjectClarificationRequired
from ai.session_tasks import ParsedSessionTasks, parse_session_tasks
from bot.handlers.session_tasks import AddSessionTask, append_session_tasks, prepare_session_tasks
from core.modules.session_task import SessionTask


class SessionParserTests(unittest.TestCase):
    def parse(self, result, finish="stop"):
        with patch("ai.session_tasks.OpenAI") as client:
            client.return_value.__enter__.return_value.chat.completions.create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=json.dumps(result)))]
            )
            return parse_session_tasks("по тимп лабы и курсовая", ["Технологии и методы программирования"], date(2026, 11, 13))

    def test_parses_array_and_optional_dates(self):
        result = self.parse({"subject": "Технологии и методы программирования", "tasks": [
            {"text": "Сдать лабораторные 1-4", "due_date": None},
            {"text": "Защитить курсовую", "due_date": "2026-12-20"},
        ]})
        self.assertEqual(len(result.tasks), 2)
        self.assertEqual(result.tasks[1].due_date, "2026-12-20")

    def test_unknown_subject_requires_clarification(self):
        with self.assertRaises(SubjectClarificationRequired):
            self.parse({"subject": "", "tasks": [{"text": "Сдать лабораторные"}]})

    def test_invalid_outputs_rejected(self):
        for tasks in ([], [{"text": " "}], [{"text": "Сдать", "due_date": "2026-02-31"}], [{"text": 123}]):
            with self.subTest(tasks=tasks), self.assertRaises(ValueError):
                self.parse({"subject": "Технологии и методы программирования", "tasks": tasks})
        with self.assertRaises(ValueError):
            self.parse({"subject": "Технологии и методы программирования", "tasks": [{"text": "Сдать"}]}, finish="length")


class SessionAppendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def run_inline(function, *args):
            return function(*args)

        worker = patch("bot.handlers.session_tasks.asyncio.to_thread", side_effect=run_inline)
        worker.start()
        self.addCleanup(worker.stop)
        self.session = AsyncMock()
        self.session.add = MagicMock()
        self.session.scalar.return_value = SimpleNamespace(id=7)
        self.old = SessionTask(id=1, group_id=7, semester="2026-2", subject="Алгебра", text="Сдать расчёт", due_date=date(2026, 12, 1))
        self.session.scalars.return_value = [self.old]

    def parsed(self, *tasks):
        return ParsedSessionTasks.model_validate({"subject": "Алгебра", "tasks": [{"text": text} for text in tasks]})

    async def test_appends_to_same_subject_and_deduplicates_batch(self):
        with patch("bot.handlers.session_tasks.reconcile_homework", return_value=HomeworkMerge(match_index=None, merged_homework="")):
            result = await append_session_tasks(self.session, 7, "2026-2", self.parsed("Сдать расчёт", "Закрыть контрольную", "Закрыть контрольную"))
        self.assertEqual(result, (1, 0))
        self.session.add.assert_called_once()
        self.assertEqual(self.session.add.call_args.args[0].subject, "Алгебра")
        self.assertEqual(self.old.due_date, date(2026, 12, 1))
        self.assertIn("FOR UPDATE", str(self.session.scalar.call_args.args[0]))

    async def test_enriches_existing_task(self):
        with patch("bot.handlers.session_tasks.reconcile_homework", return_value=HomeworkMerge(match_index=0, merged_homework="Сдать расчёт в PDF")):
            result = await append_session_tasks(self.session, 7, "2026-2", self.parsed("Расчёт нужен в PDF"))
        self.assertEqual(result, (0, 1))
        self.assertEqual(self.old.text, "Сдать расчёт в PDF")
        self.session.add.assert_not_called()

    async def test_other_subject_is_not_merged(self):
        self.old.subject = "Физика"
        with patch("bot.handlers.session_tasks.reconcile_homework") as compare:
            result = await append_session_tasks(self.session, 7, "2026-2", self.parsed("Сдать расчёт"))
        compare.assert_not_called()
        self.assertEqual(result, (1, 0))

    async def test_ai_failure_rolls_back_entire_batch(self):
        with patch("bot.handlers.session_tasks.reconcile_homework", side_effect=ValueError("Unavailable")):
            with self.assertRaises(ValueError):
                await append_session_tasks(self.session, 7, "2026-2", self.parsed("Уточнение"))
        self.session.rollback.assert_awaited_once()
        self.session.commit.assert_not_awaited()

    async def test_due_date_is_updated_only_when_provided(self):
        parsed = ParsedSessionTasks.model_validate({"subject": "Алгебра", "tasks": [{"text": "Сдать расчёт", "due_date": "2026-12-20"}]})
        result = await append_session_tasks(self.session, 7, "2026-2", parsed)
        self.assertEqual(result, (0, 1))
        self.assertEqual(self.old.due_date, date(2026, 12, 20))

    async def test_preview_requires_confirmation_and_does_not_save(self):
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=42, user_id=42))
        await state.update_data(group_id=7, semester="2026-2")
        message = MagicMock()
        message.from_user.id = 42
        message.answer = AsyncMock()
        self.session.scalar.return_value = SimpleNamespace(id=7, name="261-329")
        self.session.scalars.return_value = []
        with patch("bot.handlers.session_tasks.get_semester_schedule", return_value={"lessons": [{"subject": "Алгебра"}]}), patch(
            "bot.handlers.session_tasks.parse_session_tasks", return_value=self.parsed("Сдать расчёт", "Закрыть контрольную")
        ):
            await prepare_session_tasks(message, state, self.session, "по алгебре расчёт и контрольная")
        self.assertEqual(await state.get_state(), AddSessionTask.confirmation.state)
        self.assertEqual(len((await state.get_data())["parsed"]["tasks"]), 2)
        self.session.commit.assert_not_awaited()
        self.assertIn("session_save:", str(message.answer.call_args.kwargs["reply_markup"]))

    async def test_unknown_subject_keeps_text_for_clarification(self):
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=42, user_id=42))
        await state.update_data(group_id=7, semester="2026-2")
        message = MagicMock()
        message.from_user.id = 42
        message.answer = AsyncMock()
        self.session.scalar.return_value = SimpleNamespace(id=7, name="261-329")
        self.session.scalars.return_value = []
        with patch("bot.handlers.session_tasks.get_semester_schedule", return_value={"lessons": [{"subject": "Алгебра"}]}), patch(
            "bot.handlers.session_tasks.parse_session_tasks", side_effect=SubjectClarificationRequired()
        ):
            await prepare_session_tasks(message, state, self.session, "сдать расчёт")
        self.assertEqual(await state.get_state(), AddSessionTask.subject.state)
        self.assertEqual((await state.get_data())["raw_text"], "сдать расчёт")
        self.session.commit.assert_not_awaited()

    async def test_stale_confirmation_cannot_save(self):
        from bot.handlers.session_tasks import confirm_session_tasks
        state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=42, user_id=42))
        await state.set_state(AddSessionTask.confirmation)
        await state.update_data(token="new")
        callback = MagicMock(data="session_save:old", answer=AsyncMock())
        await confirm_session_tasks(callback, state, self.session)
        self.session.commit.assert_not_awaited()
        self.assertIn("неактуально", callback.answer.call_args.args[0])
