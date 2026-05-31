"""
Logging service for StarPets CashOuts Bot.

Log format:
  [2026-05-04 17:15:44] [INFO ] [user=Mr_JWW | id=989599679] PAYOUT_CREATED | blogger=braba7x | amount=$30,6
  [2026-05-04 17:15:44] [INFO ] [system] DB_INIT | path=data/starpets.db

Log levels:
  INFO  — normal user actions and system events
  WARN  — unexpected but recoverable situations
  ERROR — application errors (not network noise)

Network errors (SSL, disconnect, timeout) are logged at WARNING level
and suppressed from ERROR level to avoid log spam.
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from config import LOG_DIR

# --------------------------------------------------------------------------- #
# Network error filter — downgrades noisy Telegram network errors to WARNING
# --------------------------------------------------------------------------- #
_NETWORK_PATTERNS = (
    "SSLError",
    "DECRYPTION_FAILED",
    "BAD_RECORD_MAC",
    "RemoteProtocolError",
    "ConnectError",
    "NetworkError",
    "Server disconnected",
    "httpx.",
    "httpcore.",
)


class _NetworkErrorFilter(logging.Filter):
    """Downgrade network-level errors to WARNING so they don't pollute ERROR logs."""
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno == logging.ERROR:
            msg = str(record.getMessage())
            if any(p in msg for p in _NETWORK_PATTERNS):
                record.levelno  = logging.WARNING
                record.levelname = "WARN "
        return True


# --------------------------------------------------------------------------- #
# Handler setup
# --------------------------------------------------------------------------- #
os.makedirs(LOG_DIR, exist_ok=True)
_log_file = os.path.join(LOG_DIR, f"bot_{datetime.now().strftime('%Y%m')}.log")

_file_handler    = logging.FileHandler(_log_file, encoding="utf-8")
_console_handler = logging.StreamHandler()

_TZ = timezone(timedelta(hours=3))  # UTC+3 Moscow time

class _TzFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=_TZ)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")

_fmt = _TzFormatter(
    fmt="[%(asctime)s] [%(levelname)-5s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_file_handler.setFormatter(_fmt)
_console_handler.setFormatter(_fmt)

_file_handler.setLevel(logging.DEBUG)
_console_handler.setLevel(logging.INFO)

_net_filter = _NetworkErrorFilter()
_file_handler.addFilter(_net_filter)
_console_handler.addFilter(_net_filter)

logger = logging.getLogger("starpets")
logger.setLevel(logging.DEBUG)
logger.addHandler(_file_handler)
logger.addHandler(_console_handler)
logger.propagate = False

# Also filter the root telegram logger to suppress network noise there
_tg_logger = logging.getLogger("telegram")
_tg_logger.addFilter(_net_filter)
_ext_logger = logging.getLogger("telegram.ext")
_ext_logger.addFilter(_net_filter)


# --------------------------------------------------------------------------- #
# Event categories for structured logging
# --------------------------------------------------------------------------- #
#
# USER LIFECYCLE
#   USER_REGISTERED      — new user registered via /start
#   USER_UPDATED         — username or role updated
#   SETTINGS_CHANGED     — any setting changed (field, old, new)
#
# BLOGGER MANAGEMENT
#   BLOGGER_ADDED        — new blogger created
#   BLOGGER_DEACTIVATED  — blogger soft-deleted
#   BLOGGER_REACTIVATED  — soft-deleted blogger restored
#   NOTE_UPDATED         — blogger note changed
#   METHOD_ADDED         — payment method added
#   METHOD_UPDATED       — payment method address changed
#   METHOD_TOGGLED       — method enabled/disabled
#   METHOD_SET_PRIMARY   — primary method changed
#   METHOD_DEACTIVATED   — method disabled via manage menu
#   QUICK_METHOD_ADDED   — method added inline during /payout
#
# PAYOUTS
#   PAYOUT_CREATED       — payout block generated
#   PAYOUT_STATUS_FILTER — rows skipped/included due to status filter
#
# IMPORT
#   IMPORT_BLOGGER       — blogger added or updated via /import_bloggers
#   IMPORT_DONE          — import session completed (added, updated, skipped)
#
# ADMIN
#   BACKUP_CREATED       — automatic scheduled backup
#   BACKUP_MANUAL        — manual /backup command
#   DB_RESTORED          — database restored from file
#   ADMIN_VIEW           — admin opened admin panel
#
# SYSTEM
#   BOT_STARTING         — bot startup initiated
#   BOT_STARTED          — bot polling started
#   DB_INIT              — database initialized
#   COMMANDS_SET         — bot commands registered
#   SCHEDULER_STARTED    — APScheduler started
#   UNHANDLED_ERROR      — unexpected exception in handler


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def _user_tag(user_id: int | None, username: str | None) -> str:
    uid   = str(user_id) if user_id is not None else "?"
    uname = username or "unknown"
    return f"[user={uname} | id={uid}]"


def _kv(**kwargs) -> str:
    return " | ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)


def log_info(action: str, user_id: int | None = None, username: str | None = None, **kwargs):
    tag     = _user_tag(user_id, username)
    details = _kv(**kwargs)
    msg     = f"{tag} {action}"
    if details:
        msg += f" | {details}"
    logger.info(msg)


def log_warn(action: str, user_id: int | None = None, username: str | None = None, **kwargs):
    tag     = _user_tag(user_id, username)
    details = _kv(**kwargs)
    msg     = f"{tag} {action}"
    if details:
        msg += f" | {details}"
    logger.warning(msg)


def log_error(action: str, user_id: int | None = None, username: str | None = None, **kwargs):
    tag     = _user_tag(user_id, username)
    details = _kv(**kwargs)
    msg     = f"{tag} {action}"
    if details:
        msg += f" | {details}"
    logger.error(msg)


def log_system(action: str, **kwargs):
    details = _kv(**kwargs)
    msg     = f"[system] {action}"
    if details:
        msg += f" | {details}"
    logger.info(msg)