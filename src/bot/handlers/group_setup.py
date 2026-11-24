from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from bot.group_topics import GROUP_TYPES, check_group
from bot.preferences import preferences

router = Router()


@router.my_chat_member(F.chat.type.in_(GROUP_TYPES))
async def bot_membership_changed(event: ChatMemberUpdated, bot: Bot):
    if event.new_chat_member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
        await preferences.set_topic(event.chat.id, active=False)
        return
    await check_group(bot, event.chat.id)


@router.message(F.chat.type.in_(GROUP_TYPES), Command("setup"))
async def setup_command(message: Message, bot: Bot):
    if message.from_user is None:
        return
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}:
        await message.answer("Настроить тему может администратор группы.")
        return
    topic_id = message.message_thread_id if message.is_topic_message else None
    await check_group(bot, message.chat.id, topic_id=topic_id)


@router.callback_query(
    F.data == "check_group_topics", F.message.chat.type.in_(GROUP_TYPES)
)
async def setup_callback(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    await check_group(bot, callback.message.chat.id)


@router.message(F.migrate_to_chat_id)
async def group_migrated(message: Message, bot: Bot):
    await preferences.move_profile(message.chat.id, message.migrate_to_chat_id)
    await check_group(bot, message.migrate_to_chat_id)
