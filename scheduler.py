import os
import string
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from database import channels_col, logs_col, shortlinks_col

scheduler = AsyncIOScheduler(timezone="UTC")

# Your Koyeb domain — no trailing slash
SHORT_BASE = os.getenv(
    "SHORT_BASE",
    "https://lost-eudora-sukhanq-66b304f1.koyeb.app",
)


def _suffix(seq: int) -> str:
    letters = string.ascii_lowercase
    seq = seq % (26 * 26)
    return letters[seq // 26] + letters[seq % 26]


async def shorten(url: str, alias: str | None = None) -> tuple[str, str]:
    """
    Return a URL on your own Koyeb domain that 302-redirects to `url`.
    No intermediate page. No third-party shortener.
    """
    if not alias:
        return url, "raw"
    await shortlinks_col.update_one(
        {"alias": alias},
        {"$set": {"target": url, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return f"{SHORT_BASE}/{alias}", "self"


async def rotate_link(bot: Bot, channel: dict):
    chat_id = channel["chat_id"]
    old_raw = channel.get("raw_link")

    # 1. Revoke previous raw invite
    if old_raw:
        try:
            await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=old_raw)
        except Exception:
            pass

    # 2. Create new raw invite
    expire = datetime.now(timezone.utc) + timedelta(
        seconds=channel["interval_seconds"] * 2
    )
    try:
        result = await bot.create_chat_invite_link(
            chat_id=chat_id,
            expire_date=expire,
            name=f"auto-{int(datetime.now().timestamp())}",
            creates_join_request=False,
        )
    except Exception as e:
        await logs_col.insert_one({
            "actor_id": 0,
            "action": "rotate_failed",
            "channel_id": chat_id,
            "detail": {"error": str(e)},
            "timestamp": datetime.now(timezone.utc),
        })
        return

    raw_link = result.invite_link
    display_link = raw_link
    provider = "raw"

    # 3. Optional short-link
    prefix = channel.get("short_prefix")
    if prefix:
        seq = channel.get("short_seq", 0)
        alias = f"{prefix}{_suffix(seq)}"
        display_link, provider = await shorten(raw_link, alias=alias)

    # 4. Save
    await channels_col.update_one(
        {"_id": channel["_id"]},
        {
            "$set": {
                "current_link": display_link,
                "raw_link": raw_link,
                "link_created_at": datetime.now(timezone.utc),
                "short_seq": channel.get("short_seq", 0) + 1,
                "last_provider": provider,
            }
        },
    )
    await logs_col.insert_one({
        "actor_id": 0,
        "action": "rotate_link",
        "channel_id": chat_id,
        "detail": {"display": display_link, "raw": raw_link, "provider": provider},
        "timestamp": datetime.now(timezone.utc),
    })


def add_rotation_job(bot: Bot, channel: dict):
    job_id = f"rotate_{channel['chat_id']}"
    scheduler.add_job(
        rotate_link,
        "interval",
        seconds=channel["interval_seconds"],
        args=[bot, channel],
        id=job_id,
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )


def remove_rotation_job(chat_id: int):
    try:
        scheduler.remove_job(f"rotate_{chat_id}")
    except Exception:
        pass


async def restore_all_jobs(bot: Bot):
    """Rebuild every enabled channel's rotation job on startup."""
    async for ch in channels_col.find({"enabled": True}):
        add_rotation_job(bot, ch)
