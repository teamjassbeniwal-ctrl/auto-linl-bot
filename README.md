# 📩 Telegram Auto-Link Bot

A production-ready Telegram bot that manages multiple channels and automatically rotates their invite links on a configurable schedule. Built with **aiogram 3**, **MongoDB (Motor)**, and **APScheduler**, and designed to run continuously on **Koyeb**.

---

## Table of Contents

1. [Features](#-features)
2. [Architecture](#-architecture)
3. [Project Structure](#-project-structure)
4. [Prerequisites](#-prerequisites)
5. [Installation (Local)](#-installation-local)
6. [Deployment on Koyeb](#-deployment-on-koyeb)
7. [Environment Variables](#-environment-variables)
8. [Database Schema](#-database-schema)
9. [How Link Rotation Works](#-how-link-rotation-works)
10. [Command Reference](#-command-reference)
11. [Permissions Matrix](#-permissions-matrix)
12. [Usage Walkthrough](#-usage-walkthrough)
13. [Testing Guide](#-testing-guide)
14. [Troubleshooting](#-troubleshooting)
15. [Security Notes](#-security-notes)
16. [License & Legal Use](#-license--legal-use)

---

## ✨ Features

- **Multi-channel management** — one bot instance serves unlimited channels.
- **Multi-user isolation** — each user sees only their own channels and links.
- **Automatic invite-link rotation** on 5m / 10m / 1h / 24h / custom intervals.
- **Role-based access control** — Owner, Admin, User.
- **Persistent storage** — all data survives restarts and redeploys.
- **Crash-safe scheduler** — jobs are rebuilt from MongoDB on every startup, so no duplicates and no losses.
- **Owner dashboard** — user count, channel count, scheduled job count, recent activity.
- **Health-check endpoint** (`/health`) for Koyeb and other PaaS providers.
- **Full English UI** — every message, button, and log line is English-only.

---

## 🏗 Architecture

```

┌───────────────────────┐
│   Telegram Bot API    │
└──────────┬────────────┘
│ long polling
┌──────────▼────────────┐        ┌────────────────────┐
│  aiogram Dispatcher   │◄──────►│  APScheduler       │
│  (handlers/*)         │        │  (rotation jobs)   │
└──────────┬────────────┘        └──────────┬─────────┘
│                                 │
└────────────┬────────────────────┘
│
┌───────▼────────┐
│    MongoDB     │
│  (Motor async) │
└───────┬────────┘
│
┌───────▼────────┐
│  /health (HTTP)│  ← Koyeb health check
└────────────────┘

```

The bot and a small HTTP server share one `asyncio` event loop. The HTTP server exposes `GET /health`, which is what Koyeb uses to decide whether the container is alive. The scheduler is deliberately **not** backed by MongoDB; instead, on every startup the bot reads all enabled channels from MongoDB and rebuilds their jobs with a fixed ID (`rotate_<chat_id>`) and `replace_existing=True`, which guarantees exactly one job per channel.

---

## 📁 Project Structure

```

auto-link-bot/
├── main.py                 # Entry point: bot + health server + scheduler
├── config.py               # Environment loading
├── database.py             # MongoDB client & collections
├── scheduler.py            # Rotation job logic
├── handlers/
│   ├── init.py
│   ├── start.py            # /start and channel picker
│   ├── admin.py            # /addadmin, /removeadmin, /adminlist
│   ├── channel.py          # /addchannel, /removechannel, /settime, etc.
│   └── dashboard.py        # /dashboard (Owner only)
├── requirements.txt
├── .env.example
└── README.md

```

---

## 🔧 Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.11+ | Local development |
| MongoDB Atlas (or any MongoDB 5+) | Free tier works fine |
| Telegram Bot token from [@BotFather](https://t.me/BotFather) | |
| Your Telegram user ID from [@userinfobot](https://t.me/userinfobot) | Becomes `OWNER_ID` |
| A Koyeb account | For production deploy |

The bot account must be **added as an administrator** in every channel it manages, with at least the **"Invite Users via Link"** permission enabled.

---

## 💻 Installation (Local)

```bash
# 1. Clone the repository
git clone https://github.com/your-name/auto-link-bot.git
cd auto-link-bot

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
# then edit .env and fill in BOT_TOKEN, MONGO_URI, OWNER_ID

# 5. Run
python main.py
```

You should see:

```
INFO:aiogram.event:Update id=... is handled. Duration ...
INFO:__main__:Health server listening on port 8080
INFO:__main__:Bot started. Scheduler running with N jobs.
```

Verify the health endpoint:

```bash
curl http://localhost:8080/health
# → ok
```

---

🚀 Deployment on Koyeb

1. Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/your-name/auto-link-bot.git
git push -u origin main
```

2. Create the Koyeb service

1. Open https://app.koyeb.com → Create Service
2. Choose Web Service → GitHub and select your repo
3. Builder: Buildpack (auto-detected Python)
4. Run command: python main.py
5. Port: 8080 (Koyeb sets PORT env var automatically)
6. Health check:
   · Protocol: HTTP
   · Path: /health
   · Port: 8080
7. Environment variables:
   Key Value
   BOT_TOKEN 123456789:AAH...
   MONGO_URI mongodb+srv://...
   OWNER_ID 123456789
8. Instance size: choose Eco (paid) or Free. On Free, the instance may scale to zero when idle, which pauses the rotation scheduler — use a paid instance or an uptime-ping service if you need strict 24/7 rotation.
9. Deploy. When status is HEALTHY, visit https://<your-app>.koyeb.app/health → should return ok.

3. MongoDB Network Access

In MongoDB Atlas → Network Access, whitelist 0.0.0.0/0 (or the Koyeb egress range if you prefer to lock it down).

---

🌍 Environment Variables

Variable Required Description
BOT_TOKEN ✅ Telegram bot token from @BotFather
MONGO_URI ✅ MongoDB connection string (with database name, e.g. /autolink)
OWNER_ID ✅ Telegram user ID of the bot owner
PORT ❌ HTTP port (Koyeb injects it automatically; defaults to 8080)

The .env.example file already contains placeholders for each variable.

---

🗄 Database Schema

Database name: autolink (configured in the connection string).

Collection users

```json
{
  "_id": "ObjectId",
  "telegram_id": 123456789,
  "role": "owner | admin | user",
  "created_at": "ISODate"
}
```

Collection channels

```json
{
  "_id": "ObjectId",
  "chat_id": -1001234567890,
  "title": "NEET Lectures",
  "owner_id": 123456789,
  "current_link": "https://t.me/+AbCdEfGhIjK",
  "link_created_at": "ISODate",
  "interval_seconds": 300,
  "enabled": true,
  "assigned_admins": [987654321]
}
```

Collection activity_logs

```json
{
  "_id": "ObjectId",
  "actor_id": 123456789,
  "action": "add_channel | remove_channel | rotate_link | rotate_failed | add_admin | remove_admin | set_interval | start",
  "channel_id": -1001234567890,
  "detail": {},
  "timestamp": "ISODate"
}
```

Indexes (created automatically on startup)

· channels.chat_id — unique
· channels.owner_id
· activity_logs.timestamp

---

🔄 How Link Rotation Works

Each channel has an independent APScheduler job keyed as rotate_<chat_id>.

On every tick, the bot:

1. Revokes the previous invite link (revoke_chat_invite_link).
2. Creates a new invite link with expire_date = now + 2 × interval.
3. Updates the current_link field in MongoDB.
4. Logs the event to activity_logs.

Example with a 5-minute interval:

Time Active link
10:00 AM https://t.me/+LinkA
10:05 AM https://t.me/+LinkB
10:10 AM https://t.me/+LinkC
10:15 AM https://t.me/+LinkD

Because the new link's expiry is twice the interval, a single failed rotation never leaves the channel without a working link.

Restart safety

On startup, restore_all_jobs() reads every enabled: true channel and calls scheduler.add_job(..., id=f"rotate_{chat_id}", replace_existing=True). This means:

· No duplicate jobs after a restart.
· No lost jobs — anything in the DB is re-scheduled automatically.

---

📖 Command Reference

👤 User Commands (everyone)

Command Description
/start Shows the current invite link. If you manage multiple channels, shows a picker.

👑 Owner-Only Commands

Command Description Example
/addadmin USER_ID Promote a user to admin /addadmin 987654321
/removeadmin USER_ID Demote an admin /removeadmin 987654321
/adminlist List all admins /adminlist
/dashboard Stats: users, channels, jobs, recent activity /dashboard

📢 Channel Management Commands (Owner or assigned Admin)

Command Description Example
/addchannel @username Register a channel (bot must be admin there) /addchannel @neet_lectures
/addchannel -100xxxxxxxxxx Register by chat ID (works for private channels) /addchannel -1001234567890
/removechannel CHAT_ID Unregister a channel (channel owner only) /removechannel -1001234567890
/mychannels List channels you own or are assigned to /mychannels
/settime CHAT_ID VALUE Change rotation interval /settime -1001234567890 600
/setlink CHAT_ID LINK Manually override link (channel owner only) /setlink -1001234567890 https://t.me/+ABC
/status Show status of channels you manage /status

⏱ /settime accepted values

Shortcut Meaning
5m Every 5 minutes
10m Every 10 minutes
1h Every 1 hour
24h Every 24 hours
any integer ≥ 60 Custom interval in seconds

---

🔐 Permissions Matrix

Action Owner Admin User
/start ✅ ✅ ✅
/mychannels ✅ ✅ ✅
/addchannel ✅ ✅ ✅ (creates a channel they own)
/settime on own channels ✅ ✅ ✅
/settime on assigned channels ✅ ✅ ❌
/setlink ✅ (own channels) ❌ ❌
/removechannel ✅ (own channels) ❌ ❌
/addadmin, /removeadmin, /adminlist ✅ ❌ ❌
/dashboard ✅ ❌ ❌

---

🧭 Usage Walkthrough

Assuming your bot is @my_autolink_bot and the channel is @neet_lectures.

1. Register the channel

```
/addchannel @neet_lectures
```

Response:

```
✅ Channel added: NEET Lectures
🔗 Initial link: https://t.me/+AbCdEfGhIjK
⏱ Default interval: 5 minutes
Use /settime to change the interval.
```

2. Change the rotation interval to 10 minutes

```
/settime -1001234567890 10m
```

Response: ✅ Update interval set to 600 seconds.

3. Confirm status

```
/status
```

Response:

```
📊 Channel Status

NEET Lectures
  Status: 🟢 Enabled
  Interval: 600 seconds
  Link created at: 2026-10-22 14:30:00+00:00
```

4. Test as a normal user

Open the bot from a second Telegram account → /start → you'll see the current invite link and a Join button. Wait 10 minutes and /start again — the link changes.

5. Add a second admin

```
/addadmin 987654321
```

That admin can now run /addchannel, /settime, /status, /mychannels on their own channels — and can never see yours.

6. Owner dashboard

```
/dashboard
```

Response:

```
👑 Owner Dashboard

👥 Registered users: 42
📢 Total channels: 5
🟢 Enabled channels: 5
⚙️ Scheduled jobs: 5

Recent activity:
• 10-22 14:30 — rotate_link (ch: -1001234567890)
• 10-22 14:25 — rotate_link (ch: -1001234567890)
• 10-22 14:20 — set_interval (ch: -1001234567890)
```

---

🧪 Testing Guide

1. Health check

```bash
curl https://<your-app>.koyeb.app/health
# → ok
```

2. Add a channel

Send /addchannel @yourchannel. Then confirm in MongoDB Atlas → autolink.channels that a document exists.

3. Rotation speed test

```
/settime -100xxxxxxxxxx 60
```

Wait 60 seconds, then from a second account run /start. The link should differ from the previous one. Verify by checking current_link in MongoDB or by examining the activity_logs collection.

4. Multi-user isolation

1. Account A adds channel X.
2. Account B adds channel Y.
3. Account B runs /mychannels → must not contain X.
4. Account B tries /settime <chat_id_of_X> 60 → must receive ⛔ You do not have permission to manage this channel.

5. Permission checks

· Non-owner sends /addadmin → ⛔ Only the Owner can run this command.
· Non-owner sends /dashboard → ⛔ Only the Owner can access the Dashboard.
· Assigned admin tries /setlink → ⛔ Only the channel owner can set the link manually.

6. Restart recovery

1. Note the number of enabled channels (via /dashboard or MongoDB).
2. In Koyeb → Redeploy the service.
3. After startup, run /dashboard. The Scheduled jobs count should exactly match the number of enabled channels — proving no duplicates and no losses.

7. Failure handling

1. Remove the bot's admin permission from a channel.
2. Wait for the next rotation tick.
3. In MongoDB → activity_logs, verify a rotate_failed entry was written.
4. Restore permissions; the next tick should succeed and write a rotate_link entry.

---

🩺 Troubleshooting

Symptom Cause Fix
Koyeb service keeps restarting Missing /health endpoint, or missing env vars Verify PORT is injected and /health returns ok
Could not fetch channel info Bot is not an admin in the channel Add the bot as administrator
Could not create an invite link Missing Invite Users via Link permission Re-check bot permissions in the channel
The target must be a channel or supergroup Used /addchannel on a private user or basic group Use @username for public channels or -100... chat ID for private ones
This channel is already registered Duplicate /addchannel Run /mychannels to see existing ones
Rotation stops after some time on Free tier Koyeb scaled the instance to zero Use a paid instance or an uptime-ping service
Duplicate rotation messages or jobs Scheduler was started twice Ensure scheduler.start() is only called once in on_startup()
MongoDB connection refused IP not whitelisted Add 0.0.0.0/0 in MongoDB Atlas → Network Access

---

🔒 Security Notes

· Never commit .env — .env is ignored via .gitignore (add it if not present).
· Never log the bot token — the code never prints BOT_TOKEN.
· Validate identity on every command — every handler checks owner_id / assigned_admins before mutating anything.
· Isolated per-user views — /mychannels and /status query with $or on owner_id / assigned_admins, so users can never see other users' channels.
· Only the channel owner can run destructive operations (/removechannel, /setlink).
· Only OWNER_ID can add or remove administrators.
· The bot token is only loaded from environment variables — nothing else reads it.

---

⚖️ License & Legal Use

This project is provided for legitimate channel management only.

Automatic invite-link rotation must not be used to:

· Evade copyright enforcement or takedowns.
· Circumvent Telegram's Terms of Service.
· Distribute illegal content.

You are responsible for the channels you add and the content in them.

---

📦 requirements.txt (for reference)

```txt
aiogram==3.15.0
motor==3.6.0
APScheduler==3.10.4
aiohttp==3.10.11
python-dotenv==1.0.1
```

---

🤝 Contributing

Pull requests are welcome. Please open an issue first to discuss larger changes.

---

📞 Support

· Telegram Bot API: https://core.telegram.org/bots/api
· aiogram docs: https://docs.aiogram.dev
· Koyeb docs: https://www.koyeb.com/docs
· MongoDB Atlas: https://www.mongodb.com/docs/atlas/

```

Also create a **`.gitignore`** file so `.env` and virtual-environment folders never leak:

```gitignore
# Env
.env
.env.*
!.env.example

# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
env/

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db

# Logs
*.log
```

