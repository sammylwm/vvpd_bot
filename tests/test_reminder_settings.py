import unittest
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.profile import change_time, render_profile
from bot.preferences import ProfileSettings, TopicSettings, preferences
from bot.reminders import MOSCOW, send_reminders
from core.api import Schedule


class ReminderSettingsTests(unittest.IsolatedAsyncioTestCase):
    def test_profile_has_time_and_status_in_one_row(self):
        user = ProfileSettings(
            1, "123-456", reminders_enabled=False, reminder_time=time(9, 30)
        )
        buttons = render_profile(user).blocks[-1].buttons
        self.assertEqual(len(buttons), 2)
        self.assertIn("09:30", buttons[0].text)
        self.assertIn("Выключено", buttons[1].text)

    async def test_invalid_time_keeps_waiting(self):
        for value in ("24:00", "12:60", "9:30", "hello", ""):
            message = SimpleNamespace(
                text=value, from_user=SimpleNamespace(id=1), answer=AsyncMock()
            )
            state = SimpleNamespace(clear=AsyncMock())
            await change_time(message, state)
            message.answer.assert_awaited_once()
            state.clear.assert_not_awaited()

    async def test_time_changes_without_enabling_notifications(self):
        user = ProfileSettings(1, "123-456", reminders_enabled=False)
        message = SimpleNamespace(
            text="09:30", from_user=SimpleNamespace(id=1), answer_rich=AsyncMock()
        )
        state = SimpleNamespace(clear=AsyncMock())
        with (
            patch.object(preferences, "profile", return_value=user),
            patch.object(preferences, "update_profile") as update,
        ):
            await change_time(message, state)
        update.assert_awaited_once_with(1, reminder_time=time(9, 30))
        state.clear.assert_awaited_once()

    async def test_broadcast_reuses_schedule_and_sends_group_reminder_to_topic(self):
        user = ProfileSettings(1, "123-456")
        group = ProfileSettings(-100456, "123-456")
        bot = SimpleNamespace(send_rich_message=AsyncMock())
        api = AsyncMock()
        api.schedule.return_value = Schedule(lessons=[])
        day = date(2026, 11, 9)
        with (
            patch("bot.reminders.datetime") as clock,
            patch.object(
                preferences, "due_profiles", side_effect=[[user, group], []]
            ) as due,
            patch.object(preferences, "topic", return_value=TopicSettings(topic_id=77)),
            patch.object(preferences, "update_profile") as update,
            patch("bot.reminders.asyncio.sleep", new=AsyncMock()),
        ):
            clock.now.return_value = datetime(2026, 11, 9, 8, 0, 15, tzinfo=MOSCOW)
            await send_reminders(bot, api)
            await send_reminders(bot, api)

        api.schedule.assert_awaited_once_with("123-456", day, day)
        self.assertEqual(bot.send_rich_message.await_count, 2)
        self.assertEqual(bot.send_rich_message.await_args.args[0], -100456)
        self.assertEqual(
            bot.send_rich_message.await_args.kwargs["message_thread_id"], 77
        )
        self.assertEqual(due.await_args.args, (day, time(8)))
        self.assertEqual(
            [call.args[0] for call in update.await_args_list], [1, -100456]
        )
        self.assertTrue(
            all(
                call.kwargs == {"last_reminder_date": day}
                for call in update.await_args_list
            )
        )
