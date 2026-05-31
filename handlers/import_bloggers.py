"""
/import_bloggers — bulk import of bloggers with payment methods.

Input: text message or .txt/.tsv file (up to 512 KB).

Row format (tab-separated):
  name | Site_ID | USDT-TRC20 | PayPal_email | primary_method

Rules:
- At least one of columns 2-4 must be non-empty
- Primary (col 5) must match a non-empty column: site, usdt-trc20, paypal
- If >40% of known bloggers would have their primary method changed, ask for confirmation

Safety: all changes are collected first, then applied atomically only after confirmation.
"""

from __future__ import annotations
import re
import io
from dataclasses import dataclass, field
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters,
)

from database.queries import (
    get_user, get_blogger_by_name, add_blogger,
    add_payment_method, set_primary_method, get_active_methods,
    get_primary_method, db_log,
)
from services.logger import log_info
from handlers.common import get_user_or_reject, get_lang, nav_keyboard

WAIT_DATA = 0
WAIT_CONFIRM = 1  # handled globally, not inside ConversationHandler

_PRIMARY_ALIASES = {
    "site":       "site",
    "usdt-trc20": "usdt-trc20",
    "usdt":       "usdt-trc20",
    "paypal":     "paypal",
    "pp":         "paypal",
}

_METHOD_LABELS = {
    "site":       "Site",
    "usdt-trc20": "USDT-TRC20",
    "paypal":     "PayPal",
}


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #
@dataclass
class ImportRow:
    name:    str
    site:    str = ""
    usdt:    str = ""
    paypal:  str = ""
    primary: str = ""


@dataclass
class ParsedRow:
    row:    ImportRow
    error:  str = ""  # non-empty = skip this row


@dataclass
class PlannedChange:
    """What will happen to one blogger."""
    name:            str
    is_new:          bool
    methods:         list[tuple[str, str]]        # [(type, address), ...]
    method_statuses: list[str] = None             # "added"|"changed"|"same" per method
    primary:         str = ""
    old_primary:     str = ""
    primary_changed: bool = False

    def __post_init__(self):
        if self.method_statuses is None:
            self.method_statuses = ["added"] * len(self.methods)


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
# Smart field detection patterns
_SITE_RE   = re.compile(r'^[0-9a-f]{24}$', re.IGNORECASE)
_USDT_RE   = re.compile(r'^T[1-9A-HJ-NP-Za-km-z]{33}$')
_PAYPAL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _parse_smart_row(line: str) -> tuple[str, str, str, str, str] | None:
    """
    Parse a single row by content type, not position.
    Returns (name, site, usdt, paypal, primary) or None if unparseable.

    Detection rules:
    - Last token = primary method keyword (site/usdt-trc20/paypal) if recognised
    - Site ID: 24 hex chars
    - USDT-TRC20: starts with T, 34 base58 chars
    - PayPal: contains @ with domain
    - Name: everything else joined with space
    """
    tokens = re.split(r'\s+', line.strip())
    tokens = [t for t in tokens if t]
    if not tokens:
        return None

    primary = ""
    if tokens and tokens[-1].lower() in _PRIMARY_ALIASES:
        primary = _PRIMARY_ALIASES[tokens[-1].lower()]
        tokens = tokens[:-1]

    site = usdt = paypal = ""
    name_parts = []

    for token in tokens:
        if _SITE_RE.match(token):
            site = token
        elif _USDT_RE.match(token):
            usdt = token
        elif _PAYPAL_RE.match(token):
            paypal = token
        else:
            name_parts.append(token)

    name = " ".join(name_parts).strip()
    return name, site, usdt, paypal, primary


