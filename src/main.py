import asyncio

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode

from bot.handlers import router
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
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
