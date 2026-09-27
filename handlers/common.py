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
from database.queries import get_user, set_payout_mode
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
    return user.get("lang", "en")


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


def clear_payout_context(context) -> None:
    """Remove transient payout data without touching other active flows."""
    exact_keys = {
        "user", "effective_filter", "payout_bloggers", "payout_raw",
        "all_payout_texts", "_payout_just_handled",
    }
    for key in list(context.user_data):
        if key in exact_keys or key.startswith(("pd_", "chm_methods_")):
            context.user_data.pop(key, None)
    if context.user_data.get("_last_action") == "payout_got_rows":
        context.user_data.pop("_last_action", None)


async def disable_payout_mode(telegram_id: int, context) -> None:
    await set_payout_mode(telegram_id, False)
    clear_payout_context(context)



def track_action(context, action: str):
    """Record last user action for error diagnostics."""
    context.user_data["_last_action"] = action


def get_last_action(context) -> str:
    return context.user_data.get("_last_action", "unknown")
