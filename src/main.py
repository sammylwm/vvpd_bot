import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiohttp import ClientSession, ClientTimeout

from bot.attachments import AlbumMiddleware
from bot.group_topics import run_group_setup
from bot.handlers import router
from bot.preferences import preferences
from bot.reminders import run_reminders
from core.api import PolytechApi
from settings.config import config


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(
        config.bot.token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(disable_fsm=True, events_isolation=SimpleEventIsolation())
    dp.update.outer_middleware(AlbumMiddleware())
    dp.update.outer_middleware(dp.fsm)
    await bot.send_message(chat_id=8364931329, text="Бот успешно запущен")
    dp.include_router(router)
    try:
        await preferences.initialize()
        await poll(dp, bot)
    finally:
        await preferences.engine.dispose()


async def poll(dp: Dispatcher, bot: Bot):
    async with (
        ClientSession(timeout=ClientTimeout(total=120)) as session,
        bot.context(),
    ):
        api = PolytechApi(session, config.api.path)
        tasks = (
            asyncio.create_task(run_reminders(bot, api)),
            asyncio.create_task(run_group_setup(bot)),
        )
        try:
            await dp.start_polling(bot, api=api, close_bot_session=False)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
