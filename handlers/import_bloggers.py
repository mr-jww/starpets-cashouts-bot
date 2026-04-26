"""
/import-bloggers — bulk import bloggers with payment methods.

Accepted input: text message or .txt/.tsv file.

Format (tab-separated, one blogger per line):
  name  \t  Site_ID  \t  USDT-TRC20  \t  PayPal_email  \t  primary_method

Columns 2-4 may be empty (use empty string or just omit with tab).
At least one of columns 2-4 must be non-empty.
Column 5 (primary) must match a non-empty column: site, usdt-trc20, paypal.

Examples:
  braba7x.ff1\t690779e7e54ed806f3d730b4\t\t\tsite
  taypk7\t\tTLBwE3pdG9UYedsZHUENewCYLdy7KKhGi3\t\tusdt-trc20
  blogger3\t\t\tuser@gmail.com\tpaypal
  blogger4\t690779...\tTLBwE3...\t\tsite
"""

from __future__ import annotations
import io
from dataclasses import dataclass, field
from telegram import Update, Document
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler,
    ConversationHandler, filters,
)

from database.queries import (
    get_user, get_blogger_by_name, add_blogger,
    add_payment_method, set_primary_method, get_active_methods,
    db_log,
)
from services.logger import log_info
from handlers.common import get_user_or_reject, get_lang, nav_keyboard

WAIT_DATA = 0

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
    name:       str
    site:       str = ""
    usdt:       str = ""
    paypal:     str = ""
    primary:    str = ""


@dataclass
class ImportResult:
    added:    list[str] = field(default_factory=list)
    updated:  list[str] = field(default_factory=list)
    skipped:  list[tuple[str, str]] = field(default_factory=list)  # (name, reason)
    errors:   list[tuple[str, str]] = field(default_factory=list)  # (line/name, reason)


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def parse_import_text(text: str, lang: str) -> tuple[list[ImportRow], list[tuple[str, str]]]:
    """
    Returns (valid_rows, parse_errors).
    parse_errors: list of (line_identifier, reason).
    """
    rows: list[ImportRow] = []
    errors: list[tuple[str, str]] = []

    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue

        parts = line.split("\t")
        parts = [p.strip() for p in parts]

        # Pad to 5 columns
        while len(parts) < 5:
            parts.append("")

        name    = parts[0]
        site    = parts[1]
        usdt    = parts[2]
        paypal  = parts[3]
        primary_raw = parts[4].lower().strip()

        # Validate name
        if not name:
            errors.append((
                f"Строка {line_no}" if lang == "ru" else f"Line {line_no}",
                "отсутствует имя блогера" if lang == "ru" else "blogger name missing"
            ))
            continue

        # At least one method must be present
        if not site and not usdt and not paypal:
            errors.append((
                name,
                "не указан ни один метод оплаты" if lang == "ru"
                else "no payment method provided"
            ))
            continue

        # Validate primary
        primary = _PRIMARY_ALIASES.get(primary_raw, "")
        if not primary:
            errors.append((
                name,
                f"неверный основной метод: «{primary_raw}»" if lang == "ru"
                else f"invalid primary method: '{primary_raw}'"
            ))
            continue

        # Primary must match a non-empty field
        method_map = {"site": site, "usdt-trc20": usdt, "paypal": paypal}
        if not method_map.get(primary):
            errors.append((
                name,
                f"основной метод «{primary_raw}» не имеет адреса" if lang == "ru"
                else f"primary method '{primary_raw}' has no address"
            ))
            continue

        rows.append(ImportRow(name=name, site=site, usdt=usdt, paypal=paypal, primary=primary))

    return rows, errors


# --------------------------------------------------------------------------- #
# Processor
# --------------------------------------------------------------------------- #
async def process_import(rows: list[ImportRow], user: dict, lang: str) -> ImportResult:
    result = ImportResult()

    for row in rows:
        # Get or create blogger
        db_b = await get_blogger_by_name(row.name, user["id"])
        is_new = db_b is None
        if is_new:
            db_b = await add_blogger(row.name, user["id"])
            if db_b is None:
                # Race condition – try fetching again
                db_b = await get_blogger_by_name(row.name, user["id"])
            if db_b is None:
                result.errors.append((row.name, "не удалось создать" if lang == "ru" else "failed to create"))
                continue

        # Add methods
        added_methods = {}
        method_specs = [
            ("site",       row.site),
            ("usdt-trc20", row.usdt),
            ("paypal",     row.paypal),
        ]
        for mtype, address in method_specs:
            if not address:
                continue
            m = await add_payment_method(db_b["id"], mtype, address)
            added_methods[mtype] = m["id"]

        # Set primary
        primary_id = added_methods.get(row.primary)
        if primary_id:
            await set_primary_method(primary_id, db_b["id"])

        if is_new:
            result.added.append(row.name)
        else:
            result.updated.append(row.name)

        log_info(
            "IMPORT_BLOGGER",
            user_id=user["telegram_id"],
            username=user["username"],
            blogger=row.name,
            action="added" if is_new else "updated",
            methods=",".join(added_methods.keys()),
            primary=row.primary,
        )
        await db_log(
            user["id"], "IMPORT_BLOGGER",
            f"blogger={row.name} | action={'added' if is_new else 'updated'} | primary={row.primary}"
        )

    return result


