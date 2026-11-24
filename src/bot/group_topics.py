import asyncio
import logging
from collections import defaultdict

from aiogram import BaseMiddleware, Bot
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.preferences import TopicSettings, preferences

logger = logging.getLogger(__name__)
setup_locks = defaultdict(asyncio.Lock)
GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}
SETUP_BUTTON = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Проверить настройки", callback_data="check_group_topics"
            )
        ]
    ]
)


async def configure_group(
    bot: Bot, chat_id: int, notify: bool = True, topic_id: int | None = None
) -> None:
    async with setup_locks[chat_id]:
        record = await preferences.topic(chat_id) or TopicSettings()
        chat = await bot.get_chat(chat_id)
        member = await bot.get_chat_member(chat_id, bot.id)
        if member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
            await preferences.set_topic(chat_id, active=False)
            return
        if not record.active or record.topic_id is None:
            await preferences.set_topic(chat_id, active=True)
        missing = []
        if not chat.is_forum:
            missing.append("Включите темы в настройках группы.")
        if member.status != ChatMemberStatus.ADMINISTRATOR or not getattr(
            member, "can_manage_topics", False
        ):
            missing.append(
                "Назначьте меня администратором и выдайте право «Управление темами»."
            )
        if missing:
            if notify:
                await bot.send_message(
                    chat_id,
                    "Для работы в группе нужны темы и право управления ими.\n\n"
                    + "\n".join(missing)
                    + "\n\nПосле настройки я создам тему «Дедлайны». "
                    "Проверка выполняется автоматически; можно также нажать кнопку ниже.",
                    reply_markup=SETUP_BUTTON,
                )
            return
        if topic_id is not None:
            await preferences.set_topic(chat_id, topic_id=topic_id, active=True)
        topic_id = topic_id if topic_id is not None else record.topic_id
        if topic_id is not None:
            if notify:
                await bot.send_message(
                    chat_id,
                    "Настройка завершена. Работаю в теме «Дедлайны».",
                    message_thread_id=topic_id,
                )
            return
        topic = await bot.create_forum_topic(chat_id, name="Дедлайны")
        await preferences.set_topic(
            chat_id, topic_id=topic.message_thread_id, active=True
        )
        await bot.send_message(
            chat_id,
            "Тема «Дедлайны» создана. Отправляйте команды и задания здесь.",
            message_thread_id=topic.message_thread_id,
        )


async def check_group(
    bot: Bot, chat_id: int, notify: bool = True, topic_id: int | None = None
) -> None:
    try:
        await configure_group(bot, chat_id, notify, topic_id)
    except TelegramForbiddenError:
        logger.warning("Bot cannot access group %s", chat_id)
        await preferences.set_topic(chat_id, active=False)
    except TelegramAPIError:
        logger.exception("Failed to configure group %s", chat_id)


async def run_group_setup(bot: Bot) -> None:
    while True:
        try:
            for chat_id in await preferences.pending_topics():
                await check_group(bot, chat_id, notify=False)
        except Exception:
            logger.exception("Group setup check failed")
        await asyncio.sleep(15)


class DeadlineTopicMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        message = event.message if isinstance(event, CallbackQuery) else event
        if message is None:
            return
        if message.chat.type not in GROUP_TYPES:
            return await handler(event, data)
        record = await preferences.topic(message.chat.id)
        allowed = (
            record is not None
            and record.active
            and record.topic_id is not None
            and getattr(message, "message_thread_id", None) == record.topic_id
        )
        if allowed:
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer("Бот работает только в теме «Дедлайны».")
