"""
All Telegram handlers for the Auto-Link Bot.
Flat single-module design (no handlers/ package).
"""

import asyncio
from datetime import datetime, timezone

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
    Return channels the given user should see under /start:
      - every channel they own or are assigned to, PLUS
      - every channel marked public.
    """
    return await channels_col.find({
        "enabled": True,
        "$or": [
            {"owner_id": user_id},
            {"assigned_admins": user_id},
            {"visibility": "public"},
        ],
    }).to_list(None)


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

    # Same rule as /start: own/assigned OR public
    ch = await channels_col.find_one({
        "chat_id": chat_id,
        "$or": [
            {"owner_id": user_id},
            {"assigned_admins": user_id},
            {"visibility": "public"},
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
# /help
# ────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    text = (
        "📖 <b>Auto-Link Bot — Command Reference</b>\n\n"
        "👤 <b>Everyone</b>\n"
        "/start — Show the current invite link(s)\n"
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
        "/setprivate CHAT_ID — Hide a channel from non-owners/admins\n\n"
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
    await logs_col.insert_one({
        "actor_id": message.from_user.id,
        "action": "broadcast",
        "channel_id": None,
        "detail": {"sent": sent, "failed": failed, "total": total},
        "timestamp": datetime.now(timezone.utc),
    })


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
        )
    except Exception as e:
        await message.answer(f"❌ Could not create an invite link: {e}")
        return

    doc = {
        "chat_id": chat.id,
        "title": chat.title,
        "owner_id": message.from_user.id,
        "current_link": link_result.invite_link,
        "link_created_at": datetime.now(timezone.utc),
        "interval_seconds": 300,
        "enabled": True,
        "visibility": "public",     # ← NEW — every /addchannel is public by default
        "assigned_admins": [],
    }
    await channels_col.insert_one(doc)
    add_rotation_job(message.bot, doc)

    await logs_col.insert_one({
        "actor_id": message.from_user.id,
        "action": "add_channel",
        "channel_id": chat.id,
        "detail": {"title": chat.title},
        "timestamp": datetime.now(timezone.utc),
    })
    await message.answer(
        f"✅ Channel added: <b>{chat.title}</b>\n"
        f"🔗 Initial link: {link_result.invite_link}\n"
        f"⏱ Default interval: 5 minutes\n"
        f"👁 Visibility: public — every user who sends /start will see this link.\n"
        f"Use /setprivate to hide it, /settime to change interval.",
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
        lines.append(
            f"{status} <b>{ch['title']}</b>\n"
            f"   Chat ID: <code>{ch['chat_id']}</code>\n"
            f"   Interval: {ch['interval_seconds']} s\n"
            f"   Visibility: {vis}\n"
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
    if not ch:
        await message.answer("❌ Channel not found.")
        return
    if ch["owner_id"] != message.from_user.id:
        await message.answer("⛔ Only the channel owner can change visibility.")
        return
    await channels_col.update_one({"chat_id": chat_id}, {"$set": {"visibility": "public"}})
    await message.answer("✅ Channel is now public — all users who send /start will see it.")


@router.message(Command("setprivate"))
async def cmd_setprivate(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Usage: /setprivate CHAT_ID")
        return
    chat_id = int(parts[1])
    ch = await channels_col.find_one({"chat_id": chat_id})
    if not ch:
        await message.answer("❌ Channel not found.")
        return
    if ch["owner_id"] != message.from_user.id:
        await message.answer("⛔ Only the channel owner can change visibility.")
        return
    await channels_col.update_one({"chat_id": chat_id}, {"$set": {"visibility": "private"}})
    await message.answer("✅ Channel is now private — only you and its assigned admins will see it.")


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
        lines.append(
            f"<b>{ch['title']}</b>\n"
            f"  Status: {status}\n"
            f"  Visibility: {vis}\n"
            f"  Interval: {ch['interval_seconds']} s\n"
            f"  Link created at: {ch.get('link_created_at', 'Unknown')}\n"
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
    public_channels = await channels_col.count_documents({"visibility": "public"})
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
