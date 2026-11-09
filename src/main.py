import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode

from bot.handlers import router
from bot.reminders import run_reminders
from bot.group_topics import run_group_setup
from core.db_helper import DbSessionMiddleware
from settings.config import config

bot = Bot(
    config.bot.token.get_secret_value(),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher()
dp.include_router(router)


async def main():
    dp.update.middleware.register(DbSessionMiddleware())
    logging.basicConfig(level=logging.DEBUG)
    reminder_task = asyncio.create_task(run_reminders(bot))
    group_setup_task = asyncio.create_task(run_group_setup(bot))
    try:
        await dp.start_polling(bot, close_bot_session=False)
    finally:
        reminder_task.cancel()
        group_setup_task.cancel()
        with suppress(asyncio.CancelledError):
            await reminder_task
        with suppress(asyncio.CancelledError):
            await group_setup_task
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
