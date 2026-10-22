from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI

client = AsyncIOMotorClient(MONGO_URI)
db = client.autolink

users_col = db.users
channels_col = db.channels
logs_col = db.activity_logs
shortlinks_col = db.shortlinks       # ← new

async def ensure_indexes():
    await channels_col.create_index("chat_id", unique=True)
    await channels_col.create_index("owner_id")
    await logs_col.create_index("timestamp")
    await shortlinks_col.create_index("alias", unique=True)   # ← new
