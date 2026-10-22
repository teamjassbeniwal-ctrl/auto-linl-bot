from aiogram import Router, types
from aiogram.filters import Command
from database import channels_col, logs_col
from scheduler import add_rotation_job, remove_rotation_job
from config import INTERVAL_OPTIONS
from datetime import datetime, timezone

router = Router()

async def can_manage(user_id: int, chat_id: int) -> bool:
    """Check if the user owns or is assigned to the channel."""
    ch = await channels_col.find_one({
        "chat_id": chat_id,
        "$or": [{"owner_id": user_id}, {"assigned_admins": user_id}],
    })
    return ch is not None

@router.message(Command("addchannel"))
async def cmd_addchannel(message: types.Message):
    """Usage: /addchannel @channelusername OR /addchannel -1001234567890"""
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        await message.answer(
            "Usage: /addchannel @channelusername or /addchannel -100xxxxxxxxxx"
        )
        return

    target = parts[1].strip()
    try:
        chat = await message.bot.get_chat(target)
    except Exception as e:
        await message.answer(
            f"❌ Could not fetch channel info: {e}\n"
            "Make sure the bot is an administrator in that channel."
        )
        return

    if chat.type not in ("channel", "supergroup"):
        await message.answer("❌ The target must be a channel or supergroup.")
        return

    existing = await channels_col.find_one({"chat_id": chat.id})
    if existing:
        await message.answer("❌ This channel is already registered.")
        return

    # Create the initial invite link
    try:
        link_result = await message.bot.create_chat_invite_link(
            chat_id=chat.id,
            name=f"init-{int(datetime.now().timestamp())}",
        )
    except Exception as e:
        await message.answer(
            f"❌ Could not create an invite link: {e}\n"
            "Make sure the bot has the 'Invite Users' permission."
        )
        return

    doc = {
        "chat_id": chat.id,
        "title": chat.title,
        "owner_id": message.from_user.id,
        "current_link": link_result.invite_link,
        "link_created_at": datetime.now(timezone.utc),
        "interval_seconds": 300,  # default: 5 minutes
        "enabled": True,
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
        f"Use /settime to change the interval.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

@router.message(Command("removechannel"))
async def cmd_removechannel(message: types.Message):
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Usage: /removechannel CHAT_ID")
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
        await message.answer("⛔ Only the channel owner can remove this channel.")
        return

    remove_rotation_job(chat_id)
    await channels_col.delete_one({"chat_id": chat_id})
    await logs_col.insert_one({
        "actor_id": message.from_user.id,
        "action": "remove_channel",
        "channel_id": chat_id,
        "detail": {},
        "timestamp": datetime.now(timezone.utc),
    })
    await message.answer(f"✅ Channel removed: {ch['title']}")

@router.message(Command("mychannels"))
async def cmd_mychannels(message: types.Message):
    user_id = message.from_user.id
    channels = await channels_col.find({
        "$or": [{"owner_id": user_id}, {"assigned_admins": user_id}]
    }).to_list(None)

    if not channels:
        await message.answer("You are not managing any channels yet.")
        return

    lines = ["📋 <b>Your Channels</b>\n"]
    for ch in channels:
        status = "🟢" if ch["enabled"] else "🔴"
        lines.append(
            f"{status} <b>{ch['title']}</b>\n"
            f"   Chat ID: <code>{ch['chat_id']}</code>\n"
            f"   Interval: {ch['interval_seconds']} seconds\n"
            f"   Link: {ch.get('current_link', 'N/A')}\n"
        )
    await message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)

@router.message(Command("settime"))
async def cmd_settime(message: types.Message):
    """Usage: /settime CHAT_ID 300 OR /settime CHAT_ID 5m"""
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer(
            "Usage: /settime CHAT_ID SECONDS\n"
            "Shortcuts: 5m / 10m / 1h / 24h\n"
            "Example: /settime -1001234567890 600"
        )
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
                await message.answer("❌ The interval must be at least 60 seconds.")
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
    """Manually override the current invite link (owner only)."""
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
        {"$set": {"current_link": parts[2], "link_created_at": datetime.now(timezone.utc)}},
    )
    await message.answer("✅ Link updated.")

@router.message(Command("status"))
async def cmd_status(message: types.Message):
    user_id = message.from_user.id
    channels = await channels_col.find({
        "$or": [{"owner_id": user_id}, {"assigned_admins": user_id}]
    }).to_list(None)

    if not channels:
        await message.answer("You are not managing any channels yet.")
        return

    lines = ["📊 <b>Channel Status</b>\n"]
    for ch in channels:
        status = "🟢 Enabled" if ch["enabled"] else "🔴 Disabled"
        lines.append(
            f"<b>{ch['title']}</b>\n"
            f"  Status: {status}\n"
            f"  Interval: {ch['interval_seconds']} seconds\n"
            f"  Link created at: {ch.get('link_created_at', 'Unknown')}\n"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")
