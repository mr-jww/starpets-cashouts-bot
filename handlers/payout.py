"""
/payout handler.

Flags:
  /payout             - use manager_filter from settings
  /payout amb-John    - filter by John
  /payout amb-all     - no filter

Buttons under each payout block:
  [ toggle format ]  [ change method ]
  (block/text mode from settings)

Error flow:
  - If any blogger has errors: one combined error summary sent first
  - Blocks with errors sent as reply to that summary
  - Blocks without errors sent normally
  - ERROR:$X used as total when errors present
"""

from __future__ import annotations
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message, InputFile
import io
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters,
)

from database.queries import (
    get_user, get_blogger_by_name, add_blogger,
    get_active_methods, get_primary_method, get_active_methods_by_type,
    save_payout, db_log, METHOD_LABELS, METHOD_TYPES,
    add_payment_method, set_primary_method,
)
from services.parser import parse_rows, BloggerResult
from services.formatter import format_oneline, format_multiline
from services.logger import log_info
from handlers.common import get_user_or_reject, get_lang

WAIT_ROWS, WAIT_UNKNOWN, WAIT_QUICK_ADDRESS = range(3)
CANCEL_TEXT = {"ru": "Отменено.", "en": "Cancelled."}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _parse_flags(args: list[str]) -> str | None:
    for arg in [a.lower() for a in args]:
        if arg.startswith("amb-"):
            val = arg[4:]
            return "" if val == "all" else val
    return None


def _get_output_mode(user: dict) -> str:
    return user.get("output_mode") or "block"


def _payout_keyboard(key: str, method_id: int, fmt: str, lang: str, output_mode: str) -> InlineKeyboardMarkup:
    if lang == "ru":
        # Show current format, clicking switches to opposite
        toggle = "↕ Однострочный" if fmt == "multiline" else "↕ Многострочный"
        change  = "💳 Поменять метод"
    else:
        toggle = "↕ One line" if fmt == "multiline" else "↕ Multiline"
        change  = "💳 Change method"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(toggle, callback_data=f"pt_tog:{key}:{method_id}:{fmt}"),
        InlineKeyboardButton(change, callback_data=f"pt_chm:{key}"),
    ]])


def _format_block(text: str, output_mode: str) -> str:
    escaped = text.replace("`", "'")
    if output_mode == "block":
        return f"```\n{escaped}\n```"
    return text


async def _send_payout_block(
    target,
    result: BloggerResult,
    method_type: str,
    address: str,
    method_id: int,
    key: str,
    fmt: str,
    lang: str,
    output_mode: str,
    reply_to: Message | None = None,
    edit: bool = False,
):
    text = (format_oneline if fmt == "oneline" else format_multiline)(
        result, method_type, address, lang
    )
    msg_text = _format_block(text, output_mode)
    keyboard = _payout_keyboard(key, method_id, fmt, lang, output_mode)
    parse_mode = "Markdown" if output_mode == "block" else None

    if edit:
        await target.edit_message_text(msg_text, reply_markup=keyboard, parse_mode=parse_mode)
    elif reply_to:
        await reply_to.reply_text(msg_text, reply_markup=keyboard, parse_mode=parse_mode)
    else:
        eff = getattr(target, "effective_message", None) or getattr(target, "message", None) or target
        await eff.reply_text(msg_text, reply_markup=keyboard, parse_mode=parse_mode)


def _storage_key(name: str) -> str:
    return f"pd_{re.sub(r'[^a-zA-Z0-9_]', '_', name)}"


import re



def _nav_keyboard(lang: str) -> InlineKeyboardMarkup:
    if lang == "ru":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Главная", callback_data="nav_home")],
            [InlineKeyboardButton("📤 Вывести все выплаты", callback_data="nav_copy_all")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Home", callback_data="nav_home")],
        [InlineKeyboardButton("📤 Export all payouts", callback_data="nav_copy_all")],
    ])


