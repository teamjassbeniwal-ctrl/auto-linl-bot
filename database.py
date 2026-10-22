from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI

client = AsyncIOMotorClient(MONGO_URI)
db = client.autolink

users_col = db.users
channels_col = db.channels
logs_col = db.activity_logs

async def ensure_indexes():
    await channels_col.create_index("chat_id", unique=True)
    await channels_col.create_index("owner_id")
    await logs_col.create_index("timestamp")
