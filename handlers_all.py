"""
Auto-Link Bot — all handlers in one flat module.

Role rules:
  - Owner and Admins may run every command.
  - Regular users may run ONLY /start.
    Any other command replies "not authorized".

/start behavior:
  - Owner / Admin → NEVER shows their own channels.
                    Shows ONLY public channels of OTHER users.
                    If none exist → "No public channels are available right now."
  - Regular user  → shows ALL public channels.
  - If no public channels exist for a regular user
    → "No public channels are available right now."
"""

import asyncio
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
# Role helpers
# ────────────────────────────────────────────────────────────────

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def is_admin(user_id: int) -> bool:
    doc = await users_col.find_one({"telegram_id": user_id, "role": "admin"})
    return doc is not None


async def has_privileged_role(user_id: int) -> bool:
    """Owner or Admin → may use every command."""
    if is_owner(user_id):
        return True
    return await is_admin(user_id)


async def can_manage(user_id: int, chat_id: int) -> bool:
    ch = await channels_col.find_one({
        "chat_id": chat_id,
        "$or": [{"owner_id": user_id}, {"assigned_admins": user_id}],
    })
    return ch is not None


# ────────────────────────────────────────────────────────────────
# Authorization gate — runs before every command except /start
# ────────────────────────────────────────────────────────────────

GATE_EXEMPT = {"start"}  # /start is always allowed


async def _role_gate(message: types.Message) -> bool:
    """
    Returns True if the caller may proceed, False if we already replied.
    """
    # Skip commands that don't start with '/'
    if not message.text or not message.text.startswith("/"):
        return True

    # Extract command name (e.g. "/help@MyBot" → "help")
    cmd = message.text.split()[0][1:].split("@")[0].lower()
    if cmd in GATE_EXEMPT:
        return True

    if await has_privileged_role(message.from_user.id):
        return True

    await message.answer(
        "⛔ You are not authorized to use this command.\n\n"
        "Only the bot Owner and Admins can manage channels.\n"
        "Send /start to see available channels."
    )
    return False


# ────────────────────────────────────────────────────────────────
# Visibility helpers
# ────────────────────────────────────────────────────────────────

async def public_channels_for(user_id: int, exclude_own: bool):
    """
    Return the list of public channels this user should see.

    If exclude_own is True (Owner/Admin), skip channels they own or are
    assigned to — so owners always see "no public channels" for /start
    unless another user has added a public channel.
    """
    query = {
        "enabled": True,
        "visibility": {"$ne": "private"},
        "current_link": {"$exists": True, "$ne": None},
    }
    if exclude_own:
        query["$and"] = [
            {"owner_id": {"$ne": user_id}},
            {"assigned_admins": {"$ne": user_id}},
        ]
    return await channels_col.find(query).to_list(None)


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

    privileged = await has_privileged_role(user_id)
    channels = await public_channels_for(user_id, exclude_own=privileged)

    if not channels:
        # Owner/Admin: hint to add a channel. Regular user: same text.
        if privileged:
            await message.answer(
                "👋 Welcome to Auto-Link Bot!\n\n"
                "No public channels are available right now.\n"
                "If you own a channel, use /addchannel to register it.\n"
                "Use /mychannels to see the channels you manage."
            )
        else:
            await message.answer(
                "👋 Welcome to Auto-Link Bot!\n\n"
                "No public channels are available right now.\n"
                "Please try again later."
            )
        return

    if len(channels) == 1:
        ch = channels[0]
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🚀 Join Channel", url=ch["current_link"])
        ]])
        await message.answer(
            f"👋 Welcome to Auto-Link Bot!\n\n"
            f"📢 Channel: {ch['title']}\n\n"
            f"🔗 Current Link:\n{ch['current_link']}\n\n"
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
    if not ch or not ch.get("current_link"):
        await callback.answer("Channel not available.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Join Channel", url=ch["current_link"])
    ]])
    await callback.message.edit_text(
        f"📢 Channel: {ch['title']}\n\n"
        f"🔗 Current Link:\n{ch['current_link']}\n\n"
        f"👇 Click below to join!",
        reply_markup=kb,
        disable_web_page_preview=True,
    )
    await callback.answer()


# ────────────────────────────────────────────────────────────────
# /help
# ────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    if not await _role_gate(message):
        return

    text = (
        "📖 <b>Auto-Link Bot — Command Reference</b>\n\n"
        "📢 <b>Channel Management</b>\n"
        "/addchannel @username — Register a channel\n"
        "/addchannel -100xxxxxxxxxx — Register by chat ID\n"
        "/removechannel CHAT_ID — Unregister (owner of that channel)\n"
        "/settime CHAT_ID VALUE — Rotation interval (5m / 10m / 1h / 24h / seconds)\n"
        "/setlink CHAT_ID LINK — Manual override (channel owner)\n"
        "/setpublic CHAT_ID — Show to all users\n"
        "/setprivate CHAT_ID — Hide from regular users\n"
        "/setshort CHAT_ID PREFIX — Enable short-link rotation\n"
        "/setrawlink CHAT_ID — Disable short-link rotation\n"
        "/mychannels — Channels you manage\n"
        "/status — Status of channels you manage\n\n"
    )
    if is_owner(message.from_user.id):
        text += (
            "👑 <b>Owner Only</b>\n"
            "/addadmin USER_ID\n"
            "/removeadmin USER_ID\n"
            "/adminlist\n"
            "/dashboard\n"
            "/broadcast MESSAGE\n\n"
        )
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


# ────────────────────────────────────────────────────────────────
# /broadcast — Owner only
# ────────────────────────────────────────────────────────────────

@router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if not await _role_gate(message):
        return
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
    if not await _role_gate(message):
        return
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
    if not await _role_gate(message):
        return
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
    if not await _role_gate(message):
        return
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
    if not await _role_gate(message):
        return
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
            creates_join_request=False,
        )
    except Exception as e:
        await message.answer(f"❌ Could not create an invite link: {e}")
        return
    doc = {
        "chat_id": chat.id,
        "title": chat.title,
        "owner_id": message.from_user.id,
        "current_link": link_result.invite_link,
        "raw_link": link_result.invite_link,
        "link_created_at": datetime.now(timezone.utc),
        "interval_seconds": 300,
        "enabled": True,
        "visibility": "public",
        "short_prefix": None,
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
    if not await _role_gate(message):
        return
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
    if not await _role_gate(message):
        return
    channels = await channels_col.find({
        "$or": [{"owner_id": message.from_user.id},
                {"assigned_admins": message.from_user.id}]
    }).to_list(None)
    if not channels:
        await message.answer("You are not managing any channels yet. Use /addchannel.")
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
    if not await _role_gate(message):
        return
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
    if not await _role_gate(message):
        return
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
    if not await _role_gate(message):
        return
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
    if not await _role_gate(message):
        return
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
    if not await _role_gate(message):
        return
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
    if not (5 <= len(prefix) <= 20):
        await message.answer("❌ Prefix must be 5–20 characters.")
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
    if not await _role_gate(message):
        return
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
    if not await _role_gate(message):
        return
    channels = await channels_col.find({
        "$or": [{"owner_id": message.from_user.id},
                {"assigned_admins": message.from_user.id}]
    }).to_list(None)
    if not channels:
        await message.answer("You are not managing any channels yet. Use /addchannel.")
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
    if not await _role_gate(message):
        return
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
