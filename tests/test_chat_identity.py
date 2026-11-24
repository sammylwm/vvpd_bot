import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat, Message, User

from bot.handlers.authorization import receive_group
from bot.handlers.deadline_buttons import show_schedule_deadlines
from bot.handlers.filters import UnregisteredUser
from bot.identity import account_id
from bot.preferences import ProfileSettings, preferences


def message(chat_id, chat_type, sender=123):
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=chat_id, type=chat_type),
        from_user=User(id=sender, is_bot=False, first_name="Test"),
        text="261-329",
    )


class ChatIdentityTests(unittest.IsolatedAsyncioTestCase):
    def test_private_and_group_identity(self):
        for chat_type, chat_id in (
            ("private", 123),
            ("group", -456),
            ("supergroup", -100456),
        ):
            for sender in (123, 789):
                ms = message(chat_id, chat_type, sender)
                self.assertEqual(
                    account_id(ms, sender),
                    sender if chat_type == "private" else chat_id,
                )

    async def test_registration_is_shared_in_a_group_only(self):
        member = message(-100456, "supergroup")
        another_member = message(-100456, "supergroup", 789)
        private = message(123, "private")
        checker = UnregisteredUser()
        profile = ProfileSettings(-100456, "261-329")
        with (
            patch.object(
                preferences, "profile", side_effect=[None, profile, None]
            ) as load,
            patch.object(preferences, "set_group") as save,
            patch.object(Message, "answer", new_callable=AsyncMock),
        ):
            self.assertTrue(await checker(member))
            await receive_group(member, AsyncMock())
            self.assertFalse(await checker(another_member))
            self.assertTrue(await checker(private))
        save.assert_awaited_once_with(-100456, "261-329")
        self.assertEqual(
            [call.args[0] for call in load.await_args_list], [-100456, -100456, 123]
        )

    async def test_deadline_button_requires_the_current_group(self):
        ms = message(-100456, "supergroup")
        callback = SimpleNamespace(
            data="schedule_tasks:261-329:2026-11-09:2026-11-10",
            message=ms,
            from_user=ms.from_user,
            answer=AsyncMock(),
        )
        api = AsyncMock()
        with patch.object(preferences, "profile", return_value=None):
            await show_schedule_deadlines(callback, api)
        api.deadlines.assert_not_awaited()
        callback.answer.assert_awaited_once()