def parse_import_text(text: str, lang: str) -> tuple[list[ParsedRow], list[ParsedRow]]:
    """Returns (valid, invalid). Uses smart field detection by content type."""
    valid:   list[ParsedRow] = []
    invalid: list[ParsedRow] = []

    for line_no, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue

        parsed = _parse_smart_row(line)
        if parsed is None:
            tag = f"Строка {line_no}" if lang == "ru" else f"Line {line_no}"
            invalid.append(ParsedRow(ImportRow(tag), "пустая строка" if lang == "ru" else "empty line"))
            continue

        name, site, usdt, paypal, primary = parsed

        if not name:
            tag = f"Строка {line_no}" if lang == "ru" else f"Line {line_no}"
            invalid.append(ParsedRow(ImportRow(tag), "имя не определено" if lang == "ru" else "name not found"))
            continue

        if not site and not usdt and not paypal:
            invalid.append(ParsedRow(ImportRow(name), "нет ни одного метода оплаты" if lang == "ru" else "no payment method"))
            continue

        # If no primary specified, auto-assign to the only method
        if not primary:
            found = [(t, a) for t, a in [("site", site), ("usdt-trc20", usdt), ("paypal", paypal)] if a]
            if len(found) == 1:
                primary = found[0][0]
            else:
                invalid.append(ParsedRow(ImportRow(name),
                    "не указан основной метод (их несколько)" if lang == "ru"
                    else "primary method not specified (multiple methods found)"))
                continue

        method_map = {"site": site, "usdt-trc20": usdt, "paypal": paypal}
        if not method_map.get(primary):
            invalid.append(ParsedRow(ImportRow(name),
                f"основной «{primary}» не имеет адреса" if lang == "ru"
                else f"primary '{primary}' has no address"))
            continue

        valid.append(ParsedRow(ImportRow(name=name, site=site, usdt=usdt, paypal=paypal, primary=primary)))

    return valid, invalid


# --------------------------------------------------------------------------- #
# Plan builder (no DB writes yet)
# --------------------------------------------------------------------------- #
async def build_plan(valid_rows: list[ParsedRow], manager_id: int) -> list[PlannedChange]:
    changes: list[PlannedChange] = []
    for pr in valid_rows:
        row = pr.row
        db_b = await get_blogger_by_name(row.name, manager_id)
        is_new = db_b is None

        old_primary = ""
        existing_methods: dict[str, str] = {}  # type -> address

        if not is_new:
            pm = await get_primary_method(db_b["id"])
            old_primary = pm["type"] if pm else ""
            for m in await get_active_methods(db_b["id"]):
                existing_methods[m["type"]] = m["address"]

        methods = [(t, a) for t, a in [
            ("site", row.site), ("usdt-trc20", row.usdt), ("paypal", row.paypal)
        ] if a]

        # Compute per-method status
        statuses = []
        for mtype, addr in methods:
            if mtype not in existing_methods:
                statuses.append("added")
            elif existing_methods[mtype] == addr:
                statuses.append("same")
            else:
                statuses.append("changed")

        primary_changed = (not is_new) and old_primary and old_primary != row.primary

        changes.append(PlannedChange(
            name=row.name,
            is_new=is_new,
            methods=methods,
            method_statuses=statuses,
            primary=row.primary,
            old_primary=old_primary,
            primary_changed=primary_changed,
        ))
    return changes


# --------------------------------------------------------------------------- #
# Suspicion check
# --------------------------------------------------------------------------- #
def is_suspicious(changes: list[PlannedChange]) -> bool:
    """True if >40% of existing bloggers have their primary method changed."""
    existing = [c for c in changes if not c.is_new]
    if not existing:
        return False
    changed = [c for c in existing if c.primary_changed]
    return len(changed) / len(existing) > 0.4


