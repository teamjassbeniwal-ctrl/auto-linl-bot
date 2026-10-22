import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
PORT = int(os.getenv("PORT", "8080"))

# Shortcut interval values (in seconds)
INTERVAL_OPTIONS = {
    "5m": 300,
    "10m": 600,
    "1h": 3600,
    "24h": 86400,
}