# --------------------------------------------------------------------------- #
# Format result message
# --------------------------------------------------------------------------- #
def format_result(
    result: ImportResult,
    parse_errors: list[tuple[str, str]],
    lang: str,
) -> str:
    lines = []

    if result.added:
        header = f"Добавлено ({len(result.added)}):" if lang == "ru" else f"Added ({len(result.added)}):"
        lines.append(header)
        for name in result.added:
            lines.append(f"  + {name}")

    if result.updated:
        header = f"Обновлено ({len(result.updated)}):" if lang == "ru" else f"Updated ({len(result.updated)}):"
        lines.append(header)
        for name in result.updated:
            lines.append(f"  ~ {name}")

    all_errors = parse_errors + result.errors
    if all_errors:
        header = f"Ошибки ({len(all_errors)}):" if lang == "ru" else f"Errors ({len(all_errors)}):"
        lines.append(header)
        for name, reason in all_errors:
            lines.append(f"  ✗ {name}: {reason}")

    if not lines:
        return "Нет данных для импорта." if lang == "ru" else "No data to import."

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# /import-bloggers entry
# --------------------------------------------------------------------------- #
async def cmd_import_bloggers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return ConversationHandler.END
    lang = get_lang(user)
    context.user_data["ib_user"] = user

    if lang == "ru":
        text = (
            "Вставьте список блогеров или отправьте файл .txt/.tsv\n\n"
            "Формат (tab-разделитель, одна строка = один блогер):\n"
            "имя  →  Site ID  →  USDT-TRC20  →  PayPal  →  основной\n\n"
            "Примеры:\n"
            "braba7x.ff1\t690779e7e54ed806f3d730b4\t\t\tsite\n"
            "taypk7\t\tTLBwE3pdG9UYeds...\t\tusdt-trc20\n"
            "blogger3\t\t\tuser@gmail.com\tpaypal\n\n"
            "Пустые ячейки пропускаются. Хотя бы один метод обязателен.\n"
            "/cancel — отмена"
        )
    else:
        text = (
            "Paste the blogger list or send a .txt/.tsv file\n\n"
            "Format (tab-separated, one line = one blogger):\n"
            "name  →  Site ID  →  USDT-TRC20  →  PayPal  →  primary\n\n"
            "Examples:\n"
            "braba7x.ff1\t690779e7e54ed806f3d730b4\t\t\tsite\n"
            "taypk7\t\tTLBwE3pdG9UYeds...\t\tusdt-trc20\n"
            "blogger3\t\t\tuser@gmail.com\tpaypal\n\n"
            "Empty cells are skipped. At least one method required.\n"
            "/cancel — cancel"
        )
    await update.message.reply_text(text)
    return WAIT_DATA


# --------------------------------------------------------------------------- #
# Got text
# --------------------------------------------------------------------------- #
async def import_got_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data.get("ib_user") or await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    text = update.message.text.strip()
    rows, parse_errors = parse_import_text(text, lang)

    if not rows and not parse_errors:
        await update.message.reply_text(
            "Не найдено строк." if lang == "ru" else "No rows found."
        )
        return WAIT_DATA

    result = await process_import(rows, user, lang)
    summary = format_result(result, parse_errors, lang)
    await update.message.reply_text(summary, reply_markup=nav_keyboard(lang))
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Got file
# --------------------------------------------------------------------------- #
async def import_got_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data.get("ib_user") or await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    doc: Document = update.message.document

    # Validate file type
    fname = doc.file_name or ""
    if not (fname.endswith(".txt") or fname.endswith(".tsv")):
        await update.message.reply_text(
            "Поддерживаются только файлы .txt и .tsv"
            if lang == "ru" else
            "Only .txt and .tsv files are supported"
        )
        return WAIT_DATA

    # Size limit: 512 KB
    if doc.file_size and doc.file_size > 512 * 1024:
        await update.message.reply_text(
            "Файл слишком большой (максимум 512 КБ)"
            if lang == "ru" else
            "File too large (max 512 KB)"
        )
        return WAIT_DATA

    file = await doc.get_file()
    buf = io.BytesIO()
    await file.download_to_memory(buf)
    try:
        text = buf.getvalue().decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = buf.getvalue().decode("cp1251")
        except Exception:
            await update.message.reply_text(
                "Не удалось прочитать файл. Используйте кодировку UTF-8."
                if lang == "ru" else
                "Could not read file. Use UTF-8 encoding."
            )
            return WAIT_DATA

    rows, parse_errors = parse_import_text(text, lang)

    if not rows and not parse_errors:
        await update.message.reply_text(
            "Файл пустой или не содержит данных."
            if lang == "ru" else
            "File is empty or contains no data."
        )
        return WAIT_DATA

    result = await process_import(rows, user, lang)
    summary = format_result(result, parse_errors, lang)
    await update.message.reply_text(summary, reply_markup=nav_keyboard(lang))
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /cancel
# --------------------------------------------------------------------------- #
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data.clear()
    await update.message.reply_text(
        "Отменено." if lang == "ru" else "Cancelled."
    )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_import_handlers(app):
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("import_bloggers", cmd_import_bloggers)],
        states={
            WAIT_DATA: [
                MessageHandler(
                    filters.Document.MimeType("text/plain") |
                    filters.Document.FileExtension("tsv") |
                    filters.Document.FileExtension("txt"),
                    import_got_file,
                ),
                MessageHandler(filters.TEXT & ~filters.COMMAND, import_got_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        conversation_timeout=300,
    ))