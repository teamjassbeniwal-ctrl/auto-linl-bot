"""
All Telegram handlers for the Auto-Link Bot.
Single-module design (no handlers/ package).

Public channels are visible to every user via /start and /channels.
Private channels are only visible to their owner and assigned admins.
"""

import asyncio
import urllib.parse
from datetime import datetime, timezone

import aiohttp
from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import OWNER_ID, INTERVAL_OPTIONS
from database import channels_col, users_col, logs_col
from scheduler import add_rotation_job, remove_rotation_job, scheduler


router = Router()


# ────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def can_manage(user_id: int, chat_id: int) -> bool:
    ch = await channels_col.find_one({
        "chat_id": chat_id,
        "$or": [{"owner_id": user_id}, {"assigned_admins": user_id}],
    })
    return ch is not None


async def visible_to(user_id: int):
    """
    Channels the user should see on /start:
      - any channel they own or are assigned to
      - every channel NOT explicitly marked private
    """
    return await channels_col.find({
        "enabled": True,
        "$or": [
            {"owner_id": user_id},
            {"assigned_admins": user_id},
            {"visibility": {"$ne": "private"}},
        ],
    }).to_list(None)


async def shorten(url: str, alias: str | None = None) -> str:
    """
    Shorten a Telegram invite link.
    Primary: is.gd (free, no key, supports custom alias).
    Fallback: v.gd (same API).
    Returns the short URL, or the original URL on failure.
    """
    base = "https://is.gd/create.php"
    params = {"format": "simple", "url": url}
    if alias:
        params["shorturl"] = alias

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(base, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                text = (await r.text()).strip()
                if text.startswith("http"):
                    return text
    except Exception:
        pass

    # Fallback v.gd
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://v.gd/create.php",
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                text = (await r.text()).strip()
                if text.startswith("http"):
                    return text
    except Exception:
        pass

    return url  # fall back to raw Telegram link


# ────────────────────────────────────────────────────────────────
# /start
# ────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id

    await users_col.update_one(
        {"telegram_id": user_id},
        {"$setOnInsert": {"role": "user", "created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    await logs_col.insert_one({
        "actor_id": user_id,
        "action": "start",
        "channel_id": None,
        "detail": {},
        "timestamp": datetime.now(timezone.utc),
    })

    channels = await visible_to(user_id)

    if not channels:
        await message.answer(
            "👋 Welcome to Auto-Link Bot!\n\n"
            "No public channels are available right now.\n"
            "If you own a channel, use /addchannel to register it."
        )
        return

    if len(channels) == 1:
        ch = channels[0]
        link = ch.get("current_link")
        if not link:
            await message.answer(
                "❌ Link not found for this channel yet. Please try again in a moment."
            )
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🚀 Join Channel", url=link)
        ]])
        await message.answer(
            f"👋 Welcome to Auto-Link Bot!\n\n"
            f"📢 Channel: {ch['title']}\n\n"
            f"🔗 Current Link:\n{link}\n\n"
            f"👇 Click below to join!",
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    else:
        buttons = [
            [InlineKeyboardButton(text=ch["title"], callback_data=f"view_ch:{ch['chat_id']}")]
            for ch in channels
        ]
        await message.answer(
            "📢 Please select a channel:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )


@router.callback_query(lambda c: c.data.startswith("view_ch:"))
async def view_channel(callback: types.CallbackQuery):
    chat_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    ch = await channels_col.find_one({
        "chat_id": chat_id,
        "$or": [
            {"owner_id": user_id},
            {"assigned_admins": user_id},
            {"visibility": {"$ne": "private"}},
        ],
    })
    if not ch:
        await callback.answer("Access denied.", show_alert=True)
        return

    link = ch.get("current_link")
    if not link:
        await callback.answer(
            "❌ Link not found for this channel yet. Please try again in a moment.",
            show_alert=True,
        )
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Join Channel", url=link)
    ]])
    await callback.message.edit_text(
        f"📢 Channel: {ch['title']}\n\n"
        f"🔗 Current Link:\n{link}\n\n"
        f"👇 Click below to join!",
        reply_markup=kb,
        disable_web_page_preview=True,
    )
    await callback.answer()


