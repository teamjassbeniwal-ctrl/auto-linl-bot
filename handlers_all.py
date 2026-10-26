"""
Auto-Link Bot — all handlers in one flat module.

Roles:
  Owner : OWNER_ID from env — full access, cannot be demoted
  Admin : identical to Owner, except cannot demote the Owner
  User  : may run ONLY /start
"""

import asyncio
from datetime import datetime, timezone

from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import OWNER_ID, INTERVAL_OPTIONS
from database import channels_col, users_col, logs_col, shortlinks_col
from scheduler import (
    add_rotation_job,
    remove_rotation_job,
    scheduler,
    SHORT_BASE,
)


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
    return is_owner(user_id) or await is_admin(user_id)


# ────────────────────────────────────────────────────────────────
# Gate — blocks regular users from any command except /start
# ────────────────────────────────────────────────────────────────

GATE_EXEMPT = {"start"}


async def _role_gate(message: types.Message) -> bool:
    if not message.text or not message.text.startswith("/"):
        return True
    cmd = message.text.split()[0][1:].split("@")[0].lower()
    if cmd in GATE_EXEMPT:
        return True
    if await has_privileged_role(message.from_user.id):
        return True
    await message.answer(
        "⛔ You are not authorized to use this command.\n\n"
        "Send /start to get the channel link."
    )
    return False


# ────────────────────────────────────────────────────────────────
# /start — everyone
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

    channels = await channels_col.find({
        "enabled": True,
        "visibility": {"$ne": "private"},
        "current_link": {"$exists": True, "$ne": None},
    }).to_list(None)

    if not channels:
        await message.answer(
            "👋 Welcome to Auto-Link Bot!\n\n"
            "No public channels are available right now."
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
    ch = await channels_col.find_one({
        "chat_id": chat_id,
        "visibility": {"$ne": "private"},
        "current_link": {"$exists": True, "$ne": None},
    })
    if not ch:
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
# /help — Owner + Admin
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
        "/removechannel CHAT_ID — Unregister a channel\n"
        "/settime CHAT_ID VALUE — Rotation interval (5m / 10m / 1h / 24h / seconds)\n"
        "/setlink CHAT_ID LINK — Manually override the link\n"
        "/setpublic CHAT_ID — Show to all users\n"
        "/setprivate CHAT_ID — Hide from regular users\n"
        "/setshort CHAT_ID PREFIX — Enable short-link rotation (5–100 chars)\n"
        "/setrawlink CHAT_ID — Disable short-link rotation\n"
        "/shorten CHAT_ID ALIAS — Create a fixed custom short link (5–100 chars)\n"
        "/mychannels — List every channel\n"
        "/status — Status of every channel\n\n"
        "👑 <b>Admin Management</b>\n"
        "/addadmin USER_ID — Promote user to admin\n"
        "/removeadmin USER_ID — Demote admin (Owner cannot be demoted)\n"
        "/adminlist — List all admins\n"
        "/dashboard — Bot statistics\n"
        "/broadcast MESSAGE — Message every user\n"
    )
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


# ────────────────────────────────────────────────────────────────
# /addadmin — Owner + Admin
# ────────────────────────────────────────────────────────────────

@router.message(Command("addadmin"))
async def cmd_addadmin(message: types.Message):
    if not await _role_gate(message):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /addadmin USER_ID")
        return
    target = int(parts[1])
    if target == OWNER_ID:
        await message.answer("That user is already the Owner.")
        return
    await users_col.update_one(
        {"telegram_id": target},
        {"$set": {"role": "admin"}},
        upsert=True,
    )
    await message.answer(f"✅ Admin added: <code>{target}</code>", parse_mode="HTML")


# ────────────────────────────────────────────────────────────────
# /removeadmin — Owner + Admin (cannot demote Owner)
# ────────────────────────────────────────────────────────────────

@router.message(Command("removeadmin"))
async def cmd_removeadmin(message: types.Message):
    if not await _role_gate(message):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Usage: /removeadmin USER_ID")
        return
    target = int(parts[1])
    if target == OWNER_ID:
        await message.answer("⛔ The Owner cannot be demoted.")
        return
    await users_col.update_one({"telegram_id": target}, {"$set": {"role": "user"}})
    await message.answer(f"✅ Admin removed: <code>{target}</code>", parse_mode="HTML")


# ────────────────────────────────────────────────────────────────
# /adminlist — Owner + Admin
# ────────────────────────────────────────────────────────────────

