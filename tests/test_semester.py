import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.filters import CommandObject

from bot.handlers.semester import command_semester, get_semester_schedule, render_semester


class SemesterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.lesson = {
            "weekday": 1, "start": "10:40", "end": "12:10", "subject": "Алгебра",
            "type": "Практика", "rooms": ["ПК319"], "teacher": "Иванов",
            "date_from": "2026-09-01", "date_to": "2026-12-25",
        }

    def test_render_preserves_periods_and_highlights(self):
        message = render_semester("261-329", {"lessons": [self.lesson]}, owner=42)
        days = [block for block in message.blocks if block.type == "details"]
        self.assertEqual(len(days), 6)
        self.assertEqual(days[0].summary, "Понедельник · занятий: 1")
        self.assertTrue(all(block.is_open is False for block in days))
        payload = message.model_dump_json()
        for value in ("01.09.2026 - 25.12.2026", "10:40 - 12:10", "ПК319", "semester:42:close"):
            self.assertIn(value, payload)
        room = next(block for block in days[0].blocks if block.type == "paragraph" and getattr(block.text, "type", None) == "bold" and "ПК319" in block.text.text)
        self.assertEqual(room.text.type, "bold")

    def test_explicit_date_determines_weekday(self):
        lesson = {**self.lesson, "date": "2026-11-10"}
        message = render_semester("261-329", {"lessons": [lesson]}, owner=42)
        days = [block for block in message.blocks if block.type == "details"]
        self.assertEqual(days[1].summary, "Вторник · занятий: 1")
        self.assertIn("10.11.2026", message.model_dump_json())

    def test_fetch_uses_full_schedule_endpoint(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"lessons": [self.lesson]}).encode()
        with patch("bot.handlers.semester.urlopen", return_value=response) as fetch:
            result = get_semester_schedule("261-329", schedule_base_url="https://example.com/")
        fetch.assert_called_once_with("https://example.com/schedule/261-329", timeout=60)
        self.assertEqual(result["lessons"], [self.lesson])

    async def test_command_handles_api_error(self):
        message = MagicMock()
        message.from_user.id = 42
        message.answer = AsyncMock()
        message.answer_rich = AsyncMock()
        session = MagicMock()
        session.scalar = AsyncMock(return_value=MagicMock(name="261-329"))
        with patch("bot.handlers.semester.account_id", return_value=42), patch(
            "bot.handlers.semester.asyncio.to_thread", new=AsyncMock(side_effect=ValueError("Invalid response"))
        ), self.assertLogs("bot.handlers.semester", level="ERROR"):
            await command_semester(message, CommandObject(command="semester"), session)
        message.answer_rich.assert_not_called()
        self.assertIn("Не удалось", message.answer.call_args.args[0])