# --------------------------------------------------------------------------- #
# Apply changes
# --------------------------------------------------------------------------- #
async def apply_changes(
    changes: list[PlannedChange], manager_id: int, user: dict
) -> tuple[list[str], list[str]]:
    """Returns (added_names, updated_names)."""
    added, updated = [], []
    for c in changes:
        db_b = await get_blogger_by_name(c.name, manager_id)
        if db_b is None:
            db_b = await add_blogger(c.name, manager_id)
            if db_b is None:
                db_b = await get_blogger_by_name(c.name, manager_id)
            if db_b is None:
                continue

        added_methods: dict[str, int] = {}
        for mtype, address in c.methods:
            m = await add_payment_method(db_b["id"], mtype, address)
            added_methods[mtype] = m["id"]

        if c.primary in added_methods:
            await set_primary_method(added_methods[c.primary], db_b["id"])

        action = "added" if c.is_new else "updated"
        log_info("IMPORT_BLOGGER", user_id=user["telegram_id"],
                 username=user["username"], blogger=c.name, result=action)
        await db_log(user["id"], "IMPORT_BLOGGER",
                     f"blogger={c.name} | action={action} | primary={c.primary}")

        if c.is_new:
            added.append(c.name)
        else:
            updated.append(c.name)

    return added, updated


# --------------------------------------------------------------------------- #
# Format result
# --------------------------------------------------------------------------- #
def format_plan_summary(changes: list[PlannedChange], lang: str) -> str:
    lines = []
    _status_ru = {"added": "добавлен", "changed": "изменён", "same": "не изменился"}
    _status_en = {"added": "added",    "changed": "changed",  "same": "unchanged"}

    for c in changes:
        if lang == "ru":
            tag = "новый" if c.is_new else "обновление"
            if c.primary_changed:
                tag += f", основной {c.old_primary} → {c.primary}"
        else:
            tag = "new" if c.is_new else "update"
            if c.primary_changed:
                tag += f", primary {c.old_primary} → {c.primary}"

        lines.append(f"+ {c.name} ({tag})")

        for (mtype, addr), status in zip(c.methods, c.method_statuses):
            label = _METHOD_LABELS.get(mtype, mtype)
            is_primary = (mtype == c.primary)
            status_label = (_status_ru if lang == "ru" else _status_en)[status]
            primary_marker = " [+]" if is_primary else ""
            lines.append(f"  {label}: {addr} ({status_label}){primary_marker}")

    return "\n".join(lines)


def format_final_result(
    added: list[str],
    updated: list[str],
    invalid: list[ParsedRow],
    lang: str,
) -> str:
    parts = []
    if added:
        h = f"Добавлено ({len(added)}):" if lang == "ru" else f"Added ({len(added)}):"
        parts.append(h)
        for name in added:
            parts.append(f"  + {name}")
    if updated:
        h = f"Обновлено ({len(updated)}):" if lang == "ru" else f"Updated ({len(updated)}):"
        parts.append(h)
        for name in updated:
            parts.append(f"  ~ {name}")
    if invalid:
        h = f"Пропущено ({len(invalid)}):" if lang == "ru" else f"Skipped ({len(invalid)}):"
        parts.append(h)
        for pr in invalid:
            parts.append(f"  ✗ {pr.row.name}: {pr.error}")
    if not parts:
        return "Нет данных для импорта." if lang == "ru" else "Nothing to import."
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
_IMPORT_NAV_BUTTONS = {
    "🏠 Home", "💸 Payout", "👥 Bloggers", "⚙️ Settings",
    "🏠 Главная", "💸 Выплата", "👥 Блогеры", "⚙️ Настройки",
}



