import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from config import BOT_TOKEN, PORT
from database import ensure_indexes
from scheduler import scheduler, restore_all_jobs
from handlers import register_all

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

async def health(request):
    return web.Response(text="ok")

async def on_startup():
    await ensure_indexes()
    register_all(dp)
    await restore_all_jobs(bot)
    scheduler.start()
    logger.info("Bot started. Scheduler running with %d jobs.", len(scheduler.get_jobs()))

async def on_shutdown():
    scheduler.shutdown(wait=False)
    await bot.session.close()

async def main():
    # Start the HTTP health-check server required by Koyeb
    app = web.Application()
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Health server listening on port %d", PORT)

    await on_startup()
    try:
        await dp.start_polling(bot)
    finally:
        await on_shutdown()

if __name__ == "__main__":
    asyncio.run(main())
