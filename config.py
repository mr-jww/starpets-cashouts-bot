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

# Active managers with their passwords (sha256 hashed at startup)
# Name -> plain password (stored as hash in DB on first login)
ACTIVE_MANAGERS: dict[str, str] = {
    "John":       "847291",
    "Alex":       "563084",
    "Rocky":      "219467",
    "Max":        "730156",   # formerly MaxSP
    "smazakxd":   "492803",
    "Swirl":      "615742",
    "Maison":     "384910",
    "Emilio":     "057329",
    "Nick":       "726481",
    "Jennifer":   "193045",
    "Marco":      "840563",
    "Ketty":      "471829",
    "Rina":       "306754",
    "Stacy":      "928310",
    "Antonio":    "645097",
    "Tony":       "183426",
    "Tom":        "759204",
    "Talon":      "437815",
    "Vanessa":    "062943",
    "Monty":      "591736",
    "Unfairbird": "824059",
    "Vojtěch":    "317648",
    "Seb":        "483271",
    "Annalice":   "706534",
    "Sergo":      "159087",
}

# Display order for manager selection buttons (3 columns)
MANAGER_BUTTON_ORDER = [
    "John", "Alex", "Rocky",
    "Max", "smazakxd", "Swirl",
    "Maison", "Emilio", "Nick",
    "Jennifer", "Marco", "Ketty",
    "Rina", "Stacy", "Antonio",
    "Tony", "Tom", "Talon",
    "Vanessa", "Monty", "Unfairbird",
    "Vojtěch", "Seb", "Annalice",
    "Sergo",
]

# Google Sheets sync
SHEETS_ID          = os.getenv("SHEETS_ID", "")
SHEETS_CREDENTIALS = os.getenv("SHEETS_CREDENTIALS", "google_credentials.json")