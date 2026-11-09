import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import Chat, Message, User as TelegramUser

from bot.handlers.authorization import receive_group
from bot.handlers.add import AddDeadline, confirm_deadline
from bot.handlers.day import command_day, command_reminder
from bot.handlers.deadline_buttons import show_schedule_deadlines
from bot.handlers.deadlines import command_deadlines
from bot.handlers.delete import command_delete_deadline, delete_deadline
from bot.handlers.filters import UnregisteredUser
from bot.handlers.profile import command_profile, profile_action
from bot.handlers.week import command_week
from bot.identity import account_id
from bot.reminders import MOSCOW, send_reminders
from core.modules.group import Group
from core.modules.user import User


def message(chat_id, chat_type, sender=123):
    return Message(
        message_id=1, date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type=chat_type),
        from_user=TelegramUser(id=sender, is_bot=False, first_name="Test"),
        text="261-329",
    )


class ChatIdentityTests(unittest.IsolatedAsyncioTestCase):
    def test_private_and_group_identity(self):
        for chat_type, chat_id, expected in (
            ("private", 123, 123), ("group", -456, -456),
            ("supergroup", -100456, -100456),
        ):
            for sender in (123, 789):
                ms = message(chat_id, chat_type, sender)
                self.assertEqual(account_id(ms, sender), sender if chat_type == "private" else expected)

    async def test_commands_and_registration_check_query_chat_account(self):
        for chat_type, chat_id in (("private", 123), ("supergroup", -100456)):
            ms = message(chat_id, chat_type)
            for command in (command_day, command_reminder, command_deadlines, command_week, command_delete_deadline):
                with self.subTest(chat_type=chat_type, command=command.__name__):
                    session = AsyncMock()
                    session.scalar.return_value = None
                    session.scalars.return_value = []
                    with patch.object(Message, "answer", new_callable=AsyncMock):
                        if command in (command_day, command_week):
                            await command(ms, SimpleNamespace(command="day", args=None), session)
                        else:
                            await command(ms, session)
                    call = session.scalars.await_args if command == command_delete_deadline else session.scalar.await_args
                    self.assertIn(chat_id, call.args[0].compile().params.values())
            session = AsyncMock()
            session.scalar.return_value = True
            self.assertFalse(await UnregisteredUser()(ms, session))
            self.assertIn(chat_id, session.scalar.await_args.args[0].compile().params.values())

    async def test_registration_stores_chat_id(self):
        for chat_type, chat_id in (("private", 123), ("supergroup", -100456)):
            session = AsyncMock()
            session.begin = MagicMock()
            session.begin.return_value.__aenter__ = AsyncMock()
            session.begin.return_value.__aexit__ = AsyncMock(return_value=False)
            session.scalar.side_effect = [Group(id=1, name="261-329"), None]
            session.add = MagicMock()
            with patch.object(Message, "answer", new_callable=AsyncMock):
                await receive_group(message(chat_id, chat_type), AsyncMock(), session)
            self.assertEqual(session.add.call_args.args[0].telegram_id, chat_id)

    async def test_profile_uses_shared_account_for_different_members(self):
        user = User(telegram_id=-100456, reminder_time=datetime.now().time(), reminders_enabled=True)
        group = Group(id=1, name="261-329")
        for sender in (123, 789):
            ms = message(-100456, "supergroup", sender)
            session = AsyncMock()
            state = AsyncMock()
            state.get_state.return_value = None
            cb = SimpleNamespace(
                data="profile:off:-100456", message=ms,
                from_user=SimpleNamespace(id=sender), answer=AsyncMock(),
            )
            with patch("bot.handlers.profile.load_profile", new_callable=AsyncMock, return_value=(user, group)) as load, patch.object(Message, "answer_rich", new_callable=AsyncMock):
                await command_profile(ms, session)
                await profile_action(cb, state, session)
            self.assertEqual([call.args[1] for call in load.await_args_list], [-100456, -100456])
            session.commit.assert_awaited_once()
            self.assertFalse(user.reminders_enabled)

    async def test_deadline_buttons_query_chat_account(self):
        for chat_type, chat_id in (("private", 123), ("supergroup", -100456)):
            ms = message(chat_id, chat_type, sender=789)
            cb = SimpleNamespace(
                data="schedule_tasks:1:2026-11-09:2026-11-10", message=ms,
                from_user=SimpleNamespace(id=789), answer=AsyncMock(),
            )
            session = AsyncMock()
            session.scalar.return_value = None
            await show_schedule_deadlines(cb, session)
            expected = 789 if chat_type == "private" else chat_id
            self.assertEqual(session.scalar.await_args.args[0].compile().params["telegram_id_1"], expected)
            cb.data = "delete_yes:1"
            session.execute.return_value = MagicMock()
            session.execute.return_value.scalar_one_or_none.return_value = None
            with patch.object(Message, "answer", new_callable=AsyncMock), patch.object(Message, "edit_reply_markup", new_callable=AsyncMock):
                await delete_deadline(cb, session)
            self.assertEqual(session.execute.await_args.args[0].compile().params["telegram_id_1"], expected)

    async def test_add_confirmation_uses_shared_chat_account(self):
        ms = message(-100456, "supergroup", sender=789)
        cb = SimpleNamespace(
            data="add_yes:token", message=ms,
            from_user=SimpleNamespace(id=789), answer=AsyncMock(),
        )
        state = AsyncMock()
        state.get_data.return_value = {
            "confirmation_token": "token", "group": "261-329", "homework": {},
        }
        state.get_state.return_value = AddDeadline.waiting_for_confirmation.state
        session = AsyncMock()
        session.scalar.return_value = "261-329"
        result = SimpleNamespace(status="unchanged")
        with patch("bot.handlers.add.save_deadline", new_callable=AsyncMock, return_value=result) as save, patch.object(Message, "answer", new_callable=AsyncMock), patch.object(Message, "edit_reply_markup", new_callable=AsyncMock):
            await confirm_deadline(cb, state, session)
        self.assertEqual(session.scalar.await_args.args[0].compile().params["telegram_id_1"], -100456)
        save.assert_awaited_once_with(session, "261-329", {}, [])

    async def test_shared_reminders_go_to_deadlines_topic(self):
        session = AsyncMock()
        session.execute.side_effect = [[(-100456, 1, "261-329")], MagicMock()]
        session.get.return_value = SimpleNamespace(active=True, topic_id=77)
        context = MagicMock()
        context.__aenter__.return_value = session
        context.__aexit__.return_value = False
        bot = SimpleNamespace(send_rich_message=AsyncMock())
        with patch("bot.reminders.async_session_maker", return_value=context), patch("bot.reminders.datetime") as clock, patch("bot.reminders.build_reminder_payload", new_callable=AsyncMock, return_value=(["schedule"], [])), patch("bot.reminders.asyncio.sleep", new_callable=AsyncMock):
            clock.now.return_value = datetime(2026, 11, 9, 8, 0, tzinfo=MOSCOW)
            await send_reminders(bot)
        self.assertEqual(bot.send_rich_message.await_args.args[0], -100456)
        self.assertEqual(bot.send_rich_message.await_args.kwargs["message_thread_id"], 77)
        session.commit.assert_awaited_once()
