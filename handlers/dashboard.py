from aiogram import Router, types
from aiogram.filters import Command
from config import OWNER_ID
from database import users_col, channels_col, logs_col
from scheduler import scheduler

router = Router()
# ... cmd_dashboard ...

@router.message(Command("dashboard"))
async def cmd_dashboard(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Only the Owner can access the Dashboard.")
        return

    total_users = await users_col.count_documents({})
    total_channels = await channels_col.count_documents({})
    enabled_channels = await channels_col.count_documents({"enabled": True})
    jobs = scheduler.get_jobs()

    recent_logs = await logs_col.find().sort("timestamp", -1).limit(10).to_list(None)

    lines = [
        "👑 <b>Owner Dashboard</b>\n",
        f"👥 Registered users: {total_users}",
        f"📢 Total channels: {total_channels}",
        f"🟢 Enabled channels: {enabled_channels}",
        f"⚙️ Scheduled jobs: {len(jobs)}\n",
        "<b>Recent activity:</b>",
    ]
    for log in recent_logs:
        ts = log["timestamp"].strftime("%m-%d %H:%M")
        lines.append(f"• {ts} — {log['action']} (ch: {log.get('channel_id', '-')})")

    await message.answer("\n".join(lines), parse_mode="HTML")
