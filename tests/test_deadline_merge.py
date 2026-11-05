import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from ai.ai import HomeworkMerge, reconcile_homework
from bot.handlers.add import save_deadline
from core.modules.deadline import Deadline


class DeadlineMergeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Run the mocked AI call inline; these unit tests exercise database decisions.
        async def run_inline(function, *args):
            return function(*args)

        worker = patch("bot.handlers.add.asyncio.to_thread", side_effect=run_inline)
        worker.start()
        self.addCleanup(worker.stop)
        self.old_file = {"type": "document", "file_id": "old", "file_unique_id": "same"}
        self.deadline = Deadline(
            id=1,
            group_id=7,
            date=date(2026, 11, 6),
            subject="Физика",
            deadline="Лабораторная работа № 1",
            attachments=[self.old_file],
        )
        self.homework = {
            "date": "06-11-2026",
            "subject": "Физика",
            "homework": "Лабораторная работа № 1",
        }
        self.session = AsyncMock()
        self.session.add = MagicMock()
        self.session.scalar.return_value = SimpleNamespace(id=7)
        self.session.scalars.return_value = [self.deadline]

    async def test_exact_duplicate_adds_new_files_without_ai(self):
        new_file = {"type": "photo", "file_id": "new"}
        with patch("bot.handlers.add.reconcile_homework") as compare:
            result = await save_deadline(
                self.session, "123-456", self.homework, [new_file]
            )
        self.assertEqual(result.status, "updated")
        self.assertEqual(self.deadline.attachments, [self.old_file, new_file])
        compare.assert_not_called()
        self.session.add.assert_not_called()
        self.session.commit.assert_awaited_once()

    async def test_same_file_with_another_file_id_is_not_added_again(self):
        result = await save_deadline(
            self.session,
            "123-456",
            self.homework,
            [
                {"type": "document", "file_id": "other-id", "file_unique_id": "same"},
                self.old_file,
            ],
        )
        self.assertEqual(result.status, "unchanged")
        self.assertEqual(self.deadline.attachments, [self.old_file])

    async def test_ai_enriches_correct_assignment_and_keeps_files(self):
        other = Deadline(
            subject="Физика", deadline="Лабораторная работа № 2", attachments=[]
        )
        self.session.scalars.return_value = [other, self.deadline]
        self.homework["homework"] += ", с отчётом в PDF"
        merged = self.homework["homework"]
        with patch(
            "bot.handlers.add.reconcile_homework",
            return_value=HomeworkMerge(
                match_index=1,
                merged_homework=merged,
            ),
        ):
            result = await save_deadline(self.session, "123-456", self.homework)
        self.assertEqual(result.status, "updated")
        self.assertEqual(self.deadline.deadline, merged)
        self.assertEqual(self.deadline.attachments, [self.old_file])
        self.assertEqual(other.deadline, "Лабораторная работа № 2")
        self.session.add.assert_not_called()

    async def test_paraphrase_without_new_facts_is_unchanged(self):
        self.homework["homework"] = "Сдать первую лабораторную"
        with patch(
            "bot.handlers.add.reconcile_homework",
            return_value=HomeworkMerge(
                match_index=0,
                merged_homework=self.deadline.deadline,
            ),
        ):
            result = await save_deadline(self.session, "123-456", self.homework)
        self.assertEqual(result.status, "unchanged")

    async def test_different_assignment_creates_new_deadline(self):
        self.homework["homework"] = "Лабораторная работа № 2"
        with patch(
            "bot.handlers.add.reconcile_homework",
            return_value=HomeworkMerge(
                match_index=None,
                merged_homework="",
            ),
        ):
            result = await save_deadline(self.session, "123-456", self.homework)
        self.assertEqual(result.status, "created")
        self.session.add.assert_called_once()
        self.assertEqual(self.deadline.deadline, "Лабораторная работа № 1")

    async def test_comparison_failure_rolls_back_without_changes(self):
        self.homework["homework"] += " с отчётом"
        with patch(
            "bot.handlers.add.reconcile_homework", side_effect=ValueError("invalid AI")
        ), self.assertRaises(ValueError):
            await save_deadline(self.session, "123-456", self.homework)
        self.session.rollback.assert_awaited_once()
        self.session.commit.assert_not_awaited()
        self.assertEqual(self.deadline.deadline, "Лабораторная работа № 1")


class AIComparisonTests(unittest.TestCase):
    def compare(self, content):
        with patch("ai.ai.OpenAI") as client:
            client.return_value.__enter__.return_value.chat.completions.create.return_value = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop", message=SimpleNamespace(content=content)
                    )
                ]
            )
            return reconcile_homework("Новый текст", ["Старый текст"])

    def test_valid_merge(self):
        self.assertEqual(
            self.compare(
                '{"match_index":0,"merged_homework":"Общий текст"}'
            ).match_index,
            0,
        )

    def test_invalid_or_incomplete_results_are_rejected(self):
        for content in [
            '{"match_index":2,"merged_homework":"Текст"}',
            '{"match_index":-1,"merged_homework":"Текст"}',
            '{"match_index":0,"merged_homework":" "}',
            '{"match_index":null,"merged_homework":"Текст"}',
            '{"match_index":true,"merged_homework":"Текст"}',
            '{"duplicate":true}',
        ]:
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.compare(content)
