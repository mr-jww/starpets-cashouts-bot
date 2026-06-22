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
    get_user, get_blogger_by_name,
    get_active_methods, get_active_methods_by_type, get_primary_method,
    save_payout, db_log, METHOD_LABELS,
)
from services.parser import parse_rows, looks_like_lost_tabs, BloggerResult
from services.formatter import format_oneline, format_multiline, payout_warning
from services.logger import log_info
from handlers.common import get_user_or_reject, get_lang
from handlers.start import _universal_cancel
import re

WAIT_ROWS = 0
CANCEL_TEXT = {"ru": "Отменено.", "en": "Cancelled."}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _parse_flags(args: list[str]) -> str | None:
    for arg in (args or []):
        if arg.lower().startswith("amb-"):
            val = arg[4:]
            return "" if val.lower() == "all" else val
    return None


def _manager_matches(flt: str, cell: str) -> bool:
    """True if the manager filter matches the row's manager cell.

    Matches a whole name, not a substring: "Tom" does not match "Tommy". The
    cell may also carry extra text ("John (lead)") and still match.
    """
    flt = (flt or "").strip().lower()
    if not flt:
        return True
    cell = (cell or "").strip().lower()
    if cell == flt:
        return True
    return flt in re.split(r"[^\w]+", cell)


def _get_output_mode(user: dict) -> str:
    return user.get("output_mode") or "block"


