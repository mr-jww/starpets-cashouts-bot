"""
/payout handler.

Usage:
  /payout             — use manager name from settings as filter (or all if not set)
  /payout amb-John    — filter by manager John
  /payout amb-all     — ignore filter, show all from pasted rows

Buttons under each payout block:
  [ ↕ Multiline / One line ]  [ 💳 Change method ]
"""

from __future__ import annotations
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters,
)

from database.queries import (
    get_user, get_blogger_by_name, add_blogger,
    get_active_methods, get_primary_method,
    save_payout, db_log, METHOD_LABELS,
)
from services.parser import parse_rows, BloggerResult
from services.formatter import format_oneline, format_multiline
from services.logger import log_info
from handlers.common import get_user_or_reject, get_lang

WAIT_ROWS, WAIT_UNKNOWN, WAIT_CHANGE_METHOD = range(3)

CANCEL_TEXT = {"ru": "Отменено.", "en": "Cancelled."}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _parse_flags(args: list[str]) -> str | None:
    """
    Returns amb filter string or None.
    'amb-John' -> 'John'
    'amb-all'  -> '' (empty = no filter)
    no flag    -> None (use settings)
    """
    for arg in [a.lower() for a in args]:
        if arg.startswith("amb-"):
            val = arg[4:]
            return "" if val == "all" else val
    return None


def _payout_keyboard(key: str, method_id: int, fmt: str, lang: str) -> InlineKeyboardMarkup:
    if lang == "ru":
        toggle = "↕ Многострочный" if fmt == "oneline" else "↕ Однострочный"
        change  = "💳 Поменять метод"
    else:
        toggle = "↕ Multiline" if fmt == "oneline" else "↕ One line"
        change  = "💳 Change method"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(toggle, callback_data=f"pt_tog:{key}:{method_id}:{fmt}"),
        InlineKeyboardButton(change, callback_data=f"pt_chm:{key}"),
    ]])


async def _send_payout_block(
    target,
    result: BloggerResult,
    method_type: str,
    address: str,
    method_id: int,
    key: str,
    fmt: str,
    lang: str,
    edit: bool = False,
):
    text = (format_oneline if fmt == "oneline" else format_multiline)(
        result, method_type, address, lang
    )
    escaped = text.replace("`", "'")
    msg = f"```\n{escaped}\n```"
    keyboard = _payout_keyboard(key, method_id, fmt, lang)
    if edit:
        await target.edit_message_text(msg, reply_markup=keyboard, parse_mode="Markdown")
    else:
        if hasattr(target, "effective_message") and target.effective_message:
            eff = target.effective_message
        elif hasattr(target, "message") and target.message:
            eff = target.message
        else:
            eff = target
        await eff.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")


def _storage_key(blogger_name: str) -> str:
    """Safe key for user_data storage."""
    return f"pd_{blogger_name.replace(' ', '_')}"


# --------------------------------------------------------------------------- #
# /payout entry
# --------------------------------------------------------------------------- #
async def cmd_payout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return ConversationHandler.END

    lang = get_lang(user)
    flag_filter = _parse_flags(context.args or [])

    # Resolve effective filter:
    # flag_filter=None -> use settings; flag_filter="" -> no filter; flag_filter="John" -> John
    if flag_filter is None:
        effective_filter = user.get("manager_filter") or None
    else:
        effective_filter = flag_filter if flag_filter else None

    context.user_data.update({
        "user":             user,
        "effective_filter": effective_filter,
    })

    hint = ""
    if effective_filter:
        hint = f"\n{'Менеджер' if lang == 'ru' else 'Manager'}: {effective_filter}"
    elif flag_filter == "":
        hint = f"\n{'Фильтр отключён' if lang == 'ru' else 'Filter disabled'}"

    await update.message.reply_text(
        ("Вставьте строки из таблицы." + hint + "\n/cancel — отмена")
        if lang == "ru" else
        ("Paste rows from the spreadsheet." + hint + "\n/cancel — cancel")
    )
    return WAIT_ROWS