# ────────────────────────────────────────────────────────────────
# /channels — browse every public channel
# ────────────────────────────────────────────────────────────────

@router.message(Command("channels"))
async def cmd_channels(message: types.Message):
    channels = await channels_col.find({
        "enabled": True,
        "visibility": {"$ne": "private"},
    }).to_list(None)

    if not channels:
        await message.answer("No public channels available right now.")
        return

    # One inline button per channel — up to 20 fits comfortably on any screen.
    buttons = [
        [InlineKeyboardButton(text=ch["title"], url=ch["current_link"])]
        for ch in channels
        if ch.get("current_link")
    ]
    if not buttons:
        await message.answer("Channels exist but no invite links are available yet.")
        return

    await message.answer(
        "📢 <b>Available Public Channels</b>\n\nTap one to join:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ────────────────────────────────────────────────────────────────
# /help
# ────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "📖 <b>Auto-Link Bot — Command Reference</b>\n\n"
        "👤 <b>Everyone</b>\n"
        "/start — Show the current invite link(s)\n"
        "/channels — List all public channels\n"
        "/help — Show this help message\n"
        "/mychannels — List channels you own or are assigned to\n"
        "/status — Show status of the channels you manage\n\n"
        "📢 <b>Channel Management</b> (owner or assigned admin)\n"
        "/addchannel @username — Register a channel (public by default)\n"
        "/addchannel -100xxxxxxxxxx — Register by chat ID\n"
        "/removechannel CHAT_ID — Unregister a channel (owner only)\n"
        "/settime CHAT_ID VALUE — Change rotation interval (5m / 10m / 1h / 24h / seconds)\n"
        "/setlink CHAT_ID LINK — Manually override the link (owner only)\n"
        "/setpublic CHAT_ID — Make a channel visible to everyone\n"
        "/setprivate CHAT_ID — Hide a channel from non-owners/admins\n"
        "/setshort CHAT_ID PREFIX — Enable short-link rotation with a prefix\n"
        "/setrawlink CHAT_ID — Turn off short-link rotation\n\n"
    )
    if is_owner(message.from_user.id):
        text += (
            "👑 <b>Owner Only</b>\n"
            "/addadmin USER_ID — Promote a user to admin\n"
            "/removeadmin USER_ID — Demote an admin\n"
            "/adminlist — List all admins\n"
            "/dashboard — Bot statistics &amp; recent activity\n"
            "/broadcast MESSAGE — Send a message to every registered user\n\n"
        )
    text += (
        "🔗 <b>Quick start</b>\n"
        "1. /addchannel @my_channel\n"
        "2. /settime -1001234567890 10m\n"
        "3. Share the bot → users send /start and see your link\n"
    )
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


# ────────────────────────────────────────────────────────────────
# /broadcast — Owner only
# ────────────────────────────────────────────────────────────────

@router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Only the Owner can run this command.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer("Usage: <code>/broadcast YOUR MESSAGE</code>", parse_mode="HTML")
        return

    body = parts[1].strip()
    user_ids = []
    async for u in users_col.find({}, {"telegram_id": 1}):
        uid = u.get("telegram_id")
        if uid and uid != OWNER_ID:
            user_ids.append(uid)

    total = len(user_ids)
    if total == 0:
        await message.answer("No registered users to broadcast to.")
        return

    status_msg = await message.answer(f"📣 Broadcasting to {total} users...")
    sent = failed = 0
    for uid in user_ids:
        try:
            await message.bot.send_message(uid, body, parse_mode=None)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Broadcast complete</b>\n\n"
        f"📤 Sent: {sent}\n"
        f"❌ Failed: {failed}\n"
        f"👥 Total: {total}",
        parse_mode="HTML",
    )


# ────────────────────────────────────────────────────────────────
# Admin commands — Owner only
# ────────────────────────────────────────────────────────────────