async def _send_import_instructions(target, lang: str, cancel_kb):
    """Send import instructions to user."""
    if lang == "ru":
        text = (
            "Отправь файл .txt/.tsv или вставь список прямо сюда.\n"
            "Одна строка – один блогер. Бот сам определит тип каждого поля.\n\n"
            "Порядок не важен – просто перечисли через пробел:\n"
            "- имя блогера\n"
            "- Site ID (24 символа, только цифры и a-f)\n"
            "- USDT-TRC20 (начинается с T, 34 символа)\n"
            "- PayPal (адрес с @)\n"
            "- основной метод в конце: site, usdt-trc20 или paypal\n\n"
            "Если метод один – основной определится автоматически.\n\n"
            "Примеры:\n"
            "`Name123 69cd46109be3718872a56f85 site`\n"
            "`blogger456 TDj7hq3Nug4MAh3GXCwLDZkMY4zcqcSyDy usdt-trc20`\n"
            "`example789 email@example.com paypal`\n"
            "`multi123 SiteID USDT_addr email@ex.com usdt-trc20`"
        )
    else:
        text = (
            "Send a .txt/.tsv file or paste the list here.\n"
            "One line = one blogger. The bot detects each field automatically.\n\n"
            "Just list everything separated by spaces:\n"
            "- blogger name\n"
            "- Site ID (24 chars, digits and a-f only)\n"
            "- USDT-TRC20 (starts with T, 34 chars)\n"
            "- PayPal (address with @)\n"
            "- primary method at the end: site, usdt-trc20 or paypal\n\n"
            "Single method – primary is set automatically.\n\n"
            "Examples:\n"
            "`Name123 69cd46109be3718872a56f85 site`\n"
            "`blogger456 TDj7hq3Nug4MAh3GXCwLDZkMY4zcqcSyDy usdt-trc20`\n"
            "`example789 email@example.com paypal`\n"
            "`multi123 SiteID USDT_addr email@ex.com usdt-trc20`"
        )
    await target.reply_text(text, reply_markup=cancel_kb, parse_mode="Markdown")


async def cmd_import_bloggers_from_callback(update, context):
    """Entry point from inline button — sends instructions and activates WAIT_DATA state."""
    query = update.callback_query
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data["ib_user"] = user
    cancel_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✕ Отмена" if lang == "ru" else "✕ Cancel",
                             callback_data="ib_cancel")
    ]])
    await _send_import_instructions(query.message, lang, cancel_kb)
    # Signal that we're in WAIT_DATA state (ConversationHandler picks up next message)
    context.user_data["ib_active"] = True
    return WAIT_DATA


async def cmd_import_bloggers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return ConversationHandler.END
    lang = get_lang(user)
    context.user_data["ib_user"] = user
    cancel_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✕ Отмена" if lang == "ru" else "✕ Cancel",
                             callback_data="ib_cancel")
    ]])
    await _send_import_instructions(update.message, lang, cancel_kb)
    return WAIT_DATA


# --------------------------------------------------------------------------- #
# Parse and plan
# --------------------------------------------------------------------------- #

async def _send_chunked(target, text: str, reply_markup=None):
    """Split text into <=4000-char chunks and send as separate messages.
    The inline keyboard is attached to the last chunk only."""
    MAX = 4000
    if len(text) <= MAX:
        await target.reply_text(text, reply_markup=reply_markup)
        return
    # Split by lines, group into chunks
    lines = text.splitlines(keepends=True)
    chunks = []
    current = ""
    for line in lines:
        if len(current) + len(line) > MAX:
            if current:
                chunks.append(current.rstrip())
            current = line
        else:
            current += line
    if current.strip():
        chunks.append(current.rstrip())
    for i, chunk in enumerate(chunks):
        kb = reply_markup if i == len(chunks) - 1 else None
        await target.reply_text(chunk, reply_markup=kb)


