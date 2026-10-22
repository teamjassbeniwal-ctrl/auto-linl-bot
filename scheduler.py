import os
from datetime import datetime, timedelta, timezone
import string

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot

from database import channels_col, logs_col, shortlinks_col   # ← add shortlinks_col

scheduler = AsyncIOScheduler(timezone="UTC")

# Your Koyeb domain — no trailing slash
SHORT_BASE = os.getenv("SHORT_BASE", "https://lost-eudora-sukhanq-66b304f1.koyeb.app")


def _suffix(seq: int) -> str:
    letters = string.ascii_lowercase
    seq = seq % (26 * 26)
    return letters[seq // 26] + letters[seq % 26]


async def shorten(url: str, alias: str | None = None) -> tuple[str, str]:
    """
    Return a URL on your own Koyeb domain that 302-redirects to `url`.
    No intermediate page. No third-party shortener. No rate limits.
    """
    if not alias:
        return url, "raw"

    # Upsert the alias → current Telegram link mapping
    await shortlinks_col.update_one(
        {"alias": alias},
        {"$set": {"target": url, "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    return f"{SHORT_BASE}/{alias}", "self"


async def rotate_link(bot: Bot, channel: dict):
    chat_id = channel["chat_id"]
    old_raw = channel.get("raw_link")

    if old_raw:
        try:
            await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=old_raw)
        except Exception:
            pass

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

    prefix = channel.get("short_prefix")
    if prefix:
        seq = channel.get("short_seq", 0)
        alias = f"{prefix}{_suffix(seq)}"
        display_link, provider = await shorten(raw_link, alias=alias)

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


# add_rotation_job, remove_rotation_job, restore_all_jobs — unchanged, keep as-is
