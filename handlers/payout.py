"""
/payout handler.

Flags:
  /payout             - use manager_filter from settings
  /payout amb-Name    - filter by John
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
from services.formatter import format_oneline, format_multiline, both_formats, payout_warning
from services.logger import log_info
from handlers.common import get_user_or_reject, get_lang
from handlers.start import _universal_cancel
import re

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
        sent = await reply_to.reply_text(msg_text, reply_markup=keyboard, parse_mode=parse_mode)
    else:
        eff = getattr(target, "effective_message", None) or getattr(target, "message", None) or target
        sent = await eff.reply_text(msg_text, reply_markup=keyboard, parse_mode=parse_mode)

    # Warning if below minimum payout threshold
    if not edit:
        warning = payout_warning(method_type, result.total_price_display, lang)
        if warning:
            eff_w = getattr(target, "effective_message", None) or getattr(target, "message", None) or target
            try:
                await eff_w.reply_text(warning)
            except Exception:
                pass


def _storage_key(name: str) -> str:
    return f"pd_{re.sub(r'[^a-zA-Z0-9_]', '_', name)}"




def _nav_keyboard(lang: str) -> InlineKeyboardMarkup:
    if lang == "ru":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🏠 Главная",        callback_data="nav_home"),
                InlineKeyboardButton("💸 Новая выплата",  callback_data="start_payout"),
            ],
            [InlineKeyboardButton("📤 Вывести все выплаты", callback_data="nav_copy_all")],
        ])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏠 Home",        callback_data="nav_home"),
            InlineKeyboardButton("💸 New payout",  callback_data="start_payout"),
        ],
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
    # Answer callback query if triggered from inline button
    if update.callback_query:
        await update.callback_query.answer()
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
        text = "Выдели строки в таблице, скопируй их (*Ctrl+C*) и вставь сюда (*Ctrl+Shift+V*)." + hint
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✕ Отмена", callback_data="payout_cancel"),
        ]])
    else:
        text = "Select the rows in the spreadsheet, copy them (*Ctrl+C*) and paste here (*Ctrl+Shift+V*)." + hint
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✕ Cancel", callback_data="payout_cancel"),
        ]])
    await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return WAIT_ROWS


# --------------------------------------------------------------------------- #
# Got rows
# --------------------------------------------------------------------------- #
async def payout_got_rows(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["_last_action"] = "payout_got_rows"
    user = context.user_data["user"]
    lang = get_lang(user)
    raw_text = update.message.text.strip()

    # Nav buttons — end conversation silently
    _nav = {"🏠 Home", "🏠 Главная", "💸 Payout", "💸 Выплата",
            "👥 Bloggers", "👥 Блогеры", "⚙️ Settings", "⚙️ Настройки"}
    if raw_text in _nav or any(raw_text.startswith(e) for e in ("🏠", "💸", "👥", "⚙️")):
        return ConversationHandler.END

    effective_filter = context.user_data.get("effective_filter")

    result = parse_rows(raw_text, lang)

    if result.critical_errors and not result.bloggers:
        await update.message.reply_text(
            ("Не удалось разобрать вставленный текст:\n" if lang == "ru" else "Could not parse the pasted text:\n")
            + "\n".join(result.critical_errors)
        )
        return ConversationHandler.END

    if not result.bloggers:
        await update.message.reply_text(
            "Не нашёл строк с данными. Убедись, что скопировал из таблицы через Ctrl+Shift+V." if lang == "ru" else "No data rows found. Make sure you copied from the spreadsheet using Ctrl+Shift+V."
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
                f"Среди вставленных строк нет записей менеджера {effective_filter}.\nЧтобы обработать все строки без фильтра: /payout amb-all"
                if lang == "ru" else
                f"No rows found for manager {effective_filter} in the pasted data.\nTo process all rows without a filter: /payout amb-all"
            )
            return ConversationHandler.END
        result.bloggers = filtered


    # Apply payment status filter
    # Settings: include_paid, warn_paid, include_pending, warn_pending
    include_paid    = bool(user.get("include_paid",    0))
    warn_paid       = bool(user.get("warn_paid",       1))
    include_pending = bool(user.get("include_pending", 0))
    warn_pending    = bool(user.get("warn_pending",    1))

    paid_rows:    list[str] = []   # rows that are PAID
    pending_rows: list[str] = []   # rows that are PENDING

    for b in result.bloggers:
        kept = []
        for row in b.rows:
            status = (row.pay_status or "").upper()
            if status == "PAID":
                paid_rows.append(f"{b.blogger} ({row.platform}, {row.date})")
                if include_paid:
                    kept.append(row)
                # if not include_paid: row is dropped
            elif status == "PENDING":
                pending_rows.append(f"{b.blogger} ({row.platform}, {row.date})")
                if include_pending:
                    kept.append(row)
            else:
                kept.append(row)
        b.rows = kept

    result.bloggers = [b for b in result.bloggers if b.rows]

    # Build warning messages
    warn_lines = []

    def _fmt_rows(rows: list[str]) -> list[str]:
        out = [f"  - {s}" for s in rows[:10]]
        if len(rows) > 10:
            out.append(f"  ... +{len(rows) - 10}" if lang == "ru" else f"  ... +{len(rows) - 10} more")
        return out

    if lang == "ru":
        if paid_rows and warn_paid:
            action = "добавлены в выплату" if include_paid else "пропущены"
            warn_lines.append(f"Строки со статусом PAID ({len(paid_rows)}) – {action}:")
            warn_lines.extend(_fmt_rows(paid_rows))
            if not include_paid:
                warn_lines.append("  Включить: Настройки → PAID → Включать")
        if pending_rows and warn_pending:
            action = "добавлены в выплату" if include_pending else "пропущены"
            warn_lines.append(f"Строки со статусом PENDING ({len(pending_rows)}) – {action}:")
            warn_lines.extend(_fmt_rows(pending_rows))
            if not include_pending:
                warn_lines.append("  Включить: Настройки → PENDING → Включать")
    else:
        if paid_rows and warn_paid:
            action = "included in payout" if include_paid else "skipped"
            warn_lines.append(f"PAID rows ({len(paid_rows)}) – {action}:")
            warn_lines.extend(_fmt_rows(paid_rows))
            if not include_paid:
                warn_lines.append("  To include: Settings → PAID → Include")
        if pending_rows and warn_pending:
            action = "included in payout" if include_pending else "skipped"
            warn_lines.append(f"PENDING rows ({len(pending_rows)}) – {action}:")
            warn_lines.extend(_fmt_rows(pending_rows))
            if not include_pending:
                warn_lines.append("  To include: Settings → PENDING → Include")

    if warn_lines:
        await update.message.reply_text("\n".join(warn_lines))

    if not result.bloggers:
        await update.message.reply_text(
            "Все строки имеют статус PAID или PENDING и были пропущены. Настройки фильтра: /settings."
            if lang == "ru" else
            "All rows have PAID or PENDING status and were skipped. Filter settings: /settings."
        )
        return ConversationHandler.END

    if result.critical_errors:
        await update.message.reply_text(
            ("Часть строк не удалось разобрать и они пропущены:\n" if lang == "ru" else "Some rows could not be parsed and were skipped:\n")
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
    header = f"Распознано блогеров: {len(bloggers)}" if lang == "ru" else f"Bloggers found: {len(bloggers)}"
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
        text = f"Следующие блогеры не найдены в базе ({len(unknown)}):\n{names}\n\nДобавить их и продолжить?"
    else:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Add all", callback_data="unk:add"),
            InlineKeyboardButton("Skip all", callback_data="unk:skip"),
        ]])
        text = f"The following bloggers are not in the database ({len(unknown)}):\n{names}\n\nAdd them and continue?"
    await target.reply_text(text, reply_markup=keyboard)
    return WAIT_UNKNOWN


async def cb_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = context.user_data["user"]
    lang = get_lang(user)
    unknown: list[BloggerResult] = context.user_data["unknown"]

    if query.data.split(":")[1] == "add":
        added_count = 0
        for b in unknown:
            db_b = await add_blogger(b.blogger, user["id"])
            if db_b is None:
                db_b = await get_blogger_by_name(b.blogger, user["id"])
            if db_b:
                context.user_data["known"].append((b, db_b))
                added_count += 1
        if lang == "ru":
            await query.edit_message_text(
                f"Добавлено: {added_count}. Теперь нужно указать методы оплаты для каждого."
            )
        else:
            await query.edit_message_text(
                f"Added: {added_count}. Now set a payment method for each of them."
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
        "Воспользуйтесь кнопками выше." if lang == "ru" else "Use the buttons above."
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
        "site":       {"ru": f"Укажи Profile ID для {blogger_name}:",
                       "en": f"Enter Profile ID for {blogger_name}:"},
        "usdt-trc20": {"ru": f"Укажи адрес кошелька USDT-TRC20 для {blogger_name}:",
                       "en": f"Enter USDT-TRC20 wallet address for {blogger_name}:"},
        "paypal":     {"ru": f"Укажи адрес PayPal для {blogger_name}:",
                       "en": f"Enter PayPal address for {blogger_name}:"},
    }
    await query.edit_message_text(hints.get(method_type, {}).get(lang, "Enter address:"))
    return WAIT_QUICK_ADDRESS


async def quick_address_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data.get("user") or await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    address = update.message.text.strip()

    if not address:
        await update.message.reply_text("Поле не может быть пустым. Введите реквизиты." if lang == "ru" else "Field cannot be empty. Please enter the payment details.")
        return WAIT_QUICK_ADDRESS

    blogger_name = context.user_data.get("qm_blogger", "")
    method_type  = context.user_data.get("qm_type", "site")
    db_b = await get_blogger_by_name(blogger_name, user["id"])

    if db_b:
        method = await add_payment_method(db_b["id"], method_type, address)
        await set_primary_method(method["id"], db_b["id"])
        type_label = METHOD_LABELS.get(method_type, method_type)
        log_info("QUICK_METHOD_ADDED", user_id=user["telegram_id"],
                 username=user["username"], blogger=blogger_name, type=method_type)
        await db_log(user["id"], "QUICK_METHOD_ADDED",
                     f"blogger={blogger_name} | type={method_type}")

        # Send payout block only for this blogger
        output_mode = _get_output_mode(user)
        default_fmt = user.get("default_fmt") or "oneline"
        known: list = context.user_data.get("known", [])
        for blogger_result, db_blogger in known:
            if db_blogger["id"] == db_b["id"]:
                key = _storage_key(blogger_name)
                context.user_data[key] = {
                    "result": blogger_result, "method_type": method_type,
                    "address": address, "method_id": method["id"],
                    "db_blogger": db_blogger, "fmt": default_fmt,
                    "output_mode": output_mode,
                }
                await _send_payout_block(
                    update.message, blogger_result, method_type, address,
                    method["id"], key, default_fmt, lang, output_mode,
                )
                games_str = ", ".join(blogger_result.games)
                await save_payout(
                    blogger_id=db_blogger["id"], manager_id=user["id"],
                    amount_raw=blogger_result.total_price_display,
                    method_id=method["id"], videos_count=blogger_result.video_count,
                    game=games_str, mode=blogger_result.mode,
                    raw_input=context.user_data.get("payout_raw", ""),
                    formatted_text=format_oneline(blogger_result, method_type, address, lang),
                )
                log_info("PAYOUT_CREATED", user_id=user["telegram_id"],
                         username=user["username"], blogger=blogger_name,
                         amount=blogger_result.total_price_display, method=method_type,
                         videos=blogger_result.video_count, has_errors=blogger_result.has_errors,
                         manager_filter=user.get("manager_filter") or "")
                break

    # Show next in no_method queue or finish
    queue = context.user_data.get("no_method_queue", [])
    if queue:
        next_name, next_id = queue.pop(0)
        context.user_data["no_method_queue"] = queue
        context.user_data["qm_blogger"] = next_name
        known2: list = context.user_data.get("known", [])
        next_result = next((br for br, db in known2 if db["id"] == next_id), None)
        if next_result:
            if lang == "ru":
                text = f"Нет метода оплаты для {next_name}. Добавить сейчас?"
            else:
                text = f"No payment method for {next_name}. Add now?"
            buttons = [
                [InlineKeyboardButton(METHOD_LABELS[t], callback_data=f"qmt:{t}:{next_name}")
                 for t in METHOD_TYPES],
                [InlineKeyboardButton(
                    "Пропустить" if lang == "ru" else "Skip",
                    callback_data=f"qmt:skip:{next_name}"
                )]
            ]
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
            return WAIT_QUICK_ADDRESS

    eff = update.message
    return await _finish_payout(eff, context, lang)


async def _finish_payout(eff, context, lang: str):
    """Send final summary and end conversation."""
    known: list = context.user_data.get("known", [])
    skipped: list = context.user_data.get("skipped", [])
    all_texts = []
    for br, _ in known:
        key = _storage_key(br.blogger)
        data = context.user_data.get(key)
        if data:
            fmt = data.get("fmt", "oneline")
            text_raw = (format_oneline if fmt == "oneline" else format_multiline)(
                data["result"], data["method_type"], data["address"], lang
            )
            all_texts.append(text_raw)

    context.user_data["all_payout_texts"] = all_texts

    if skipped:
        await eff.reply_text(
            f"Пропущены (нет метода оплаты): {', '.join(skipped)}" if lang == "ru"
            else f"Skipped (no payment method): {', '.join(skipped)}"
        )

    has_any_errors = any(br.has_errors for br, _ in known)

    # Count warnings (below minimum)
    warn_count = sum(
        1 for br, _ in known
        if (data := context.user_data.get(_storage_key(br.blogger)))
        and payout_warning(data["method_type"], br.total_price_display, lang)
    )

    error_count = sum(1 for br, _ in known if br.has_errors)

    if lang == "ru":
        lines = [f"Готово. Сформировано {len(all_texts)} выплат."]
        if error_count:
            lines.append(f"⚠️ Ошибки в строках: {error_count}")
        if warn_count:
            lines.append(f"⚠️ Ниже минимума выплаты: {warn_count}")
        if skipped:
            lines.append(f"⚠️ Пропущено (нет метода оплаты): {len(skipped)}")
        summary = "\n".join(lines)
    else:
        lines = [f"Done. {len(all_texts)} payout(s) generated."]
        if error_count:
            lines.append(f"⚠️ Row errors: {error_count}")
        if warn_count:
            lines.append(f"⚠️ Below minimum: {warn_count}")
        if skipped:
            lines.append(f"⚠️ Skipped (no payment method): {len(skipped)}")
        summary = "\n".join(lines)
    await eff.reply_text(summary, reply_markup=_nav_keyboard(lang))
    return ConversationHandler.END


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
                 method=method["type"], videos=blogger_result.video_count,
                 has_errors=blogger_result.has_errors,
                 manager_filter=user.get("manager_filter") or "")
        await db_log(user["id"], "PAYOUT_CREATED",
                     f"blogger={blogger_result.blogger} | amount={blogger_result.total_price_display}")

    # Offer quick method add for bloggers without methods — one at a time
    if no_method:
        # Store the queue, show only first
        context.user_data["no_method_queue"] = [
            (br.blogger, db_b["id"]) for br, db_b in no_method[1:]
        ]
        blogger_result, db_blogger = no_method[0]
        if lang == "ru":
            text = f"Нет метода оплаты для {blogger_result.blogger}. Добавить сейчас?"
        else:
            text = f"No payment method for {blogger_result.blogger}. Add now?"
        buttons = [
            [InlineKeyboardButton(
                METHOD_LABELS[t],
                callback_data=f"qmt:{t}:{blogger_result.blogger}"
            ) for t in METHOD_TYPES],
            [InlineKeyboardButton(
                "Пропустить" if lang == "ru" else "Skip",
                callback_data=f"qmt:skip:{blogger_result.blogger}"
            )]
        ]
        await eff.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return WAIT_QUICK_ADDRESS

    return await _finish_payout(eff, context, lang)


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
    try:
        await query.message.delete()
    except Exception:
        await query.edit_message_text("Выплата отменена." if lang == "ru" else "Payout cancelled.")
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
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    await query.answer(
        "Нажмите 💸 внизу для новой выплаты." if lang == "ru"
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
            CallbackQueryHandler(cmd_payout, pattern=r"^start_payout$"),
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
        fallbacks=[
            CommandHandler("cancel",         cmd_cancel),
            CommandHandler("start",           _universal_cancel),
            CommandHandler("bloggers",         _universal_cancel),
            CommandHandler("reformat",         _universal_cancel),
            CommandHandler("import_bloggers",  _universal_cancel),
            CommandHandler("settings",         _universal_cancel),
            MessageHandler(filters.Regex(r"^(🏠|💸|👥|⚙️)"), _universal_cancel),
            CallbackQueryHandler(_universal_cancel, pattern=r"^show_settings$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^show_start$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^show_more$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^set_mgr$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^mgr_pick:"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^mgr_manual$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^mgr_clear$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^go_import$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^rf_again$"),
        ],
        conversation_timeout=600,
        per_message=False,
    ))

    app.add_handler(CallbackQueryHandler(cb_payout_toggle,   pattern=r"^pt_tog:"))
    app.add_handler(CallbackQueryHandler(cb_change_method,   pattern=r"^pt_chm:"))
    app.add_handler(CallbackQueryHandler(cb_select_method,   pattern=r"^pt_sel:"))
    app.add_handler(CallbackQueryHandler(cb_quick_method_type, pattern=r"^qmt:"))
    app.add_handler(CallbackQueryHandler(cb_payout_cancel,        pattern=r"^payout_cancel$"))
    app.add_handler(CallbackQueryHandler(cb_nav_home,             pattern=r"^nav_home$"))
    app.add_handler(CallbackQueryHandler(cb_nav_payout,           pattern=r"^nav_payout$"))
    app.add_handler(CallbackQueryHandler(cb_nav_copy_all,         pattern=r"^nav_copy_all$"))