@router.message(Command("adminlist"))
async def cmd_adminlist(message: types.Message):
    if not await _role_gate(message):
        return
    admins = await users_col.find({"role": "admin"}).to_list(None)
    if not admins:
        await message.answer("No admins configured.")
        return
    lines = ["👑 <b>Admin List</b>\n"] + [
        f"• <code>{a['telegram_id']}</code>" for a in admins
    ]
    await message.answer("\n".join(lines), parse_mode="HTML")


# ────────────────────────────────────────────────────────────────
# /broadcast — Owner + Admin
# ────────────────────────────────────────────────────────────────

@router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if not await _role_gate(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer(
            "Usage: <code>/broadcast YOUR MESSAGE</code>",
            parse_mode="HTML",
        )
        return

    body = parts[1].strip()
    user_ids = []
    async for u in users_col.find({}, {"telegram_id": 1}):
        uid = u.get("telegram_id")
        if uid:
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
# /addchannel — Owner + Admin
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


# ────────────────────────────────────────────────────────────────
# /removechannel — Owner + Admin
# ────────────────────────────────────────────────────────────────

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
    remove_rotation_job(chat_id)
    await channels_col.delete_one({"chat_id": chat_id})
    await message.answer(f"✅ Channel removed: {ch['title']}")


# ────────────────────────────────────────────────────────────────
# /mychannels — Owner + Admin
# ────────────────────────────────────────────────────────────────

@router.message(Command("mychannels"))
async def cmd_mychannels(message: types.Message):
    if not await _role_gate(message):
        return
    channels = await channels_col.find({}).to_list(None)
    if not channels:
        await message.answer("No channels registered yet. Use /addchannel.")
        return
    lines = ["📋 <b>All Channels</b>\n"]
    for ch in channels:
        status = "🟢" if ch["enabled"] else "🔴"
        vis = ch.get("visibility", "public")
        sp = ch.get("short_prefix") or "—"
        lines.append(
            f"{status} <b>{ch['title']}</b>\n"
            f"   Chat ID: <code>{ch['chat_id']}</code>\n"
            f"   Owner: <code>{ch['owner_id']}</code>\n"
            f"   Interval: {ch['interval_seconds']} s\n"
            f"   Visibility: {vis}\n"
            f"   Short prefix: {sp}\n"
            f"   Link: {ch.get('current_link', 'N/A')}\n"
        )
    await message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


# ────────────────────────────────────────────────────────────────
# /settime — Owner + Admin
# ────────────────────────────────────────────────────────────────

@router.message(Command("settime"))
async def cmd_settime(message: types.Message):
    if not await _role_gate(message):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer(
            "Usage: /settime CHAT_ID SECONDS\nShortcuts: 5m / 10m / 1h / 24h"
        )
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await message.answer("❌ CHAT_ID must be a number.")
        return
    if not await channels_col.find_one({"chat_id": chat_id}):
        await message.answer("❌ Channel not found.")
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


# ────────────────────────────────────────────────────────────────
# /setlink — Owner + Admin
# ────────────────────────────────────────────────────────────────

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
    if not await channels_col.find_one({"chat_id": chat_id}):
        await message.answer("❌ Channel not found.")
        return
    await channels_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"current_link": parts[2],
                  "link_created_at": datetime.now(timezone.utc)}},
    )
    await message.answer("✅ Link updated.")


# ────────────────────────────────────────────────────────────────
# /setpublic — Owner + Admin
# ────────────────────────────────────────────────────────────────

@router.message(Command("setpublic"))
async def cmd_setpublic(message: types.Message):
    if not await _role_gate(message):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Usage: /setpublic CHAT_ID")
        return
    chat_id = int(parts[1])
    if not await channels_col.find_one({"chat_id": chat_id}):
        await message.answer("❌ Channel not found.")
        return
    await channels_col.update_one({"chat_id": chat_id}, {"$set": {"visibility": "public"}})
    await message.answer("✅ Channel is now public.")


# ────────────────────────────────────────────────────────────────
# /setprivate — Owner + Admin
# ────────────────────────────────────────────────────────────────

@router.message(Command("setprivate"))
async def cmd_setprivate(message: types.Message):
    if not await _role_gate(message):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Usage: /setprivate CHAT_ID")
        return
    chat_id = int(parts[1])
    if not await channels_col.find_one({"chat_id": chat_id}):
        await message.answer("❌ Channel not found.")
        return
    await channels_col.update_one({"chat_id": chat_id}, {"$set": {"visibility": "private"}})
    await message.answer("✅ Channel is now private.")


# ────────────────────────────────────────────────────────────────
# /setshort — Owner + Admin (prefix 5–100 characters)
# ────────────────────────────────────────────────────────────────

