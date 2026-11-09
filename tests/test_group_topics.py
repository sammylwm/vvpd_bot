import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.types import CallbackQuery, Chat, Message, User
from datetime import datetime, timezone

from bot.group_topics import DeadlineTopicMiddleware, configure_group
from bot.handlers.group_setup import bot_membership_changed, group_migrated
from core.modules.telegram_chat import TelegramChat


class GroupTopicsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.record = TelegramChat(id=-100123, active=True)
        self.session = AsyncMock()
        self.session.get.return_value = self.record
        self.context = MagicMock()
        self.context.__aenter__.return_value = self.session
        self.context.__aexit__.return_value = False
        self.factory = patch("bot.group_topics.async_session_maker", return_value=self.context)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.bot = SimpleNamespace(
            id=42,
            get_chat=AsyncMock(return_value=SimpleNamespace(is_forum=True)),
            get_chat_member=AsyncMock(return_value=SimpleNamespace(
                status=ChatMemberStatus.ADMINISTRATOR, can_manage_topics=True
            )),
            create_forum_topic=AsyncMock(return_value=SimpleNamespace(message_thread_id=77)),
            send_message=AsyncMock(),
        )

    async def test_missing_topics_and_rights_are_explained(self):
        self.bot.get_chat.return_value.is_forum = False
        self.bot.get_chat_member.return_value.status = ChatMemberStatus.MEMBER
        await configure_group(self.bot, self.record.id)
        self.bot.create_forum_topic.assert_not_awaited()
        text = self.bot.send_message.await_args.args[1]
        self.assertIn("Включите темы", text)
        self.assertIn("Управление темами", text)
        self.assertIsNotNone(self.bot.send_message.await_args.kwargs["reply_markup"])

    async def test_admin_without_manage_topics_cannot_create_topic(self):
        self.bot.get_chat_member.return_value.can_manage_topics = False
        await configure_group(self.bot, self.record.id)
        self.bot.create_forum_topic.assert_not_awaited()

    async def test_topic_is_saved_and_not_created_again(self):
        await configure_group(self.bot, self.record.id)
        await configure_group(self.bot, self.record.id)
        self.bot.create_forum_topic.assert_awaited_once_with(self.record.id, name="Дедлайны")
        self.assertEqual(self.record.topic_id, 77)
        self.assertEqual(self.bot.send_message.await_args.kwargs["message_thread_id"], 77)

    async def test_pending_check_creates_topic_after_settings_change(self):
        self.bot.get_chat.return_value.is_forum = False
        await configure_group(self.bot, self.record.id, notify=False)
        self.bot.send_message.assert_not_awaited()
        self.bot.get_chat.return_value.is_forum = True
        await configure_group(self.bot, self.record.id, notify=False)
        self.bot.create_forum_topic.assert_awaited_once()
        self.assertEqual(self.record.topic_id, 77)

    async def test_new_chat_is_persisted(self):
        self.session.get.return_value = None
        self.session.add = MagicMock()
        await configure_group(self.bot, -100456)
        record = self.session.add.call_args.args[0]
        self.assertEqual(record.id, -100456)
        self.assertEqual(record.topic_id, 77)

    async def test_removal_disables_pending_checks(self):
        event = SimpleNamespace(
            chat=SimpleNamespace(id=self.record.id),
            new_chat_member=SimpleNamespace(status=ChatMemberStatus.LEFT),
        )
        await bot_membership_changed(event, self.bot, self.session)
        self.assertFalse(self.record.active)
        self.bot.create_forum_topic.assert_not_awaited()

    async def test_migration_disables_old_chat_and_checks_new_chat(self):
        message = SimpleNamespace(chat=SimpleNamespace(id=self.record.id), migrate_to_chat_id=-100456)
        with patch("bot.handlers.group_setup.check_group", new_callable=AsyncMock) as check:
            await group_migrated(message, self.bot, self.session)
        self.assertFalse(self.record.active)
        check.assert_awaited_once_with(self.bot, -100456)

    async def test_messages_only_pass_in_configured_topic_or_private_chat(self):
        self.record.topic_id = 77
        middleware = DeadlineTopicMiddleware()
        handler = AsyncMock()
        for chat_type, thread_id, allowed in (
            (ChatType.SUPERGROUP, 77, True),
            (ChatType.SUPERGROUP, 88, False),
            (ChatType.SUPERGROUP, None, False),
            (ChatType.PRIVATE, None, True),
        ):
            handler.reset_mock()
            message = SimpleNamespace(
                chat=SimpleNamespace(id=self.record.id, type=chat_type),
                message_thread_id=thread_id,
            )
            await middleware(handler, message, {})
            self.assertEqual(handler.await_count, int(allowed))

    async def test_callbacks_outside_topic_are_acknowledged(self):
        callback = CallbackQuery(
            id="test", from_user=User(id=1, is_bot=False, first_name="Test"),
            chat_instance="test", data="deadlines",
            message=Message(
                message_id=1, date=datetime.now(timezone.utc),
                chat=Chat(id=self.record.id, type=ChatType.SUPERGROUP),
                message_thread_id=88,
            ),
        )
        handler = AsyncMock()
        with patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer:
            await DeadlineTopicMiddleware()(handler, callback, {})
        handler.assert_not_awaited()
        answer.assert_awaited_once()
