from aiogram import Router, types
from aiogram.filters import Command
from config import OWNER_ID
from database import users_col, logs_col
from datetime import datetime, timezone

router = Router()
# ... cmd_addadmin, cmd_removeadmin, cmd_adminlist ...

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID

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
    await users_col.update_one(
        {"telegram_id": target},
        {"$set": {"role": "admin"}},
        upsert=True,
    )
    await logs_col.insert_one({
        "actor_id": message.from_user.id,
        "action": "add_admin",
        "channel_id": None,
        "detail": {"target": target},
        "timestamp": datetime.now(timezone.utc),
    })
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
    await users_col.update_one(
        {"telegram_id": target},
        {"$set": {"role": "user"}},
    )
    await logs_col.insert_one({
        "actor_id": message.from_user.id,
        "action": "remove_admin",
        "channel_id": None,
        "detail": {"target": target},
        "timestamp": datetime.now(timezone.utc),
    })
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

    lines = ["👑 <b>Admin List</b>\n"]
    for a in admins:
        lines.append(f"• <code>{a['telegram_id']}</code>")
    await message.answer("\n".join(lines), parse_mode="HTML")