# --------------------------------------------------------------------------- #
# Got rows
# --------------------------------------------------------------------------- #
async def payout_got_rows(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data["user"]
    lang = get_lang(user)
    effective_filter = context.user_data.get("effective_filter")
    raw_text = update.message.text.strip()

    result = parse_rows(raw_text, lang)

    if result.critical_errors and not result.bloggers:
        await update.message.reply_text(
            ("Ошибка разбора:\n" if lang == "ru" else "Parse error:\n")
            + "\n".join(result.critical_errors)
        )
        return ConversationHandler.END

    if not result.bloggers:
        await update.message.reply_text(
            "Не найдено строк с данными." if lang == "ru" else "No data rows found."
        )
        return ConversationHandler.END

    # Apply manager filter
    if effective_filter:
        filtered = [
            b for b in result.bloggers
            if any(
                effective_filter.lower() in (r.manager or "").lower()
                for r in b.rows
            )
        ]
        if not filtered:
            await update.message.reply_text(
                f"Нет блогеров менеджера «{effective_filter}» в присланных строках.\n"
                f"Используйте /payout amb-all чтобы показать всех."
                if lang == "ru" else
                f"No bloggers for manager '{effective_filter}' in pasted rows.\n"
                f"Use /payout amb-all to show all."
            )
            return ConversationHandler.END
        result.bloggers = filtered

    if result.critical_errors:
        await update.message.reply_text(
            ("Некоторые строки пропущены:\n" if lang == "ru" else "Some rows skipped:\n")
            + "\n".join(result.critical_errors)
        )

    # Summary
    await _send_summary(update.message, result.bloggers, lang)

    # Separate known / unknown
    known, unknown = [], []
    for b in result.bloggers:
        db_b = await get_blogger_by_name(b.blogger, user["id"])
        if db_b:
            known.append((b, db_b))
        else:
            unknown.append(b)

    context.user_data.update({
        "known":     known,
        "unknown":   unknown,
        "skipped":   [],
        "payout_raw": raw_text,
    })

    if unknown:
        return await _ask_unknown(update.message, context)

    return await _process_known(update, context)


async def _send_summary(target, bloggers: list[BloggerResult], lang: str):
    lines = []
    for b in bloggers:
        games = ", ".join(b.games) if b.games else "?"
        err = " ⚠" if b.has_errors else ""
        if lang == "ru":
            lines.append(f"• {b.blogger} — {b.video_count} вид. — {b.total_price} — {games}{err}")
        else:
            lines.append(f"• {b.blogger} — {b.video_count} vid. — {b.total_price} — {games}{err}")
    header = f"Найдено: {len(bloggers)}" if lang == "ru" else f"Found: {len(bloggers)}"
    await target.reply_text(header + "\n" + "\n".join(lines))


# --------------------------------------------------------------------------- #
# Unknown bloggers
# --------------------------------------------------------------------------- #
async def _ask_unknown(target, context):
    user = context.user_data["user"]
    lang = get_lang(user)
    unknown: list[BloggerResult] = context.user_data["unknown"]
    names = "\n".join(f"• {b.blogger}" for b in unknown)
    if lang == "ru":
        text = f"Не в базе ({len(unknown)}):\n{names}\n\nДобавить всех?"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Добавить", callback_data="unk:add"),
            InlineKeyboardButton("Пропустить", callback_data="unk:skip"),
        ]])
    else:
        text = f"Not in database ({len(unknown)}):\n{names}\n\nAdd all?"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Add all", callback_data="unk:add"),
            InlineKeyboardButton("Skip all", callback_data="unk:skip"),
        ]])
    await target.reply_text(text, reply_markup=keyboard)
    return WAIT_UNKNOWN


async def cb_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = context.user_data["user"]
    lang = get_lang(user)
    action = query.data.split(":")[1]
    unknown: list[BloggerResult] = context.user_data["unknown"]

    if action == "add":
        for b in unknown:
            db_b = await add_blogger(b.blogger, user["id"])
            if db_b is None:
                db_b = await get_blogger_by_name(b.blogger, user["id"])
            if db_b:
                context.user_data["known"].append((b, db_b))
        await query.edit_message_text(
            f"Добавлено: {len(unknown)}." if lang == "ru" else f"Added: {len(unknown)}."
        )
    else:
        context.user_data["skipped"].extend([b.blogger for b in unknown])
        await query.edit_message_text(
            f"Пропущено: {len(unknown)}." if lang == "ru" else f"Skipped: {len(unknown)}."
        )

    return await _process_known(update, context)


async def _wait_unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data.get("user")
    lang = get_lang(user) if user else "en"
    await update.message.reply_text(
        "Нажмите одну из кнопок." if lang == "ru" else "Please press one of the buttons."
    )
    return WAIT_UNKNOWN


