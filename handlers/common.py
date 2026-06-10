"""
Shared utilities for handlers:
- get_user_or_reject: ensure user is registered
- get_lang: get user language preference
- admin_only: decorator for admin-only handlers
"""

from __future__ import annotations
from functools import wraps
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
            "Вы не зарегистрированы. Нажмите /start чтобы начать.\n"
            "You are not registered. Press /start to begin."
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
            await update.effective_message.reply_text("Нет доступа.")
            return
        return await func(update, context)
    return wrapper


def nav_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Navigation keyboard for end-of-flow messages."""
    if lang == "ru":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Главная", callback_data="nav_home")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Home", callback_data="nav_home")],
    ])



def track_action(context, action: str):
    """Record last user action for error diagnostics."""
    context.user_data["_last_action"] = action


def get_last_action(context) -> str:
    return context.user_data.get("_last_action", "unknown")