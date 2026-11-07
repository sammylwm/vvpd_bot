import unittest
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

from bot.handlers.day import render_day
from bot.handlers.week import render_week
from bot.handlers.deadline_buttons import deadline_button
from bot.reminders import build_reminder_payload
from core.modules.deadline import Deadline


class ScheduleButtonsTests(unittest.IsolatedAsyncioTestCase):
    def test_schedule_renderers_hide_assignment_text(self):
        day = date(2026, 11, 9)
        task = Deadline(date=day, subject="Физика", deadline="СЕКРЕТНОЕ ДЗ")
        schedule = {"date": day.isoformat(), "lessons": []}
        days = [day + timedelta(days=i) for i in range(6)]
        for text in (render_day("123-456", schedule, [task]),
                     render_week("123-456", days, [schedule], [task])):
            self.assertNotIn("СЕКРЕТНОЕ", text)
            self.assertNotIn("Заданий:", text)

    def test_button_preserves_group_and_date_range(self):
        button = deadline_button(7, date(2026, 11, 9), date(2026, 11, 14))
        self.assertEqual(button.inline_keyboard[0][0].callback_data,
                         "schedule_tasks:7:2026-11-09:2026-11-14")

    async def test_reminder_has_only_schedule_and_no_attachments(self):
        schedule = {"date": "2026-11-09", "lessons": []}
        with patch("bot.reminders.asyncio.to_thread", new=AsyncMock(return_value=schedule)):
            messages, tasks = await build_reminder_payload(7, "123-456", date(2026, 11, 9))
        self.assertEqual(len(messages), 1)
        self.assertEqual(tasks, [])
        self.assertNotIn("Дедлайны на завтра", messages[0])
