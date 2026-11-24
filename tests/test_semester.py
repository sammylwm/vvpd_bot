import unittest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.filters import CommandObject

from bot.handlers.semester import (
    command_semester,
    get_semester_schedule,
    render_semester,
)
from core.api import ApiError, Schedule


class SemesterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.lesson = {
            "weekday": 1,
            "pair": 2,
            "start": "10:40",
            "end": "12:10",
            "subject": "Алгебра",
            "type": "Практика",
            "rooms": ["ПК319"],
            "teacher": "Иванов",
            "date_from": "2026-09-01",
            "date_to": "2026-12-25",
        }

    def test_render_preserves_periods_and_highlights(self):
        message = render_semester("261-329", {"lessons": [self.lesson]}, owner=42)
        days = [block for block in message.blocks if block.type == "details"]
        self.assertEqual(len(days), 7)
        self.assertEqual(days[0].summary, "Понедельник · занятий: 1")
        self.assertTrue(all(block.is_open is False for block in days))
        payload = message.model_dump_json()
        for value in (
            "01.09.2026 - 25.12.2026",
            "10:40 - 12:10",
            "ПК319",
            "semester:42:close",
        ):
            self.assertIn(value, payload)
        room = next(
            block
            for block in days[0].blocks
            if block.type == "paragraph"
            and getattr(block.text, "type", None) == "bold"
            and "ПК319" in block.text.text
        )
        self.assertEqual(room.text.type, "bold")

    def test_explicit_date_determines_weekday(self):
        lesson = {**self.lesson, "weekday": None, "date": "2026-11-10"}
        message = render_semester("261-329", {"lessons": [lesson]}, owner=42)
        days = [block for block in message.blocks if block.type == "details"]
        self.assertEqual(days[1].summary, "Вторник · занятий: 1")
        self.assertIn("10.11.2026", message.model_dump_json())

    async def test_fetches_semester_and_groups_repeated_occurrences(self):
        api = AsyncMock()
        api.schedule.return_value = Schedule(
            lessons=[
                {**self.lesson, "date": "2026-11-09"},
                {**self.lesson, "date": "2026-11-16"},
            ]
        )
        result = await get_semester_schedule(api, "261-329", date(2027, 1, 15))
        api.schedule.assert_awaited_once_with(
            "261-329", date(2026, 9, 1), date(2027, 1, 31)
        )
        self.assertEqual(result["lessons"], [{**self.lesson, "date": None}])

    async def test_command_handles_api_error(self):
        message = MagicMock()
        message.from_user.id = 42
        message.answer = AsyncMock()
        message.answer_rich = AsyncMock()
        api = AsyncMock()
        api.schedule.side_effect = ApiError(503)
        with (
            patch("bot.handlers.semester.group_for_account", return_value="261-329"),
            self.assertLogs("bot.handlers.semester", level="ERROR"),
        ):
            await command_semester(message, CommandObject(command="semester"), api)
        message.answer_rich.assert_not_called()
        self.assertIn("Не удалось", message.answer.call_args.args[0])
