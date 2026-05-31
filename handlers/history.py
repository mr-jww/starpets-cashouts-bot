"""
/history [blogger_name] — show payout history for a blogger.
"""

from __future__ import annotations
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from database.queries import (
    get_user, get_bloggers_for_manager, get_blogger_by_name,
    get_payouts_for_blogger,
)
from handlers.common import get_user_or_reject, get_lang, nav_keyboard

_LIMITS = [5, 10, 20, 0]  # 0 = all


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return
    lang = get_lang(user)
    # Handle both message and callback_query contexts
    eff_msg = update.effective_message
    if not eff_msg:
        return

    arg = " ".join(context.args).strip() if context.args else ""

    if arg:
        db_b = await get_blogger_by_name(arg, user["id"])
        if not db_b:
            await eff_msg.reply_text(
                f"Блогер «{arg}» не найден." if lang == "ru" else f"Blogger '{arg}' not found."
            )
            return
        await _ask_limit(update.message, db_b, lang)
        return

    bloggers = await get_bloggers_for_manager(user["id"])
    if not bloggers:
        await eff_msg.reply_text(
            "Нет блогеров." if lang == "ru" else "No bloggers."
        )
        return

    buttons = [
        [InlineKeyboardButton(b["name"], callback_data=f"hist_b:{b['id']}:{b['name']}")]
        for b in bloggers
    ]
    text = "Выберите блогера:" if lang == "ru" else "Select blogger:"
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_history_blogger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    _, blogger_id, blogger_name = query.data.split(":", 2)
    db_b = {"id": int(blogger_id), "name": blogger_name}
    await _ask_limit(query.message, db_b, lang, edit=True)


async def _ask_limit(target, db_b: dict, lang: str, edit: bool = False):
    if lang == "ru":
        buttons = [[
            InlineKeyboardButton("5",    callback_data=f"hist_n:{db_b['id']}:{db_b['name']}:5"),
            InlineKeyboardButton("10",   callback_data=f"hist_n:{db_b['id']}:{db_b['name']}:10"),
            InlineKeyboardButton("20",   callback_data=f"hist_n:{db_b['id']}:{db_b['name']}:20"),
            InlineKeyboardButton("Все",  callback_data=f"hist_n:{db_b['id']}:{db_b['name']}:0"),
        ]]
        text = f"Сколько выплат показать для {db_b['name']}?"
    else:
        buttons = [[
            InlineKeyboardButton("5",    callback_data=f"hist_n:{db_b['id']}:{db_b['name']}:5"),
            InlineKeyboardButton("10",   callback_data=f"hist_n:{db_b['id']}:{db_b['name']}:10"),
            InlineKeyboardButton("20",   callback_data=f"hist_n:{db_b['id']}:{db_b['name']}:20"),
            InlineKeyboardButton("All",  callback_data=f"hist_n:{db_b['id']}:{db_b['name']}:0"),
        ]]
        text = f"How many payouts to show for {db_b['name']}?"

    if edit:
        await target.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await target.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_history_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    parts = query.data.split(":", 3)
    blogger_id   = int(parts[1])
    blogger_name = parts[2]
    limit        = int(parts[3])

    payouts = await get_payouts_for_blogger(blogger_id, limit)

    if not payouts:
        await query.edit_message_text(
            f"Нет выплат для {blogger_name}." if lang == "ru"
            else f"No payouts for {blogger_name}.",
            reply_markup=nav_keyboard(lang),
        )
        return

    lines = []
    for p in payouts:
        date = p["created_at"][:10]
        amount = p["amount_raw"]
        game = p.get("game") or "?"
        videos = p.get("videos_count", 0)
        if lang == "ru":
            lines.append(f"• {date} — {amount} — {videos} вид. — {game}")
        else:
            lines.append(f"• {date} — {amount} — {videos} vid. — {game}")

    shown = f"{len(payouts)}" if limit == 0 else f"{min(len(payouts), limit)}"
    header = (
        f"{blogger_name} — {shown} выплат:"
        if lang == "ru" else
        f"{blogger_name} — {shown} payouts:"
    )
    text = header + "\n" + "\n".join(lines)

    # Split if too long
    if len(text) > 4000:
        text = text[:4000] + "\n..."

    await query.edit_message_text(text, reply_markup=nav_keyboard(lang))


def register_history_handlers(app):
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CallbackQueryHandler(cb_history_blogger, pattern=r"^hist_b:"))
    app.add_handler(CallbackQueryHandler(cb_history_count,   pattern=r"^hist_n:"))