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

# Single team password — required once per account to confirm identity
# Set in .env as TEAM_PASSWORD
TEAM_PASSWORD: str = os.getenv("TEAM_PASSWORD", "")

# Display order for manager selection buttons (3 columns)
MANAGER_BUTTON_ORDER = [
    "John", "Alex", "Rocky",
    "Max", "smazakxd", "Swirl",
    "Maison", "Emilio", "Nick",
    "Jennifer", "Marco", "Ketty",
    "Rina", "Tony", "Tom",
    "Monty", "Unfairbird", "Vojtěch",
    "Seb", "Annalice", "Sergo",
    "William", "Anthony",
]

# Google Sheets sync
SHEETS_ID          = os.getenv("SHEETS_ID", "")
SHEETS_CREDENTIALS = os.getenv("SHEETS_CREDENTIALS", "google_credentials.json")