# --------------------------------------------------------------------------- #
# Error summary
# --------------------------------------------------------------------------- #
async def _send_error_summary(target, bloggers_with_errors: list[BloggerResult], lang: str) -> Message:
    header = "Ошибки в выплатах:" if lang == "ru" else "Errors in payouts:"
    parts = [header]
    for b in bloggers_with_errors:
        parts.append(f"\n{b.blogger}:")
        parts.append(b.error_summary(lang))
    text = "\n".join(parts)
    eff = getattr(target, "effective_message", None) or getattr(target, "message", None) or target
    return await eff.reply_text(text)


# --------------------------------------------------------------------------- #
# /payout entry
# --------------------------------------------------------------------------- #
async def cmd_payout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return ConversationHandler.END

    lang = get_lang(user)
    flag_filter = _parse_flags(context.args or [])
    effective_filter = (
        flag_filter if flag_filter is not None
        else (user.get("manager_filter") or None)
    ) if flag_filter != "" else None

    if flag_filter == "":
        effective_filter = None

    context.user_data.update({"user": user, "effective_filter": effective_filter})

    hint = ""
    if effective_filter:
        hint = f"\n{'Менеджер' if lang == 'ru' else 'Manager'}: {effective_filter}"
    elif flag_filter == "":
        hint = f"\n{'Фильтр отключён' if lang == 'ru' else 'Filter disabled'}"

    if lang == "ru":
        text = "Вставьте строки из таблицы." + hint
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✕ Отмена", callback_data="payout_cancel"),
        ]])
    else:
        text = "Paste rows from the spreadsheet." + hint
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✕ Cancel", callback_data="payout_cancel"),
        ]])
    await update.effective_message.reply_text(text, reply_markup=keyboard)
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
            if any(effective_filter.lower() in (r.manager or "").lower() for r in b.rows)
        ]
        if not filtered:
            await update.message.reply_text(
                f"Нет блогеров менеджера «{effective_filter}».\n/payout amb-all — показать всех"
                if lang == "ru" else
                f"No bloggers for manager '{effective_filter}'.\n/payout amb-all — show all"
            )
            return ConversationHandler.END
        result.bloggers = filtered

    if result.critical_errors:
        await update.message.reply_text(
            ("Пропущены строки:\n" if lang == "ru" else "Rows skipped:\n")
            + "\n".join(result.critical_errors)
        )

    # Summary
    await _send_summary(update.message, result.bloggers, lang)

    # Split known / unknown
    known, unknown = [], []
    for b in result.bloggers:
        db_b = await get_blogger_by_name(b.blogger, user["id"])
        if db_b:
            known.append((b, db_b))
        else:
            unknown.append(b)

    context.user_data.update({
        "known": known, "unknown": unknown,
        "skipped": [], "payout_raw": raw_text,
    })

    if unknown:
        return await _ask_unknown(update.message, context)
    return await _process_known(update, context)


async def _send_summary(target: Message, bloggers: list[BloggerResult], lang: str):
    lines = []
    for b in bloggers:
        games = ", ".join(b.games) if b.games else "?"
        err = " ⚠" if b.has_errors else ""
        lines.append(
            f"• {b.blogger} — {b.video_count} вид. — {b.total_price_display} — {games}{err}"
            if lang == "ru" else
            f"• {b.blogger} — {b.video_count} vid. — {b.total_price_display} — {games}{err}"
        )
    header = f"Найдено: {len(bloggers)}" if lang == "ru" else f"Found: {len(bloggers)}"
    await target.reply_text(header + "\n" + "\n".join(lines))


# --------------------------------------------------------------------------- #
# Unknown bloggers
# --------------------------------------------------------------------------- #
async def _ask_unknown(target: Message, context):
    user = context.user_data["user"]
    lang = get_lang(user)
    unknown: list[BloggerResult] = context.user_data["unknown"]
    names = "\n".join(f"• {b.blogger}" for b in unknown)
    if lang == "ru":
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Добавить", callback_data="unk:add"),
            InlineKeyboardButton("Пропустить", callback_data="unk:skip"),
        ]])
        text = f"Не в базе ({len(unknown)}):\n{names}\n\nДобавить всех?"
    else:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Add all", callback_data="unk:add"),
            InlineKeyboardButton("Skip all", callback_data="unk:skip"),
        ]])
        text = f"Not in database ({len(unknown)}):\n{names}\n\nAdd all?"
    await target.reply_text(text, reply_markup=keyboard)
    return WAIT_UNKNOWN