@router.message(Command("addadmin"))
async def cmd_addadmin(message: types.Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Only the Owner can run this command.")
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /addadmin USER_ID")
        return
    target = int(parts[1])
    await users_col.update_one({"telegram_id": target}, {"$set": {"role": "admin"}}, upsert=True)
    await message.answer(f"✅ Admin added: <code>{target}</code>", parse_mode="HTML")


@router.message(Command("removeadmin"))
async def cmd_removeadmin(message: types.Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Only the Owner can run this command.")
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /removeadmin USER_ID")
        return
    target = int(parts[1])
    await users_col.update_one({"telegram_id": target}, {"$set": {"role": "user"}})
    await message.answer(f"✅ Admin removed: <code>{target}</code>", parse_mode="HTML")


@router.message(Command("adminlist"))
async def cmd_adminlist(message: types.Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Only the Owner can run this command.")
        return
    admins = await users_col.find({"role": "admin"}).to_list(None)
    if not admins:
        await message.answer("No admins configured.")
        return
    lines = ["👑 <b>Admin List</b>\n"] + [f"• <code>{a['telegram_id']}</code>" for a in admins]
    await message.answer("\n".join(lines), parse_mode="HTML")


# ────────────────────────────────────────────────────────────────
# Channel management
# ────────────────────────────────────────────────────────────────

@router.message(Command("addchannel"))
async def cmd_addchannel(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("Usage: /addchannel @channelusername OR -100xxxxxxxxxx")
        return

    target = parts[1].strip()
    try:
        chat = await message.bot.get_chat(target)
    except Exception as e:
        await message.answer(f"❌ Could not fetch channel info: {e}")
        return

    if chat.type not in ("channel", "supergroup"):
        await message.answer("❌ The target must be a channel or supergroup.")
        return

    if await channels_col.find_one({"chat_id": chat.id}):
        await message.answer("❌ This channel is already registered.")
        return

    try:
        link_result = await message.bot.create_chat_invite_link(
            chat_id=chat.id,
            name=f"init-{int(datetime.now().timestamp())}",
            creates_join_request=False,      # ← approval-free link
        )
    except Exception as e:
        await message.answer(f"❌ Could not create an invite link: {e}")
        return

    doc = {
        "chat_id": chat.id,
        "title": chat.title,
        "owner_id": message.from_user.id,
        "current_link": link_result.invite_link,
        "raw_link": link_result.invite_link,          # ← raw for revocation
        "link_created_at": datetime.now(timezone.utc),
        "interval_seconds": 300,
        "enabled": True,
        "visibility": "public",
        "short_prefix": None,                          # ← for short-link rotation
        "short_seq": 0,
        "assigned_admins": [],
    }
    await channels_col.insert_one(doc)
    add_rotation_job(message.bot, doc)

    await message.answer(
        f"✅ Channel added: <b>{chat.title}</b>\n"
        f"🔗 Initial link: {link_result.invite_link}\n"
        f"⏱ Default interval: 5 minutes\n"
        f"👁 Visibility: public\n\n"
        f"Optional: enable short-link rotation with\n"
        f"<code>/setshort {chat.id} {chat.username or 'prefix'}_2027</code>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.message(Command("removechannel"))
async def cmd_removechannel(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Usage: /removechannel CHAT_ID")
        return
    chat_id = int(parts[1])
    ch = await channels_col.find_one({"chat_id": chat_id})
    if not ch:
        await message.answer("❌ Channel not found.")
        return
    if ch["owner_id"] != message.from_user.id:
        await message.answer("⛔ Only the channel owner can remove this channel.")
        return
    remove_rotation_job(chat_id)
    await channels_col.delete_one({"chat_id": chat_id})
    await message.answer(f"✅ Channel removed: {ch['title']}")


@router.message(Command("mychannels"))
async def cmd_mychannels(message: types.Message):
    channels = await channels_col.find({
        "$or": [{"owner_id": message.from_user.id},
                {"assigned_admins": message.from_user.id}]
    }).to_list(None)
    if not channels:
        await message.answer("You are not managing any channels yet.")
        return
    lines = ["📋 <b>Your Channels</b>\n"]
    for ch in channels:
        status = "🟢" if ch["enabled"] else "🔴"
        vis = ch.get("visibility", "public")
        sp = ch.get("short_prefix") or "—"
        lines.append(
            f"{status} <b>{ch['title']}</b>\n"
            f"   Chat ID: <code>{ch['chat_id']}</code>\n"
            f"   Interval: {ch['interval_seconds']} s\n"
            f"   Visibility: {vis}\n"
            f"   Short prefix: {sp}\n"
            f"   Link: {ch.get('current_link', 'N/A')}\n"
        )
    await message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


@router.message(Command("settime"))
async def cmd_settime(message: types.Message):
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Usage: /settime CHAT_ID SECONDS\nShortcuts: 5m / 10m / 1h / 24h")
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await message.answer("❌ CHAT_ID must be a number.")
        return
    if not await can_manage(message.from_user.id, chat_id):
        await message.answer("⛔ You do not have permission to manage this channel.")
        return
    val = parts[2]
    if val in INTERVAL_OPTIONS:
        seconds = INTERVAL_OPTIONS[val]
    else:
        try:
            seconds = int(val)
            if seconds < 60:
                await message.answer("❌ Interval must be at least 60 seconds.")
                return
        except ValueError:
            await message.answer("❌ Invalid interval value.")
            return
    ch = await channels_col.find_one_and_update(
        {"chat_id": chat_id},
        {"$set": {"interval_seconds": seconds}},
        return_document=True,
    )
    add_rotation_job(message.bot, ch)
    await message.answer(f"✅ Update interval set to {seconds} seconds.")


@router.message(Command("setlink"))
async def cmd_setlink(message: types.Message):
    parts = message.text.split(maxsplit=2)
    if len(parts) != 3:
        await message.answer("Usage: /setlink CHAT_ID NEW_LINK")
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await message.answer("❌ CHAT_ID must be a number.")
        return
    ch = await channels_col.find_one({"chat_id": chat_id})
    if not ch:
        await message.answer("❌ Channel not found.")
        return
    if ch["owner_id"] != message.from_user.id:
        await message.answer("⛔ Only the channel owner can set the link manually.")
        return
    await channels_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"current_link": parts[2],
                  "link_created_at": datetime.now(timezone.utc)}},
    )
    await message.answer("✅ Link updated.")


@router.message(Command("setpublic"))
async def cmd_setpublic(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Usage: /setpublic CHAT_ID")
        return
    chat_id = int(parts[1])
    ch = await channels_col.find_one({"chat_id": chat_id})
    if not ch or ch["owner_id"] != message.from_user.id:
        await message.answer("⛔ Only the channel owner can change visibility.")
        return
    await channels_col.update_one({"chat_id": chat_id}, {"$set": {"visibility": "public"}})
    await message.answer("✅ Channel is now public.")


@router.message(Command("setprivate"))
async def cmd_setprivate(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Usage: /setprivate CHAT_ID")
        return
    chat_id = int(parts[1])
    ch = await channels_col.find_one({"chat_id": chat_id})
    if not ch or ch["owner_id"] != message.from_user.id:
        await message.answer("⛔ Only the channel owner can change visibility.")
        return
    await channels_col.update_one({"chat_id": chat_id}, {"$set": {"visibility": "private"}})
    await message.answer("✅ Channel is now private.")


@router.message(Command("setshort"))
async def cmd_setshort(message: types.Message):
    """
    Enable short-link rotation.
    Usage: /setshort CHAT_ID PREFIX
    Example: /setshort -1002888391802 pw_yakeen_2_NEET_Lectures_2027
    Each rotation will produce:
      https://is.gd/<PREFIX>cl   (custom alias, 2-char suffix)
      https://is.gd/<PREFIX>da
      ...
    """
    parts = message.text.split(maxsplit=2)
    if len(parts) != 3:
        await message.answer(
            "Usage: <code>/setshort CHAT_ID PREFIX</code>\n\n"
            "Example:\n"
            "<code>/setshort -1002888391802 pw_yakeen_2_NEET_Lectures_2027</code>",
            parse_mode="HTML",
        )
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await message.answer("❌ CHAT_ID must be a number.")
        return
    ch = await channels_col.find_one({"chat_id": chat_id})
    if not ch or ch["owner_id"] != message.from_user.id:
        await message.answer("⛔ Only the channel owner can configure short links.")
        return
    prefix = parts[2].strip()
    if len(prefix) < 5 or len(prefix) > 20:
        await message.answer(
            "❌ Prefix must be between 5 and 20 characters "
            "(is.gd requires 5–30, we cap at 20 to leave room for the suffix)."
        )
        return
    await channels_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"short_prefix": prefix, "short_seq": 0}},
    )
    await message.answer(
        f"✅ Short-link rotation enabled.\n"
        f"Prefix: <code>{prefix}</code>\n"
        f"Next rotation will produce:\n"
        f"<code>https://is.gd/{prefix}aa</code>",
        parse_mode="HTML",
    )


@router.message(Command("setrawlink"))
async def cmd_setrawlink(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Usage: /setrawlink CHAT_ID")
        return
    chat_id = int(parts[1])
    ch = await channels_col.find_one({"chat_id": chat_id})
    if not ch or ch["owner_id"] != message.from_user.id:
        await message.answer("⛔ Only the channel owner can change link mode.")
        return
    await channels_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"short_prefix": None, "short_seq": 0}},
    )
    await message.answer("✅ Short-link rotation disabled. Raw Telegram links will be used.")


