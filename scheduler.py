from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot
from datetime import datetime, timedelta, timezone
import string

import aiohttp

from database import channels_col, logs_col

scheduler = AsyncIOScheduler(timezone="UTC")


def _suffix(seq: int) -> str:
    letters = string.ascii_lowercase
    seq = seq % (26 * 26)
    return letters[seq // 26] + letters[seq % 26]


async def _try_shortener(url: str, alias: str | None, service: str) -> str | None:
    """Return a shortened URL from one service, or None on failure."""
    try:
        async with aiohttp.ClientSession() as s:
            if service == "isgd":
                params = {"format": "simple", "url": url}
                if alias:
                    params["shorturl"] = alias
                async with s.get(
                    "https://is.gd/create.php",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    text = (await r.text()).strip()
                    return text if text.startswith("http") else None

            if service == "vgd":
                params = {"format": "simple", "url": url}
                if alias:
                    params["shorturl"] = alias
                async with s.get(
                    "https://v.gd/create.php",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    text = (await r.text()).strip()
                    return text if text.startswith("http") else None

            if service == "tinyurl":
                params = {"url": url}
                if alias:
                    params["alias"] = alias
                async with s.get(
                    "https://tinyurl.com/api-create.php",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    text = (await r.text()).strip()
                    return text if text.startswith("http") else None
    except Exception:
        pass
    return None


async def shorten(url: str, alias: str | None = None) -> tuple[str, str]:
    """
    Try is.gd → v.gd → TinyURL.
    Returns (short_url, provider).
    If all fail, returns (url, "raw").
    """
    for service in ("isgd", "vgd", "tinyurl"):
        short = await _try_shortener(url, alias, service)
        if short and short != url:
            return short, service
    return url, "raw"


async def rotate_link(bot: Bot, channel: dict):
    chat_id = channel["chat_id"]
    old_raw = channel.get("raw_link")

    # 1. Revoke previous raw invite
    if old_raw:
        try:
            await bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=old_raw)
        except Exception:
            pass

    # 2. Create a new raw invite
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

    # 3. Shorten if configured
    prefix = channel.get("short_prefix")
    if prefix:
        seq = channel.get("short_seq", 0)
        alias = f"{prefix}{_suffix(seq)}"
        display_link, provider = await shorten(raw_link, alias=alias)

    # 4. Persist
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
    async for ch in channels_col.find({"enabled": True}):
        add_rotation_job(bot, ch)
