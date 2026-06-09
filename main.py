"""
Точка входа. Инициализирует БД, загружает базовые вопросы, запускает бота.
"""
import asyncio
import logging

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject

from config import settings
from database.db import AsyncSessionLocal, init_db
from data.seed import seed_base_questions
from bot.handlers import start, upload, topics, materials, quiz, chat, teacher, student_group

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class DbSessionMiddleware(BaseMiddleware):
    """Открывает AsyncSession перед вызовом хэндлера и закрывает после."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with AsyncSessionLocal() as session:
            data["db"] = session
            return await handler(event, data)


async def on_startup(bot: Bot) -> None:
    await init_db()
    async with AsyncSessionLocal() as db:
        await seed_base_questions(db)
    logger.info("Database ready.")


async def main() -> None:
    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(DbSessionMiddleware())

    dp.include_router(start.router)
    dp.include_router(upload.router)
    dp.include_router(topics.router)
    dp.include_router(materials.router)
    dp.include_router(chat.router)
    dp.include_router(teacher.router)
    dp.include_router(student_group.router)
    dp.include_router(quiz.router)

    await on_startup(bot)
    logger.info("Bot is running. Press Ctrl+C to stop.")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
