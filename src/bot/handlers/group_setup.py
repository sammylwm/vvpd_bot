from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.group_topics import GROUP_TYPES, check_group
from core.modules.telegram_chat import TelegramChat

router = Router()


@router.my_chat_member(F.chat.type.in_(GROUP_TYPES))
async def bot_membership_changed(event: ChatMemberUpdated, bot: Bot, session: AsyncSession):
    if event.new_chat_member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
        record = await session.get(TelegramChat, event.chat.id)
        if record is not None:
            record.active = False
            await session.commit()
        return
    await check_group(bot, event.chat.id)


@router.message(F.chat.type.in_(GROUP_TYPES), Command("setup"))
async def setup_command(message: Message, bot: Bot):
    await check_group(bot, message.chat.id)


@router.callback_query(F.data == "check_group_topics", F.message.chat.type.in_(GROUP_TYPES))
async def setup_callback(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    await check_group(bot, callback.message.chat.id)


@router.message(F.migrate_to_chat_id)
async def group_migrated(message: Message, bot: Bot, session: AsyncSession):
    record = await session.get(TelegramChat, message.chat.id)
    if record is not None:
        record.active = False
        await session.commit()
    await check_group(bot, message.migrate_to_chat_id)
