from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from datetime import datetime, timedelta, timezone
from database import channels_col, logs_col

scheduler = AsyncIOScheduler(timezone="UTC")

async def rotate_link(bot: Bot, channel: dict):
    """Rotate the invite link for a single channel."""
    chat_id = channel["chat_id"]
    old_link = channel.get("current_link")

    # Revoke the old link (best effort)
    if old_link:
        try:
            await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=old_link)
        except Exception:
            pass

    # Create a new link. Expiry is 2x the interval so a failed
    # rotation never leaves the channel without a valid link.
    expire = datetime.now(timezone.utc) + timedelta(seconds=channel["interval_seconds"] * 2)
    try:
        result = await bot.create_chat_invite_link(
            chat_id=chat_id,
            expire_date=expire,
            name=f"auto-{int(datetime.now().timestamp())}",
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

    await channels_col.update_one(
        {"_id": channel["_id"]},
        {"$set": {
            "current_link": result.invite_link,
            "link_created_at": datetime.now(timezone.utc),
        }}
    )
    await logs_col.insert_one({
        "actor_id": 0,
        "action": "rotate_link",
        "channel_id": chat_id,
        "detail": {"new_link": result.invite_link},
        "timestamp": datetime.now(timezone.utc),
    })

def add_rotation_job(bot: Bot, channel: dict):
    """Add or replace a rotation job using a fixed ID to prevent duplicates."""
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
    job_id = f"rotate_{chat_id}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

async def restore_all_jobs(bot: Bot):
    """Restore every enabled channel's rotation job on startup."""
    async for ch in channels_col.find({"enabled": True}):
        add_rotation_job(bot, ch)