async def _handle_parsed_text(text: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data.get("ib_user") or await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    valid, invalid = parse_import_text(text, lang)

    if not valid and not invalid:
        await update.effective_message.reply_text(
            "Не найдено строк с данными." if lang == "ru" else "No data rows found."
        )
        return WAIT_DATA

    if not valid:
        # Only errors — show and stay
        lines = [("Все строки содержат ошибки:" if lang == "ru" else "All rows have errors:")]
        for pr in invalid:
            lines.append(f"  ✗ {pr.row.name}: {pr.error}")
        cancel_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✕ Отмена" if lang == "ru" else "✕ Cancel",
                                 callback_data="ib_cancel")
        ]])
        from handlers.common import nav_keyboard as _nav_kb
        _user = context.user_data.get("ib_user")
        _lang = get_lang(_user) if _user else "en"
        await update.effective_message.reply_text("\n".join(lines), reply_markup=_nav_kb(_lang))
        return ConversationHandler.END

    # Ask import mode first
    context.user_data["ib_valid"]   = [pr.row.__dict__ for pr in valid]
    context.user_data["ib_invalid"] = invalid
    context.user_data["ib_user"]    = user

    new_count = 0
    for pr in valid:
        existing = await get_blogger_by_name(pr.row.name, user["id"])
        if not existing:
            new_count += 1
    existing_count = len(valid) - new_count

    if lang == "ru":
        text = (
            f"Найдено строк: {len(valid)} (+{new_count} новых, {existing_count} уже в базе)\n\n"
            f"Выбери режим импорта:"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"➕ Только новые ({new_count})",
                callback_data="ib_mode:new_only"
            )],
            [InlineKeyboardButton(
                f"🔄 Полный импорт ({len(valid)})",
                callback_data="ib_mode:full"
            )],
            [InlineKeyboardButton("✕ Отмена", callback_data="ib_cancel")],
        ])
    else:
        text = (
            f"Found rows: {len(valid)} (+{new_count} new, {existing_count} already in DB)\n\n"
            f"Select import mode:"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                f"➕ New only ({new_count})",
                callback_data="ib_mode:new_only"
            )],
            [InlineKeyboardButton(
                f"🔄 Full import ({len(valid)})",
                callback_data="ib_mode:full"
            )],
            [InlineKeyboardButton("✕ Cancel", callback_data="ib_cancel")],
        ])
    await update.effective_message.reply_text(text, reply_markup=kb)
    return WAIT_CONFIRM

    suspicious = is_suspicious(changes)
    plan_text  = format_plan_summary(changes, lang)

    # Show invalid rows if any
    skip_text = ""
    if invalid:
        skip_lines = [("\nПропускаются:" if lang == "ru" else "\nSkipped:")]
        for pr in invalid:
            skip_lines.append(f"  ✗ {pr.row.name}: {pr.error}")
        skip_text = "\n".join(skip_lines)

    if suspicious:
        # Count how many primary methods change
        existing    = [c for c in changes if not c.is_new]
        changed_cnt = sum(1 for c in existing if c.primary_changed)
        pct         = int(changed_cnt / len(existing) * 100) if existing else 0

        if lang == "ru":
            warn = (
                f"⚠️ У {changed_cnt} из {len(existing)} уже существующих блогеров "
                f"основной метод оплаты изменится ({pct}%). "
                f"Это выше нормы – скорее всего, что-то не так с данными. "
                f"Перепроверь перед тем как продолжить.\n\n"
            )
            confirm_text = warn + plan_text + skip_text
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✓ Всё верно, применить", callback_data="ib_confirm")],
                [InlineKeyboardButton("✗ Отмена",               callback_data="ib_cancel")],
            ])
        else:
            warn = (
                f"⚠️ {changed_cnt} out of {len(existing)} existing bloggers "
                f"would have their primary payment method changed ({pct}%). "
                f"This is unusually high – likely something is wrong with the data. "
                f"Please double-check before continuing.\n\n"
            )
            confirm_text = warn + plan_text + skip_text
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✓ Looks correct, apply", callback_data="ib_confirm")],
                [InlineKeyboardButton("✗ Cancel",               callback_data="ib_cancel")],
            ])
        await _send_chunked(update.effective_message, confirm_text, kb)
        return WAIT_CONFIRM

    # No suspicion — still show plan and ask for confirmation
    if lang == "ru":
        header = f"Готово к импорту ({len(changes)} блогеров):\n\n"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✓ Применить", callback_data="ib_confirm")],
            [InlineKeyboardButton("✗ Отмена",    callback_data="ib_cancel")],
        ])
    else:
        header = f"Ready to import ({len(changes)} bloggers):\n\n"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✓ Apply",  callback_data="ib_confirm")],
            [InlineKeyboardButton("✗ Cancel", callback_data="ib_cancel")],
        ])
    await _send_chunked(update.effective_message, header + plan_text + skip_text, kb)
    return WAIT_CONFIRM


