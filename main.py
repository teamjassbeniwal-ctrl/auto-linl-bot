import asyncio
import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN, PORT
from database import ensure_indexes, shortlinks_col
from scheduler import scheduler, restore_all_jobs
import handlers_all

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()


async def health(request):
    return web.Response(text="ok")


async def redirect_short(request):
    """
    Handles GET /{alias} → 302 → the current Telegram link for that alias.
    Also handles GET / → friendly landing page (not 404 anymore).
    """
    alias = request.match_info.get("alias")

    # Landing page when alias is empty
    if not alias:
        return web.Response(
            text="<h1>Auto-Link Bot</h1><p>Bot is running.</p>",
            content_type="text/html",
        )

    doc = await shortlinks_col.find_one({"alias": alias})
    if not doc:
        return web.Response(
            text="<h1>Link not found</h1><p>This short link does not exist or has expired.</p>",
            content_type="text/html",
            status=404,
        )

    raise web.HTTPFound(doc["target"])


async def on_startup():
    await ensure_indexes()
    dp.include_router(handlers_all.router)
    await restore_all_jobs(bot)
    scheduler.start()
    logger.info("Bot started. Scheduler running with %d jobs.", len(scheduler.get_jobs()))


async def on_shutdown():
    scheduler.shutdown(wait=False)
    await bot.session.close()


async def main():
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", redirect_short)
    app.router.add_get("/{alias}", redirect_short)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Health + redirect server listening on port %d", PORT)

    await on_startup()
    try:
        await dp.start_polling(bot)
    finally:
        await on_shutdown()


if __name__ == "__main__":
    asyncio.run(main())