@router.message(Command("status"))
async def cmd_status(message: types.Message):
    channels = await channels_col.find({
        "$or": [{"owner_id": message.from_user.id},
                {"assigned_admins": message.from_user.id}]
    }).to_list(None)
    if not channels:
        await message.answer("You are not managing any channels yet.")
        return
    lines = ["📊 <b>Channel Status</b>\n"]
    for ch in channels:
        status = "🟢 Enabled" if ch["enabled"] else "🔴 Disabled"
        vis = ch.get("visibility", "public")
        sp = ch.get("short_prefix") or "off"
        lines.append(
            f"<b>{ch['title']}</b>\n"
            f"  Status: {status}\n"
            f"  Visibility: {vis}\n"
            f"  Interval: {ch['interval_seconds']} s\n"
            f"  Short prefix: {sp}\n"
            f"  Current: {ch.get('current_link', 'N/A')}\n"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


# ────────────────────────────────────────────────────────────────
# /dashboard — Owner only
# ────────────────────────────────────────────────────────────────

@router.message(Command("dashboard"))
async def cmd_dashboard(message: types.Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Only the Owner can access the Dashboard.")
        return

    total_users = await users_col.count_documents({})
    total_channels = await channels_col.count_documents({})
    enabled_channels = await channels_col.count_documents({"enabled": True})
    public_channels = await channels_col.count_documents({"visibility": {"$ne": "private"}})
    jobs = scheduler.get_jobs()
    recent = await logs_col.find().sort("timestamp", -1).limit(10).to_list(None)

    lines = [
        "👑 <b>Owner Dashboard</b>\n",
        f"👥 Registered users: {total_users}",
        f"📢 Total channels: {total_channels}",
        f"🟢 Enabled channels: {enabled_channels}",
        f"🌐 Public channels: {public_channels}",
        f"⚙️ Scheduled jobs: {len(jobs)}\n",
        "<b>Recent activity:</b>",
    ]
    for log in recent:
        ts = log["timestamp"].strftime("%m-%d %H:%M")
        lines.append(f"• {ts} — {log['action']} (ch: {log.get('channel_id', '-')})")
    await message.answer("\n".join(lines), parse_mode="HTML")