async def import_got_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if text in _IMPORT_NAV_BUTTONS or text.startswith("🏠") or text.startswith("💸"):
        # Nav button pressed — cancel import silently, let fallback handle it
        return ConversationHandler.END
    return await _handle_parsed_text(text, update, context)


async def import_got_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data.get("ib_user") or await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    doc  = update.message.document

    fname = doc.file_name or ""
    if not (fname.endswith(".txt") or fname.endswith(".tsv")):
        await update.message.reply_text(
            "Принимаются только файлы .txt и .tsv"
            if lang == "ru" else
            "Only .txt and .tsv files are accepted"
        )
        return WAIT_DATA

    if doc.file_size and doc.file_size > 512 * 1024:
        await update.message.reply_text(
            "Файл слишком большой – максимум 512 КБ"
            if lang == "ru" else
            "File too large – max 512 KB"
        )
        return WAIT_DATA

    tg_file = await doc.get_file()
    buf = io.BytesIO()
    await tg_file.download_to_memory(buf)
    try:
        text = buf.getvalue().decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = buf.getvalue().decode("cp1251")
        except Exception:
            await update.message.reply_text(
                "Не удалось прочитать файл. Используй кодировку UTF-8."
                if lang == "ru" else
                "Could not read file. Use UTF-8 encoding."
            )
            return WAIT_DATA

    return await _handle_parsed_text(text, update, context)


