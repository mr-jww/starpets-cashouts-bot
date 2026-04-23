import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))
DB_PATH: str = os.getenv("DB_PATH", "data/starpets.db")
BACKUP_DIR: str = os.getenv("BACKUP_DIR", "backups")
LOG_DIR: str = os.getenv("LOG_DIR", "logs")
BACKUP_KEEP: int = int(os.getenv("BACKUP_KEEP", "30"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in .env")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID is not set in .env")

for _dir in [os.path.dirname(DB_PATH), BACKUP_DIR, LOG_DIR]:
    if _dir:
        os.makedirs(_dir, exist_ok=True)