@router.message(Command("setshort"))
async def cmd_setshort(message: types.Message):
    if not await _role_gate(message):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) != 3:
        await message.answer(
            "Usage: <code>/setshort CHAT_ID PREFIX</code>",
            parse_mode="HTML",
        )
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await message.answer("❌ CHAT_ID must be a number.")
        return
    if not await channels_col.find_one({"chat_id": chat_id}):
        await message.answer("❌ Channel not found.")
        return
    prefix = parts[2].strip()
    if not (5 <= len(prefix) <= 100):
        await message.answer("❌ Prefix must be 5–100 characters.")
        return
    await channels_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"short_prefix": prefix, "short_seq": 0}},
    )
    await message.answer(
        f"✅ Short-link rotation enabled.\n"
        f"Prefix: <code>{prefix}</code> ({len(prefix)} chars)\n"
        f"Next rotation will produce:\n"
        f"<code>{SHORT_BASE}/{prefix}aa</code>",
        parse_mode="HTML",
    )


# ────────────────────────────────────────────────────────────────
# /setrawlink — Owner + Admin
# ────────────────────────────────────────────────────────────────

@router.message(Command("setrawlink"))
async def cmd_setrawlink(message: types.Message):
    if not await _role_gate(message):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Usage: /setrawlink CHAT_ID")
        return
    chat_id = int(parts[1])
    if not await channels_col.find_one({"chat_id": chat_id}):
        await message.answer("❌ Channel not found.")
        return
    await channels_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"short_prefix": None, "short_seq": 0}},
    )
    await message.answer("✅ Short-link rotation disabled. Raw Telegram links will be used.")


# ────────────────────────────────────────────────────────────────
# /shorten — Owner + Admin (custom alias, 5–100 characters)
# ────────────────────────────────────────────────────────────────

@router.message(Command("shorten"))
async def cmd_shorten(message: types.Message):
    if not await _role_gate(message):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Usage: /shorten CHAT_ID ALIAS")
        return
    try:
        chat_id = int(parts[1])
    except ValueError:
        await message.answer("❌ CHAT_ID must be a number.")
        return
    alias = parts[2].strip()
    if not (5 <= len(alias) <= 100) or not alias.replace("_", "").isalnum():
        await message.answer(
            "❌ Alias must be 5–100 characters and only letters, digits, or underscores."
        )
        return
    ch = await channels_col.find_one({"chat_id": chat_id})
    if not ch:
        await message.answer("❌ Channel not found.")
        return
    if not ch.get("current_link"):
        await message.answer("❌ Channel has no current link yet.")
        return
    await shortlinks_col.update_one(
        {"alias": alias},
        {"$set": {"target": ch["current_link"], "updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    await message.answer(
        f"✅ Short link created:\n"
        f"<code>{SHORT_BASE}/{alias}</code>\n"
        f"→ {ch['current_link']}",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ────────────────────────────────────────────────────────────────
# /status — Owner + Admin
# ────────────────────────────────────────────────────────────────

@router.message(Command("status"))
async def cmd_status(message: types.Message):
    if not await _role_gate(message):
        return
    channels = await channels_col.find({}).to_list(None)
    if not channels:
        await message.answer("No channels registered yet. Use /addchannel.")
        return
    lines = ["📊 <b>Channel Status</b>\n"]
    for ch in channels:
        status = "🟢 Enabled" if ch["enabled"] else "🔴 Disabled"
        vis = ch.get("visibility", "public")
        sp = ch.get("short_prefix") or "off"
        lines.append(
            f"<b>{ch['title']}</b>\n"
            f"  Chat ID: <code>{ch['chat_id']}</code>\n"
            f"  Owner: <code>{ch['owner_id']}</code>\n"
            f"  Status: {status}\n"
            f"  Visibility: {vis}\n"
            f"  Interval: {ch['interval_seconds']} s\n"
            f"  Short prefix: {sp}\n"
            f"  Current: {ch.get('current_link', 'N/A')}\n"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


# ────────────────────────────────────────────────────────────────
# /dashboard — Owner + Admin
# ────────────────────────────────────────────────────────────────

@router.message(Command("dashboard"))
async def cmd_dashboard(message: types.Message):
    if not await _role_gate(message):
        return
    total_users = await users_col.count_documents({})
    total_admins = await users_col.count_documents({"role": "admin"})
    total_channels = await channels_col.count_documents({})
    enabled_channels = await channels_col.count_documents({"enabled": True})
    public_channels = await channels_col.count_documents({"visibility": {"$ne": "private"}})
    jobs = scheduler.get_jobs()
    recent = await logs_col.find().sort("timestamp", -1).limit(10).to_list(None)

    lines = [
        "👑 <b>Dashboard</b>\n",
        f"👥 Registered users: {total_users}",
        f"🔧 Admins: {total_admins}",
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