# --------------------------------------------------------------------------- #
# Confirmation callbacks
# --------------------------------------------------------------------------- #
async def cb_ib_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle import mode selection: new_only or full."""
    query = update.callback_query
    await query.answer()
    user = context.user_data.get("ib_user") or await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    mode = query.data.split(":")[1]  # "new_only" or "full"
    raw_valid = context.user_data.get("ib_valid", [])
    invalid   = context.user_data.get("ib_invalid", [])

    # Reconstruct ImportRow objects
    valid_rows = []
    for d in raw_valid:
        row = ImportRow(**d)
        if mode == "new_only":
            existing = await get_blogger_by_name(row.name, user["id"])
            if existing:
                continue  # skip existing bloggers silently
        valid_rows.append(ParsedRow(row))

    if not valid_rows:
        await query.edit_message_text(
            "Нет новых блогеров для добавления." if lang == "ru"
            else "No new bloggers to add."
        )
        return ConversationHandler.END

    # Build plan
    changes = await build_plan(valid_rows, user["id"])
    context.user_data["ib_changes"] = changes

    suspicious = is_suspicious(changes) and mode == "full"
    plan_text  = format_plan_summary(changes, lang)

    skip_text = ""
    if invalid:
        skip_lines = ["\nПропускаются:" if lang == "ru" else "\nSkipped:"]
        for pr in invalid:
            skip_lines.append(f"  ✗ {pr.row.name}: {pr.error}")
        skip_text = "\n".join(skip_lines)

    if suspicious:
        existing    = [c for c in changes if not c.is_new]
        changed_cnt = sum(1 for c in existing if c.primary_changed)
        pct         = int(changed_cnt / len(existing) * 100) if existing else 0
        if lang == "ru":
            warn = (
                f"⚠️ У {changed_cnt} из {len(existing)} уже существующих блогеров "
                f"основной метод оплаты изменится ({pct}%). "
                f"Это выше нормы – перепроверь перед тем как продолжить.\n\n"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✓ Всё верно, применить", callback_data="ib_confirm")],
                [InlineKeyboardButton("✗ Отмена", callback_data="ib_cancel")],
            ])
        else:
            warn = (
                f"⚠️ {changed_cnt} out of {len(existing)} existing bloggers "
                f"would have their primary method changed ({pct}%). "
                f"Please double-check before continuing.\n\n"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✓ Looks correct, apply", callback_data="ib_confirm")],
                [InlineKeyboardButton("✗ Cancel", callback_data="ib_cancel")],
            ])
        await _send_chunked(query.message, warn + plan_text + skip_text, kb)
        return WAIT_CONFIRM

    if lang == "ru":
        mode_label = "только новые" if mode == "new_only" else "полный"
        header = f"Готово к импорту ({mode_label}, {len(changes)} блогеров):\n\n"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✓ Применить", callback_data="ib_confirm")],
            [InlineKeyboardButton("✗ Отмена",    callback_data="ib_cancel")],
        ])
    else:
        mode_label = "new only" if mode == "new_only" else "full"
        header = f"Ready to import ({mode_label}, {len(changes)} bloggers):\n\n"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✓ Apply",  callback_data="ib_confirm")],
            [InlineKeyboardButton("✗ Cancel", callback_data="ib_cancel")],
        ])
    await _send_chunked(query.message, header + plan_text + skip_text, kb)
    return WAIT_CONFIRM


async def cb_ib_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["_last_action"] = "import_confirm"
    user    = context.user_data.get("ib_user") or await get_user(update.effective_user.id)
    lang    = get_lang(user) if user else "en"
    changes = context.user_data.get("ib_changes", [])
    invalid = context.user_data.get("ib_invalid", [])

    if not changes:
        await query.edit_message_text("Нет данных." if lang == "ru" else "No data.")
        return ConversationHandler.END

    await query.edit_message_text(
        "Применяю..." if lang == "ru" else "Applying..."
    )

    added, updated = await apply_changes(changes, user["id"], user)

    summary = format_final_result(added, updated, invalid, lang)
    await _send_chunked(query.message, summary, nav_keyboard(lang))
    context.user_data.pop("ib_changes", None)
    context.user_data.pop("ib_invalid", None)
    context.user_data.pop("ib_valid", None)
    return ConversationHandler.END


async def cb_ib_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data.pop("ib_changes", None)
    context.user_data.pop("ib_invalid", None)
    context.user_data.pop("ib_user", None)
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("Отменено." if lang == "ru" else "Cancelled.")
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /cancel command
# --------------------------------------------------------------------------- #
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data.pop("ib_changes", None)
    context.user_data.pop("ib_invalid", None)
    context.user_data.pop("ib_user", None)
    await update.message.reply_text(
        "Импорт отменён." if lang == "ru" else "Import cancelled."
    )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_import_handlers(app):
    # Register globally so callbacks work from any message in the chain
    app.add_handler(CallbackQueryHandler(cb_ib_confirm, pattern=r"^ib_confirm$"))
    app.add_handler(CallbackQueryHandler(cb_ib_cancel,  pattern=r"^ib_cancel$"))
    app.add_handler(CallbackQueryHandler(cb_ib_mode,    pattern=r"^ib_mode:"))
    app.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("import_bloggers", cmd_import_bloggers),
            CallbackQueryHandler(cmd_import_bloggers_from_callback, pattern=r"^go_import$"),
        ],
        states={
            WAIT_DATA: [
                MessageHandler(
                    filters.Document.MimeType("text/plain") |
                    filters.Document.FileExtension("tsv") |
                    filters.Document.FileExtension("txt"),
                    import_got_file,
                ),
                CallbackQueryHandler(cb_ib_cancel,  pattern=r"^ib_cancel$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, import_got_text),
            ],
            WAIT_CONFIRM: [
                CallbackQueryHandler(cb_ib_confirm, pattern=r"^ib_confirm$"),
                CallbackQueryHandler(cb_ib_cancel,  pattern=r"^ib_cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        conversation_timeout=600,
        per_message=False,
    ))