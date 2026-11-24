import unittest
from datetime import date
from unittest.mock import AsyncMock

from bot.handlers.deadline_buttons import deadline_button
from bot.handlers.week import get_week_schedule, render_week, week_dates
from core.api import Schedule


class ScheduleButtonsTests(unittest.IsolatedAsyncioTestCase):
    def test_week_has_six_collapsed_days_and_navigation(self):
        days = week_dates(date(2026, 11, 12))
        self.assertEqual(len(days), 6)
        self.assertEqual(days[-1], date(2026, 11, 14))
        message = render_week("261-329", days, [], owner=42)
        details = [block for block in message.blocks if block.type == "details"]
        self.assertEqual(len(details), 6)
        self.assertTrue(all(block.is_open is False for block in details))
        self.assertEqual(details[-1].summary, "Суббота, 14.11 · нет занятий")
        self.assertIn("week:42:2026-11-09:next", message.model_dump_json())

    def test_selected_day_shows_sorted_lessons_and_link(self):
        days = week_dates(date(2026, 11, 9))
        lesson = {
            "start": "10:40",
            "end": "12:10",
            "subject": "Алгебра",
            "type": "Практика",
            "teacher": "Иванов",
            "rooms": ["ПК319"],
            "url": "https://example.com/lesson",
        }
        schedule = {"date": days[0].isoformat(), "lessons": [lesson]}
        message = render_week("261-329", days, [schedule], selected=0)
        details = [block for block in message.blocks if block.type == "details"]
        self.assertEqual(len(details), 1)
        self.assertTrue(details[0].is_open)
        payload = message.model_dump_json()
        for text in (
            "Алгебра (Практика)",
            "Преподаватель: Иванов",
            "Аудитория: ПК319",
            "https://example.com/lesson",
        ):
            self.assertIn(text, payload)

    def test_button_preserves_group_and_date_range(self):
        button = deadline_button("261-329", date(2026, 11, 9), date(2026, 11, 14))
        self.assertEqual(
            button.inline_keyboard[0][0].callback_data,
            "schedule_tasks:261-329:2026-11-09:2026-11-14",
        )

    async def test_week_fetches_one_range_and_keeps_empty_days(self):
        days = week_dates(date(2026, 11, 9))
        lesson = {"date": days[1].isoformat(), "subject": "Физика"}
        api = AsyncMock()
        api.schedule.return_value = Schedule(lessons=[lesson])
        schedules = await get_week_schedule(api, "261-329", days)
        api.schedule.assert_awaited_once_with("261-329", days[0], days[-1])
        self.assertEqual(schedules[0]["lessons"], [])
        self.assertEqual(schedules[1]["lessons"], [lesson])
