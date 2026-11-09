import unittest
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.profile import change_time, render_profile
from bot.reminders import MOSCOW, send_reminders
from core.modules.group import Group
from core.modules.user import User


class ReminderSettingsTests(unittest.IsolatedAsyncioTestCase):
    def test_profile_has_time_and_status_in_one_row(self):
        user = User(telegram_id=1, reminders_enabled=False, reminder_time=time(9, 30))
        profile = render_profile(user, Group(name="123-456"))
        buttons = profile.blocks[-1].buttons
        self.assertEqual(len(buttons), 2)
        self.assertIn("09:30", buttons[0].text)
        self.assertIn("Выключено", buttons[1].text)

    async def test_invalid_time_keeps_waiting(self):
        for value in ("24:00", "12:60", "9:30", "hello", ""):
            message = SimpleNamespace(text=value, from_user=SimpleNamespace(id=1), answer=AsyncMock())
            state = SimpleNamespace(clear=AsyncMock())
            session = SimpleNamespace(commit=AsyncMock())
            await change_time(message, state, session)
            message.answer.assert_awaited_once()
            state.clear.assert_not_awaited()
            session.commit.assert_not_awaited()

    async def test_time_is_saved_without_enabling_notifications(self):
        for value in ("00:00", "09:30", "23:59"):
            user = User(telegram_id=1, reminders_enabled=False, reminder_time=time(8))
            message = SimpleNamespace(text=value, from_user=SimpleNamespace(id=1), answer_rich=AsyncMock())
            state = SimpleNamespace(clear=AsyncMock())
            session = SimpleNamespace(commit=AsyncMock())
            with patch("bot.handlers.profile.load_profile", new=AsyncMock(return_value=(user, Group(name="123-456")))):
                await change_time(message, state, session)
            self.assertEqual(user.reminder_time, time.fromisoformat(value))
            self.assertFalse(user.reminders_enabled)
            session.commit.assert_awaited_once()
            state.clear.assert_awaited_once()

    async def test_broadcast_filters_time_and_records_success(self):
        session = AsyncMock()
        session.execute.side_effect = [[(1, 7, "123-456")], MagicMock()]
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=session)
        context.__aexit__ = AsyncMock(return_value=False)
        bot = SimpleNamespace(send_rich_message=AsyncMock())
        with patch("bot.reminders.async_session_maker", return_value=context), patch("bot.reminders.datetime") as clock, patch("bot.reminders.build_reminder_payload", new=AsyncMock(return_value=(["schedule"], []))), patch("bot.reminders.asyncio.sleep", new=AsyncMock()):
            clock.now.return_value = datetime(2026, 11, 9, 9, 30, 15, tzinfo=MOSCOW)
            await send_reminders(bot)
        query = session.execute.await_args_list[0].args[0]
        self.assertIn(time(9, 30), query.compile().params.values())
        self.assertIn("last_reminder_date", str(query))
        update = session.execute.await_args_list[1].args[0]
        self.assertIn(date(2026, 11, 9), update.compile().params.values())
        session.commit.assert_awaited_once()
        bot.send_rich_message.assert_awaited_once()