async def cb_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = context.user_data["user"]
    lang = get_lang(user)
    unknown: list[BloggerResult] = context.user_data["unknown"]

    if query.data.split(":")[1] == "add":
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
        "Нажмите одну из кнопок." if lang == "ru" else "Press one of the buttons."
    )
    return WAIT_UNKNOWN


# --------------------------------------------------------------------------- #
# Quick add method inside payout
# --------------------------------------------------------------------------- #
async def cb_quick_method_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User selected method type for quick add during payout."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    parts = query.data.split(":", 2)
    method_type = parts[1]
    blogger_name = parts[2]

    context.user_data["qm_type"] = method_type
    context.user_data["qm_blogger"] = blogger_name

    hints = {
        "site":       {"ru": f"Введите Profile ID для {blogger_name}:",
                       "en": f"Enter Profile ID for {blogger_name}:"},
        "usdt-trc20": {"ru": f"Введите адрес USDT-TRC20 для {blogger_name}:",
                       "en": f"Enter USDT-TRC20 address for {blogger_name}:"},
        "paypal":     {"ru": f"Введите email PayPal для {blogger_name}:",
                       "en": f"Enter PayPal email for {blogger_name}:"},
    }
    await query.edit_message_text(hints.get(method_type, {}).get(lang, "Enter address:"))
    return WAIT_QUICK_ADDRESS


async def quick_address_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data.get("user") or await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    address = update.message.text.strip()

    if not address:
        await update.message.reply_text("Пустой адрес." if lang == "ru" else "Empty address.")
        return WAIT_QUICK_ADDRESS

    blogger_name = context.user_data.get("qm_blogger", "")
    method_type  = context.user_data.get("qm_type", "site")
    db_b = await get_blogger_by_name(blogger_name, user["id"])

    if db_b:
        method = await add_payment_method(db_b["id"], method_type, address)
        await set_primary_method(method["id"], db_b["id"])
        type_label = METHOD_LABELS.get(method_type, method_type)
        await update.message.reply_text(
            f"Добавлено: {blogger_name} — {type_label}: {address}"
            if lang == "ru" else
            f"Added: {blogger_name} — {type_label}: {address}"
        )
        log_info("QUICK_METHOD_ADDED", user_id=user["telegram_id"],
                 username=user["username"], blogger=blogger_name, type=method_type)
        await db_log(user["id"], "QUICK_METHOD_ADDED",
                     f"blogger={blogger_name} | type={method_type}")

    # Resume processing
    return await _process_known(update, context)


