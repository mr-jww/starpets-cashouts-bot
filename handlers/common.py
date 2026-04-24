"""
Shared utilities for handlers:
- get_user_or_reject: ensure user is registered
- get_lang: get user language preference
- admin_only: decorator for admin-only handlers
"""

from __future__ import annotations
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes
from database.queries import get_user
from config import ADMIN_ID


async def get_user_or_reject(update: Update) -> dict | None:
    """
    Returns user dict if registered, otherwise sends rejection message and returns None.
    Used at the start of every handler that requires registration.
    """
    tg = update.effective_user
    user = await get_user(tg.id)
    if not user:
        await update.effective_message.reply_text(
            "Вы не зарегистрированы. Используйте /start для регистрации.\n"
            "You are not registered. Use /start to register."
        )
        return None
    return user


def get_lang(user: dict) -> str:
    return user.get("lang", "ru")


def is_admin(telegram_id: int) -> bool:
    return telegram_id == ADMIN_ID


def admin_only(func):
    """Decorator: rejects non-admins."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.effective_message.reply_text("Нет доступа. / Access denied.")
            return
        return await func(update, context)
    return wrapper