# --------------------------------------------------------------------------- #
# Process all known bloggers
# --------------------------------------------------------------------------- #
async def _process_known(target, context):
    user = context.user_data["user"]
    lang = get_lang(user)
    known: list[tuple[BloggerResult, dict]] = context.user_data.get("known", [])
    skipped: list[str] = context.user_data.get("skipped", [])
    no_method: list[str] = []

    # target can be Update, CallbackQuery, or Message
    if hasattr(target, "effective_message"):
        eff = target.effective_message
    elif hasattr(target, "message") and target.message:
        eff = target.message
    else:
        eff = target

    for blogger_result, db_blogger in known:
        method = await get_primary_method(db_blogger["id"])

        if not method:
            no_method.append(blogger_result.blogger)
            continue

        key = _storage_key(blogger_result.blogger)
        context.user_data[key] = {
            "result":      blogger_result,
            "method_type": method["type"],
            "address":     method["address"],
            "method_id":   method["id"],
            "db_blogger":  db_blogger,
            "fmt":         "oneline",
        }

        await _send_payout_block(
            eff, blogger_result,
            method["type"], method["address"], method["id"],
            key, "oneline", lang,
        )

        games_str = ", ".join(blogger_result.games)
        await save_payout(
            blogger_id=db_blogger["id"],
            manager_id=user["id"],
            amount_raw=blogger_result.total_price,
            method_id=method["id"],
            videos_count=blogger_result.video_count,
            game=games_str,
            mode=blogger_result.mode,
            raw_input=context.user_data.get("payout_raw", ""),
            formatted_text=format_oneline(blogger_result, method["type"], method["address"], lang),
        )
        log_info(
            "PAYOUT_CREATED",
            user_id=user["telegram_id"],
            username=user["username"],
            blogger=blogger_result.blogger,
            amount=blogger_result.total_price,
            method=method["type"],
        )
        await db_log(
            user["id"], "PAYOUT_CREATED",
            f"blogger={blogger_result.blogger} | amount={blogger_result.total_price} | method={method['type']}"
        )

    if no_method:
        names = ", ".join(no_method)
        await eff.reply_text(
            f"Нет метода оплаты для: {names}\nДобавьте через /add_method"
            if lang == "ru" else
            f"No payment method for: {names}\nAdd via /add_method"
        )

    if skipped:
        await eff.reply_text(
            f"Пропущены: {', '.join(skipped)}" if lang == "ru"
            else f"Skipped: {', '.join(skipped)}"
        )

    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Toggle oneline ↔ multiline
# --------------------------------------------------------------------------- #
async def cb_payout_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    _, key, method_id_str, current_fmt = query.data.split(":", 3)
    method_id = int(method_id_str)
    data = context.user_data.get(key)
    if not data:
        await query.answer(
            "Data expired." if lang == "en" else "Данные устарели.",
            show_alert=True
        )
        return

    new_fmt = "multiline" if current_fmt == "oneline" else "oneline"
    data["fmt"] = new_fmt
    await _send_payout_block(
        query, data["result"], data["method_type"], data["address"],
        method_id, key, new_fmt, lang, edit=True,
    )


# --------------------------------------------------------------------------- #
# Change method
# --------------------------------------------------------------------------- #
async def cb_change_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    key = query.data.split(":", 1)[1]
    data = context.user_data.get(key)
    if not data:
        await query.answer(
            "Data expired." if lang == "en" else "Данные устарели.",
            show_alert=True
        )
        return

    db_blogger = data["db_blogger"]
    methods = await get_active_methods(db_blogger["id"])
    if not methods:
        await query.answer(
            "No payment methods." if lang == "en" else "Нет методов оплаты.",
            show_alert=True
        )
        return

    buttons = []
    for m in methods:
        label = METHOD_LABELS.get(m["type"], m["type"])
        star = " ★" if m.get("is_primary") else ""
        btn_text = f"{label}: {m['address']}{star}"
        buttons.append([InlineKeyboardButton(
            btn_text,
            callback_data=f"pt_sel:{key}:{m['id']}:{m['type']}:{m['address']}"
        )])

    header = (
        f"Выберите метод для {data['result'].blogger}:"
        if lang == "ru" else
        f"Select method for {data['result'].blogger}:"
    )
    context.user_data["_chm_key"] = key
    await query.edit_message_text(header, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_select_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    parts = query.data.split(":", 4)
    key         = parts[1]
    method_id   = int(parts[2])
    method_type = parts[3]
    address     = parts[4]

    data = context.user_data.get(key)
    if not data:
        await query.answer("Data expired.", show_alert=True)
        return

    data["method_type"] = method_type
    data["address"]     = address
    data["method_id"]   = method_id
    fmt = data.get("fmt", "oneline")

    await _send_payout_block(
        query, data["result"], method_type, address,
        method_id, key, fmt, lang, edit=True,
    )


# --------------------------------------------------------------------------- #
# /cancel
# --------------------------------------------------------------------------- #
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data.clear()
    await update.message.reply_text(CANCEL_TEXT[lang])
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_payout_handlers(app):
    # Also accept the keyboard button text as entry point
    payout_entry_filter = (
        filters.TEXT & ~filters.COMMAND &
        (filters.Regex(r"^🏠") | filters.Regex(r"^💸") | filters.Regex(r"^/payout"))
    )
    app.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("payout", cmd_payout),
            MessageHandler(filters.Regex(r"^💸"), cmd_payout),
        ],
        states={
            WAIT_ROWS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, payout_got_rows),
            ],
            WAIT_UNKNOWN: [
                CallbackQueryHandler(cb_unknown,         pattern=r"^unk:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _wait_unknown_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        conversation_timeout=600,
        per_message=False,
    ))

    # Outside conversation — work on already-sent blocks
    app.add_handler(CallbackQueryHandler(cb_payout_toggle,  pattern=r"^pt_tog:"))
    app.add_handler(CallbackQueryHandler(cb_change_method,  pattern=r"^pt_chm:"))
    app.add_handler(CallbackQueryHandler(cb_select_method,  pattern=r"^pt_sel:"))