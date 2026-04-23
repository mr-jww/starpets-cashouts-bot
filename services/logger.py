"""
Logging service.
Format: [YYYY-MM-DD HH:MM:SS] [LEVEL] [user=username | id=telegram_id] ACTION | key=value ...
"""

import logging
import os
from datetime import datetime
from config import LOG_DIR

_log_file = os.path.join(LOG_DIR, f"bot_{datetime.now().strftime('%Y%m')}.log")

_file_handler = logging.FileHandler(_log_file, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)

_fmt = logging.Formatter(
    fmt="[%(asctime)s] [%(levelname)-5s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_file_handler.setFormatter(_fmt)
_console_handler.setFormatter(_fmt)

logger = logging.getLogger("starpets")
logger.setLevel(logging.DEBUG)
logger.addHandler(_file_handler)
logger.addHandler(_console_handler)
logger.propagate = False


def _user_tag(user_id: int | None = None, username: str | None = None) -> str:
    uid = str(user_id) if user_id else "?"
    uname = username or "unknown"
    return f"[user={uname} | id={uid}]"


def _kv(**kwargs) -> str:
    return " | ".join(f"{k}={v}" for k, v in kwargs.items() if v is not None)


def log_info(action: str, user_id: int | None = None, username: str | None = None, **kwargs):
    tag = _user_tag(user_id, username)
    details = _kv(**kwargs)
    msg = f"{tag} {action}"
    if details:
        msg += f" | {details}"
    logger.info(msg)


def log_warn(action: str, user_id: int | None = None, username: str | None = None, **kwargs):
    tag = _user_tag(user_id, username)
    details = _kv(**kwargs)
    msg = f"{tag} {action}"
    if details:
        msg += f" | {details}"
    logger.warning(msg)


def log_error(action: str, user_id: int | None = None, username: str | None = None, **kwargs):
    tag = _user_tag(user_id, username)
    details = _kv(**kwargs)
    msg = f"{tag} {action}"
    if details:
        msg += f" | {details}"
    logger.error(msg)


def log_system(action: str, **kwargs):
    details = _kv(**kwargs)
    msg = f"[system] {action}"
    if details:
        msg += f" | {details}"
    logger.info(msg)