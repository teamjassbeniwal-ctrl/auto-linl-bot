from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from datetime import datetime, timedelta, timezone
from database import channels_col, logs_col
import string

scheduler = AsyncIOScheduler(timezone="UTC")


def _suffix(seq: int) -> str:
    """
    Generate a deterministic 2-character suffix (aa, ab, ..., az, ba, ..., zz).
    Provides up to 676 unique aliases per prefix.
    """
    letters = string.ascii_lowercase
    if seq >= 26 * 26:
        seq = seq % (26 * 26)
    return letters[seq // 26] + letters[seq % 26]


async def rotate_link(bot: Bot, channel: dict):
    chat_id = channel["chat_id"]
    old_raw = channel.get("raw_link")

    # 1. Revoke the previous raw Telegram invite link
    if old_raw:
        try:
            await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=old_raw)
        except Exception:
            pass

    # 2. Create a new raw Telegram invite link (no approval required)
    expire = datetime.now(timezone.utc) + timedelta(seconds=channel["interval_seconds"] * 2)
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

    # 3. If short-prefix is set, shorten the new raw link
    prefix = channel.get("short_prefix")
    if prefix:
        from handlers_all import shorten
        seq = channel.get("short_seq", 0)
        alias = f"{prefix}{_suffix(seq)}"
        short = await shorten(raw_link, alias=alias)
        if short and short != raw_link:
            display_link = short
        # advance the sequence for the next rotation
        seq = seq + 1

    # 4. Persist    await channels_col.update_one(
        {"_id": channel["_id"]},
        {"$set": {
            "current_link": display_link,
            "raw_link": raw_link,
            "link_created_at": datetime.now(timezone.utc),
            "short_seq": channel.get("short_seq", 0) + 1,
        }},
    )
    await logs_col.insert_one({
        "actor_id": 0,
        "action": "rotate_link",
        "channel_id": chat_id,
        "detail": {"display": display_link, "raw": raw_link},
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
    async for ch in channels_col.find({"enabled": True}):
        add_rotation_job(bot, ch)
