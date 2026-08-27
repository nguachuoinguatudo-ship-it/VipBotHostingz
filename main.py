from __future__ import annotations

import asyncio
import logging

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import get_settings
from app.bot import StyledBot
from app.db import Database
from app.handlers import AppContext, register_data, router
from app.hosting import HostingManager


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    db = Database(settings.database_path)
    await db.connect()
    owners = await db.list_owners()
    settings.owner_ids.update(int(row["user_id"]) for row in owners)

    hosting = HostingManager(db, settings.data_dir)
    hosting.prepare()

    bot = StyledBot(settings.bot_token)
    me = await bot.get_me()
    bot_username = (settings.bot_username or me.username or "").lstrip("@")

    ctx = AppContext(settings, db, hosting, bot_username)
    register_data(ctx)
    await hosting.restart_running_bots()

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await hosting.stop_all()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
