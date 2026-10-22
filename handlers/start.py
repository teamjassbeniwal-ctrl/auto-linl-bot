from aiogram import Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import channels_col, users_col, logs_col
from config import OWNER_ID
from datetime import datetime, timezone
import asyncio

router = Router()


# ─────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────
@router.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id

    # Record the user on first contact
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

    # Find all channels this user can manage (owner or assigned admin)
    accessible = await channels_col.find({
        "$or": [
            {"owner_id": user_id},
            {"assigned_admins": user_id},
        ],
        "enabled": True,
    }).to_list(None)

    if not accessible:
        await message.answer(
            "👋 Welcome to Auto-Link Bot!\n\n"
            "You are not managing any channels yet.\n"
            "Use /addchannel to add one, or /help to see all commands."
        )
        return

    if len(accessible) == 1:
        ch = accessible[0]
        link = ch.get("current_link", "Link not generated yet")
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
        # Multiple channels: let the user pick one
        buttons = []
        for ch in accessible:
            buttons.append([InlineKeyboardButton(
                text=ch["title"],
                callback_data=f"view_ch:{ch['chat_id']}"
            )])
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
        "$or": [{"owner_id": user_id}, {"assigned_admins": user_id}],
    })
    if not ch:
        await callback.answer("Access denied.", show_alert=True)
        return

    link = ch.get("current_link", "Link not generated yet")
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


# ─────────────────────────────────────────────────────────
# /help — full command reference for everyone
# ─────────────────────────────────────────────────────────
@router.message(Command("help"))
async def cmd_help(message: types.Message):
    user_id = message.from_user.id
    is_owner = (user_id == OWNER_ID)

    text = (
        "📖 <b>Auto-Link Bot — Command Reference</b>\n\n"

        "👤 <b>Everyone</b>\n"
        "/start — Show the current invite link of your channel(s)\n"
        "/help — Show this help message\n"
        "/mychannels — List channels you own or are assigned to\n"
        "/status — Show status of the channels you manage\n\n"

        "📢 <b>Channel Management</b> (owner or assigned admin)\n"
        "/addchannel @username — Register a channel\n"
        "/addchannel -100xxxxxxxxxx — Register by chat ID\n"
        "/removechannel CHAT_ID — Unregister a channel (owner only)\n"
        "/settime CHAT_ID VALUE — Change rotation interval\n"
        "   • Shortcuts: 5m, 10m, 1h, 24h\n"
        "   • Or any number of seconds ≥ 60\n"
        "/setlink CHAT_ID LINK — Manually override the link (owner only)\n\n"
    )

    if is_owner:
        text += (
            "👑 <b>Owner Only</b>\n"
            "/addadmin USER_ID — Promote a user to admin\n"
            "/removeadmin USER_ID — Demote an admin\n"
            "/adminlist — List all admins\n"
            "/dashboard — Bot statistics &amp; recent activity\n"
            "/broadcast MESSAGE — Send a message to every registered user\n\n"
        )

    text += (
        "🔗 <b>Quick example</b>\n"
        "1. /addchannel @my_channel\n"
        "2. /settime -1001234567890 10m\n"
        "3. Share the bot with your audience → they send /start\n"
    )

    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)


# ─────────────────────────────────────────────────────────
# /broadcast — Owner only, sends a message to all users
# ─────────────────────────────────────────────────────────
@router.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Only the Owner can run this command.")
        return

    # Extract the message body after "/broadcast"
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer(
            "Usage: <code>/broadcast YOUR MESSAGE</code>\n\n"
            "Example:\n"
            "<code>/broadcast The bot will be down for maintenance tonight at 11 PM UTC.</code>",
            parse_mode="HTML",
        )
        return

    body = parts[1].strip()

    # Fetch every registered user
    user_ids = []
    async for u in users_col.find({}, {"telegram_id": 1}):
        uid = u.get("telegram_id")
        if uid and uid != OWNER_ID:   # skip the owner to avoid self-spam
            user_ids.append(uid)

    total = len(user_ids)
    if total == 0:
        await message.answer("No registered users to broadcast to.")
        return

    status_msg = await message.answer(
        f"📣 Starting broadcast to {total} users..."
    )

    sent, failed = 0, 0

    for uid in user_ids:
        try:
            await message.bot.send_message(uid, body)
            sent += 1
        except Exception:
            failed += 1
        # Small delay to respect Telegram rate limits (~30 msg/sec)
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Broadcast complete</b>\n\n"
        f"📤 Sent: {sent}\n"
        f"❌ Failed: {failed}\n"
        f"👥 Total: {total}",
        parse_mode="HTML",
    )

    # Log the broadcast
    await logs_col.insert_one({
        "actor_id": message.from_user.id,
        "action": "broadcast",
        "channel_id": None,
        "detail": {"sent": sent, "failed": failed, "total": total},
        "timestamp": datetime.now(timezone.utc),
    })