def _payout_keyboard(key: str, method_id: int, fmt: str, lang: str, output_mode: str,
                     show_change: bool = True) -> InlineKeyboardMarkup:
    if lang == "ru":
        # Show current format, clicking switches to opposite
        toggle = "↕ Однострочный" if fmt == "multiline" else "↕ Многострочный"
        change  = "💳 Поменять метод"
    else:
        toggle = "↕ One line" if fmt == "multiline" else "↕ Multiline"
        change  = "💳 Change method"
    mid = method_id if method_id is not None else 0
    row = [InlineKeyboardButton(toggle, callback_data=f"pt_tog:{key}:{mid}:{fmt}")]
    if show_change:
        row.append(InlineKeyboardButton(change, callback_data=f"pt_chm:{key}"))
    return InlineKeyboardMarkup([row])


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
    show_change_method: bool = True,
):
    text = (format_oneline if fmt == "oneline" else format_multiline)(
        result, method_type, address, lang
    )
    msg_text = _format_block(text, output_mode)
    keyboard = _payout_keyboard(key, method_id, fmt, lang, output_mode, show_change=show_change_method)
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

    # Telegram Web and the mobile apps turn clipboard tabs into single spaces,
    # which collapses each row into one column. Detect that specific case and
    # give actionable advice instead of the cryptic "not enough columns" error.
    if not result.bloggers and looks_like_lost_tabs(raw_text):
        log_info("PAYOUT_PARSE_LOST_TABS", user_id=user["telegram_id"],
                 username=user["username"], manager_filter=user.get("manager_filter") or "")
        await db_log(user["id"], "PAYOUT_PARSE_LOST_TABS", "column separators lost (tabs to spaces)")
        if lang == "ru":
            await update.message.reply_text(
                "Похоже, при копировании потерялись разделители между столбцами – "
                "так делает Telegram в браузере и на телефоне.\n\n"
                "Откройте бота в приложении Telegram Desktop на компьютере и вставьте строки "
                "заново через Ctrl+Shift+V. В браузерной и мобильной версии вставка из таблицы "
                "пока работает неправильно."
            )
        else:
            await update.message.reply_text(
                "It looks like the column separators were lost while copying – "
                "this is what Telegram does in the browser and on phones.\n\n"
                "Open the bot in the Telegram Desktop app on a computer and paste the rows "
                "again with Ctrl+Shift+V. Pasting from the spreadsheet does not work correctly "
                "in the web and mobile versions yet."
            )
        return ConversationHandler.END

    if result.critical_errors and not result.bloggers:
        log_info("PAYOUT_PARSE_FAILED", user_id=user["telegram_id"],
                 username=user["username"], reason="critical_errors",
                 lines=len(result.critical_errors))
        await db_log(user["id"], "PAYOUT_PARSE_FAILED", f"critical_errors={len(result.critical_errors)}")
        await update.message.reply_text(
            ("Не удалось разобрать вставленный текст:\n" if lang == "ru" else "Could not parse the pasted text:\n")
            + "\n".join(result.critical_errors)
        )
        return ConversationHandler.END

    if not result.bloggers:
        log_info("PAYOUT_PARSE_FAILED", user_id=user["telegram_id"],
                 username=user["username"], reason="no_rows")
        await db_log(user["id"], "PAYOUT_PARSE_FAILED", "no data rows")
        await update.message.reply_text(
            "Не нашёл строк с данными. Убедитесь, что скопировали из таблицы через Ctrl+Shift+V." if lang == "ru" else "No data rows found. Make sure you copied from the spreadsheet using Ctrl+Shift+V."
        )
        return ConversationHandler.END

    # Apply manager filter
    if effective_filter:
        filtered = [
            b for b in result.bloggers
            if any(_manager_matches(effective_filter, r.manager) for r in b.rows)
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

    context.user_data.update({
        "payout_bloggers": result.bloggers,
        "payout_raw": raw_text,
    })
    return await _emit_payouts(update, context)


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
# Emit payout blocks (no interactive prompts)
# --------------------------------------------------------------------------- #
def _split_rows_by_method(br: BloggerResult, fallback_type: str) -> list[tuple[str, BloggerResult]]:
    """Group a blogger's rows by the method given in the spreadsheet, in order.

    Rows with no method fall back to fallback_type (the blogger's base default).
    Returns [(method_type, sub_result), ...]; a single group means one block.
    """
    groups: dict[str, list] = {}
    for row in br.rows:
        groups.setdefault(row.pay_method or fallback_type, []).append(row)
    out = []
    for mtype, rows in groups.items():
        sub = BloggerResult(blogger=br.blogger, mode=br.mode)
        sub.rows = rows
        out.append((mtype, sub))
    return out


async def _resolve_jobs(bloggers, user, method_from_table):
    """Turn parsed bloggers into payout jobs.

    Base source: one job per blogger using the blogger's primary method.
    Table source: one job per method given in the rows, so different methods
    become separate payouts; the address is the blogger's method of that type.
    Each job: (result, method_type, address, method_id, has_reqs, db_blogger).
    """
    jobs = []
    for br in bloggers:
        db_b = await get_blogger_by_name(br.blogger, user["id"])
        primary = await get_primary_method(db_b["id"]) if db_b else None

        if not method_from_table:
            if primary:
                jobs.append((br, primary["type"], primary["address"], primary["id"], True, db_b))
            else:
                jobs.append((br, br.pay_method_type, "", None, False, db_b))
            continue

        primary_type = primary["type"] if primary else ""
        for mtype, sub in _split_rows_by_method(br, primary_type):
            m = None
            if db_b and mtype:
                methods_t = await get_active_methods_by_type(db_b["id"], mtype)
                m = methods_t[0] if methods_t else None
            if m:
                jobs.append((sub, m["type"], m["address"], m["id"], True, db_b))
            else:
                jobs.append((sub, mtype, "", None, False, db_b))
    return jobs


async def _emit_payouts(target, context):
    """Produce a payout block for every job, without any interactive prompts.

    A job with a usable method gets a normal block. A job without one — no
    method in the base, or the method named in the rows is not on file — is
    handled by include_no_method: when on, a block is produced with the method
    type from the spreadsheet and an empty address; when off, it is skipped.
    Such cases are always listed in the final report.
    """
    user = context.user_data["user"]
    lang = get_lang(user)
    output_mode = _get_output_mode(user)
    default_fmt = user.get("default_fmt") or "oneline"
    include_no_method = bool(user.get("include_no_method", 0))
    method_from_table = bool(user.get("method_from_table", 0))
    bloggers: list[BloggerResult] = context.user_data.get("payout_bloggers", [])

    eff = getattr(target, "effective_message", None) or getattr(target, "message", None) or target

    jobs = await _resolve_jobs(bloggers, user, method_from_table)

    # Send the error summary first (one entry per blogger), so error blocks can
    # reply to it.
    bloggers_with_errors = [b for b in bloggers if b.has_errors]
    error_summary_msg: Message | None = None
    if bloggers_with_errors:
        error_summary_msg = await _send_error_summary(eff, bloggers_with_errors, lang)

    all_texts: list[str] = []
    no_reqs: list[str] = []      # jobs without payment details (for the report)
    error_count = 0
    warn_count = 0

    for idx, (res, method_type, address, method_id, has_reqs, db_b) in enumerate(jobs):
        if not has_reqs:
            label = res.blogger
            if method_type:
                label += f" ({METHOD_LABELS.get(method_type, method_type)})"
            no_reqs.append(label)
            if not include_no_method:
                continue

        key = _storage_key(f"{res.blogger}_{idx}")
        context.user_data[key] = {
            "result": res, "method_type": method_type, "address": address,
            "method_id": method_id, "db_blogger": db_b, "fmt": default_fmt,
            "output_mode": output_mode, "has_reqs": has_reqs,
        }
        reply_to = error_summary_msg if res.has_errors else None
        await _send_payout_block(
            eff, res, method_type, address, method_id, key,
            default_fmt, lang, output_mode, reply_to=reply_to,
            show_change_method=has_reqs,
        )

        all_texts.append(
            (format_oneline if default_fmt == "oneline" else format_multiline)(
                res, method_type, address, lang
            )
        )
        if res.has_errors:
            error_count += 1
        if payout_warning(method_type, res.total_price_display, lang):
            warn_count += 1

        # Save to history only for bloggers that exist in the base.
        if db_b:
            await save_payout(
                blogger_id=db_b["id"], manager_id=user["id"],
                amount_raw=res.total_price_display, method_id=method_id,
                videos_count=res.video_count, game=", ".join(res.games),
                mode=res.mode, raw_input=context.user_data.get("payout_raw", ""),
                formatted_text=format_oneline(res, method_type, address, lang),
            )
            log_info("PAYOUT_CREATED", user_id=user["telegram_id"], username=user["username"],
                     blogger=res.blogger, amount=res.total_price_display,
                     method=method_type or "none", videos=res.video_count,
                     has_errors=res.has_errors, no_method=not has_reqs,
                     manager_filter=user.get("manager_filter") or "")
            await db_log(user["id"], "PAYOUT_CREATED",
                         f"blogger={res.blogger} | amount={res.total_price_display}"
                         + ("" if has_reqs else " | no_method"))

    context.user_data["all_payout_texts"] = all_texts

    emitted = len(all_texts)
    if lang == "ru":
        lines = [f"Готово. Сформировано {emitted} выплат."]
        if error_count:
            lines.append(f"⚠️ Ошибки в строках: {error_count}")
        if warn_count:
            lines.append(f"⚠️ Ниже минимума выплаты: {warn_count}")
        if no_reqs:
            state = "выведены с пустым методом" if include_no_method else "пропущены"
            lines.append(f"⚠️ Блогеры без реквизитов ({state}): {', '.join(no_reqs)}")
            lines.append("Чтобы реквизиты подставлялись автоматически, добавьте их в таблицу и синхронизируйтесь.")
        summary = "\n".join(lines)
    else:
        lines = [f"Done. {emitted} payout(s) generated."]
        if error_count:
            lines.append(f"⚠️ Row errors: {error_count}")
        if warn_count:
            lines.append(f"⚠️ Below minimum: {warn_count}")
        if no_reqs:
            state = "shown with an empty method" if include_no_method else "skipped"
            lines.append(f"⚠️ Bloggers without details ({state}): {', '.join(no_reqs)}")
            lines.append("To fill payment details automatically, add them to the spreadsheet and sync.")
        summary = "\n".join(lines)
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
    method_id = int(method_id_str) if method_id_str.lstrip("-").isdigit() else 0
    data = context.user_data.get(key)
    if not data:
        await query.answer("Data expired." if lang == "en" else "Данные устарели.", show_alert=True)
        return

    new_fmt = "multiline" if current_fmt == "oneline" else "oneline"
    data["fmt"] = new_fmt
    await _send_payout_block(
        query, data["result"], data["method_type"], data["address"],
        method_id, key, new_fmt, lang, data.get("output_mode", "block"), edit=True,
        show_change_method=data.get("has_reqs", True),
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
        parse_mode="Markdown",
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
    app.add_handler(CallbackQueryHandler(cb_payout_cancel,        pattern=r"^payout_cancel$"))
    app.add_handler(CallbackQueryHandler(cb_nav_home,             pattern=r"^nav_home$"))
    app.add_handler(CallbackQueryHandler(cb_nav_payout,           pattern=r"^nav_payout$"))
    app.add_handler(CallbackQueryHandler(cb_nav_copy_all,         pattern=r"^nav_copy_all$"))