# --------------------------------------------------------------------------- #
# Process all known bloggers
# --------------------------------------------------------------------------- #
async def _process_known(target, context):
    user = context.user_data["user"]
    lang = get_lang(user)
    output_mode = _get_output_mode(user)
    known: list[tuple[BloggerResult, dict]] = context.user_data.get("known", [])
    skipped: list[str] = context.user_data.get("skipped", [])
    no_method: list[tuple[BloggerResult, dict]] = []

    eff = getattr(target, "effective_message", None) or getattr(target, "message", None) or target

    # Send error summary first if needed
    bloggers_with_errors = [br for br, _ in known if br.has_errors]
    error_summary_msg: Message | None = None
    if bloggers_with_errors:
        error_summary_msg = await _send_error_summary(eff, bloggers_with_errors, lang)

    for blogger_result, db_blogger in known:
        method = await get_primary_method(db_blogger["id"])

        if not method:
            no_method.append((blogger_result, db_blogger))
            continue

        key = _storage_key(blogger_result.blogger)
        default_fmt = user.get("default_fmt") or "oneline"
        context.user_data[key] = {
            "result": blogger_result, "method_type": method["type"],
            "address": method["address"], "method_id": method["id"],
            "db_blogger": db_blogger, "fmt": default_fmt,
            "output_mode": output_mode,
        }

        reply_to = error_summary_msg if blogger_result.has_errors else None

        await _send_payout_block(
            eff, blogger_result, method["type"], method["address"],
            method["id"], key, default_fmt, lang, output_mode,
            reply_to=reply_to,
        )

        games_str = ", ".join(blogger_result.games)
        await save_payout(
            blogger_id=db_blogger["id"], manager_id=user["id"],
            amount_raw=blogger_result.total_price_display,
            method_id=method["id"], videos_count=blogger_result.video_count,
            game=games_str, mode=blogger_result.mode,
            raw_input=context.user_data.get("payout_raw", ""),
            formatted_text=format_oneline(blogger_result, method["type"], method["address"], lang),
        )
        log_info("PAYOUT_CREATED", user_id=user["telegram_id"], username=user["username"],
                 blogger=blogger_result.blogger, amount=blogger_result.total_price_display,
                 method=method["type"])
        await db_log(user["id"], "PAYOUT_CREATED",
                     f"blogger={blogger_result.blogger} | amount={blogger_result.total_price_display}")

    # Offer quick method add for bloggers without methods
    if no_method:
        for blogger_result, db_blogger in no_method:
            if lang == "ru":
                text = f"Нет метода оплаты для {blogger_result.blogger}. Добавить сейчас?"
            else:
                text = f"No payment method for {blogger_result.blogger}. Add now?"
            buttons = [
                [InlineKeyboardButton(
                    METHOD_LABELS[t],
                    callback_data=f"qmt:{t}:{blogger_result.blogger}"
                ) for t in METHOD_TYPES]
            ]
            await eff.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    if skipped:
        await eff.reply_text(
            f"Пропущены: {', '.join(skipped)}" if lang == "ru"
            else f"Skipped: {', '.join(skipped)}"
        )

    # Collect all generated payout texts for "copy all"
    all_texts = []
    for br, _ in known:
        key = _storage_key(br.blogger)
        data = context.user_data.get(key)
        if data:
            fmt = data.get("fmt", "oneline")
            out = data.get("output_mode", "block")
            text_raw = (format_oneline if fmt == "oneline" else format_multiline)(
                data["result"], data["method_type"], data["address"], lang
            )
            all_texts.append(text_raw)

    context.user_data["all_payout_texts"] = all_texts

    # Summary message
    has_any_errors = any(br.has_errors for br, _ in known)
    if lang == "ru":
        count = len([b for b, _ in known if b not in [s for s in skipped]])
        if has_any_errors:
            summary = f"Выплаты сформированы ({len(all_texts)} шт.). Есть ошибки — см. сводку выше."
        else:
            summary = f"Выплаты сформированы ({len(all_texts)} шт.). Всё в порядке."
    else:
        if has_any_errors:
            summary = f"Payouts generated ({len(all_texts)}). Errors found — see summary above."
        else:
            summary = f"Payouts generated ({len(all_texts)}). All good."

    await eff.reply_text(summary, reply_markup=_nav_keyboard(lang))
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Toggle format
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
        await query.answer("Data expired." if lang == "en" else "Данные устарели.", show_alert=True)
        return

    new_fmt = "multiline" if current_fmt == "oneline" else "oneline"
    data["fmt"] = new_fmt
    await _send_payout_block(
        query, data["result"], data["method_type"], data["address"],
        method_id, key, new_fmt, lang, data.get("output_mode", "block"), edit=True,
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
        await query.answer("Data expired." if lang == "en" else "Данные устарели.", show_alert=True)
        return

    methods = await get_active_methods(data["db_blogger"]["id"])
    if not methods:
        await query.answer("No payment methods." if lang == "en" else "Нет методов.", show_alert=True)
        return

    # Store methods in user_data to avoid passing address in callback_data
    # (Telegram callback_data limit is 64 bytes)
    context.user_data[f"chm_methods_{key}"] = {str(m["id"]): m for m in methods}

    buttons = []
    for m in methods:
        label = METHOD_LABELS.get(m["type"], m["type"])
        star = " ★" if m.get("is_primary") else ""
        buttons.append([InlineKeyboardButton(
            f"{label}: {m['address']}{star}",
            callback_data=f"pt_sel:{key}:{m['id']}"
        )])
    header = (
        f"Метод для {data['result'].blogger}:"
        if lang == "ru" else
        f"Method for {data['result'].blogger}:"
    )
    await query.edit_message_text(header, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_select_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    parts = query.data.split(":", 2)
    key       = parts[1]
    method_id = int(parts[2])

    data = context.user_data.get(key)
    if not data:
        await query.answer("Data expired.", show_alert=True)
        return

    # Look up method details from user_data cache
    methods_cache = context.user_data.get(f"chm_methods_{key}", {})
    method = methods_cache.get(str(method_id))
    if not method:
        # Fallback: fetch from DB
        from database.queries import get_method_by_id
        method = await get_method_by_id(method_id)
    if not method:
        await query.answer("Method not found.", show_alert=True)
        return

    method_type = method["type"]
    address     = method["address"]
    data.update({"method_type": method_type, "address": address, "method_id": method_id})
    await _send_payout_block(
        query, data["result"], method_type, address,
        method_id, key, data.get("fmt", "oneline"), lang,
        data.get("output_mode", "block"), edit=True,
    )



async def cb_payout_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data.clear()
    await query.edit_message_text(
        "Отменено." if lang == "ru" else "Cancelled."
    )
    return ConversationHandler.END


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
# Navigation callbacks (home, new payout, copy all)
# --------------------------------------------------------------------------- #
async def cb_nav_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg = update.effective_user
    user = await get_user(tg.id)
    lang = get_lang(user) if user else "en"
    role = user.get("role", "manager") if user else "manager"
    from handlers.start import _start_text, _main_keyboard
    await query.message.reply_text(
        _start_text(tg.first_name, lang),
        reply_markup=_main_keyboard(lang, role),
    )


async def cb_nav_payout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer(
        "Нажмите 💸 внизу для новой выплаты." if (await get_user(update.effective_user.id) or {}).get("lang") == "ru"
        else "Press 💸 below for a new payout.",
        show_alert=True,
    )


async def cb_nav_copy_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    texts = context.user_data.get("all_payout_texts", [])
    if not texts:
        await query.answer(
            "Нет данных." if lang == "ru" else "No data.", show_alert=True
        )
        return

    combined = "\n\n".join(texts)

    # If fits in one message — send as text, else as file
    from handlers.common import nav_keyboard as _nav_kb
    if len(combined) <= 4000:
        await query.message.reply_text(combined, reply_markup=_nav_kb(lang))
    else:
        buf = io.BytesIO(combined.encode("utf-8"))
        buf.name = "payouts.txt"
        caption = "Все выплаты" if lang == "ru" else "All payouts"
        await query.message.reply_document(
            document=buf,
            filename="payouts.txt",
            caption=caption,
        )
        await query.message.reply_text(
            "Готово." if lang == "ru" else "Done.",
            reply_markup=_nav_kb(lang),
        )


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_payout_handlers(app):
    app.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("payout", cmd_payout),
            MessageHandler(filters.Regex(r"^💸"), cmd_payout),
        ],
        states={
            WAIT_ROWS: [
                CallbackQueryHandler(cb_payout_cancel, pattern=r"^payout_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, payout_got_rows),
            ],
            WAIT_UNKNOWN: [
                CallbackQueryHandler(cb_unknown,        pattern=r"^unk:"),
                CallbackQueryHandler(cb_payout_cancel, pattern=r"^payout_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, _wait_unknown_text),
            ],
            WAIT_QUICK_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, quick_address_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        conversation_timeout=600,
        per_message=False,
    ))

    app.add_handler(CallbackQueryHandler(cb_payout_toggle,   pattern=r"^pt_tog:"))
    app.add_handler(CallbackQueryHandler(cb_change_method,   pattern=r"^pt_chm:"))
    app.add_handler(CallbackQueryHandler(cb_select_method,   pattern=r"^pt_sel:"))
    app.add_handler(CallbackQueryHandler(cb_quick_method_type, pattern=r"^qmt:"))
    app.add_handler(CallbackQueryHandler(cb_nav_home,             pattern=r"^nav_home$"))
    app.add_handler(CallbackQueryHandler(cb_nav_payout,           pattern=r"^nav_payout$"))
    app.add_handler(CallbackQueryHandler(cb_nav_copy_all,         pattern=r"^nav_copy_all$"))