from aiogram import Router, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import channels_col, users_col, logs_col
from datetime import datetime, timezone

router = Router()

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
            "Use /addchannel to add one."
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
