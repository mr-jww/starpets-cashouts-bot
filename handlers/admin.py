"""
Admin-only handlers:
  /admin   — overview: all users, bloggers, recent payouts, logs
  /backup  — create backup and send as file
  /restore — receive .db file and replace database
"""

from __future__ import annotations
import os
import shutil
from datetime import datetime
from telegram import Update, Document
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from database.queries import (
    get_all_users, get_all_bloggers, get_all_recent_payouts,
    get_recent_logs, search_bloggers_global, db_log,
    get_all_methods, METHOD_LABELS,
)
from services.logger import log_info, log_system
from handlers.common import admin_only, get_lang, get_user_or_reject
from database.queries import get_user
from config import DB_PATH, BACKUP_DIR, BACKUP_KEEP


# --------------------------------------------------------------------------- #
# Backup helpers
# --------------------------------------------------------------------------- #
def _backup_path() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(BACKUP_DIR, f"starpets_backup_{ts}.db")


def _create_backup() -> str:
    """Copy DB file to backups dir. Returns path to new backup."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = _backup_path()
    shutil.copy2(DB_PATH, dest)
    _cleanup_old_backups()
    log_system("BACKUP_CREATED", file=os.path.basename(dest))
    return dest


def _cleanup_old_backups():
    """Keep only the last BACKUP_KEEP backups."""
    files = sorted([
        os.path.join(BACKUP_DIR, f)
        for f in os.listdir(BACKUP_DIR)
        if f.startswith("starpets_backup_") and f.endswith(".db")
    ])
    while len(files) > BACKUP_KEEP:
        os.remove(files.pop(0))


# --------------------------------------------------------------------------- #
# /admin
# --------------------------------------------------------------------------- #
@admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user)

    users    = await get_all_users()
    bloggers = await get_all_bloggers()
    payouts  = await get_all_recent_payouts(limit=5)

    managers = [u for u in users if u["role"] == "manager"]

    lines = []

    # Users
    lines.append("=== ПОЛЬЗОВАТЕЛИ ===" if lang == "ru" else "=== USERS ===")
    for u in users:
        role_label = "admin" if u["role"] == "admin" else "manager"
        lines.append(f"• @{u['username'] or '?'} (id={u['telegram_id']}) [{role_label}]")

    # Bloggers per manager
    lines.append("")
    lines.append("=== БЛОГЕРЫ ===" if lang == "ru" else "=== BLOGGERS ===")
    for u in managers:
        mgr_bloggers = [b for b in bloggers if b["manager_id"] == u["id"]]
        lines.append(f"@{u['username'] or '?'}: {len(mgr_bloggers)} блогеров" if lang == "ru"
                     else f"@{u['username'] or '?'}: {len(mgr_bloggers)} bloggers")
        for b in mgr_bloggers:
            lines.append(f"  • {b['name']}")

    # Recent payouts
    lines.append("")
    lines.append("=== ПОСЛЕДНИЕ ВЫПЛАТЫ ===" if lang == "ru" else "=== RECENT PAYOUTS ===")
    if payouts:
        for p in payouts:
            lines.append(
                f"• {p['blogger_name']} | {p['amount_raw']} | "
                f"@{p['manager_username'] or '?'} | {p['created_at'][:16]}"
            )
    else:
        lines.append("Нет выплат." if lang == "ru" else "No payouts.")

    # Commands hint
    lines.append("")
    lines.append(
        "Команды: /backup | /restore | /admin_logs | /admin_search <имя>"
        if lang == "ru" else
        "Commands: /backup | /restore | /admin_logs | /admin_search <name>"
    )

    await update.message.reply_text("\n".join(lines))


# --------------------------------------------------------------------------- #
# /admin_logs
# --------------------------------------------------------------------------- #
@admin_only
async def cmd_admin_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user)

    logs = await get_recent_logs(limit=30)
    if not logs:
        await update.message.reply_text("Логов нет." if lang == "ru" else "No logs.")
        return

    lines = ["=== LOGS (last 30) ==="]
    for l in logs:
        uname = f"@{l['username']}" if l.get("username") else "system"
        lines.append(f"[{l['created_at'][:16]}] [{l['level']}] {uname} — {l['action']}"
                     + (f" | {l['details']}" if l.get("details") else ""))

    text = "\n".join(lines)
    # Split if too long for one message
    if len(text) > 4000:
        text = text[:4000] + "\n... (truncated)"

    await update.message.reply_text(f"```\n{text}\n```", parse_mode="Markdown")


# --------------------------------------------------------------------------- #
# /admin_search
# --------------------------------------------------------------------------- #
@admin_only
async def cmd_admin_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user)

    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await update.message.reply_text(
            "Использование: /admin_search <имя блогера>"
            if lang == "ru" else
            "Usage: /admin_search <blogger name>"
        )
        return

    results = await search_bloggers_global(query)
    if not results:
        await update.message.reply_text(
            f"Блогеры по запросу «{query}» не найдены."
            if lang == "ru" else
            f"No bloggers found for '{query}'."
        )
        return

    lines = [f"Результаты поиска «{query}»:" if lang == "ru" else f"Search results for '{query}':"]
    for b in results:
        methods = await get_all_methods(b["id"])
        method_strs = []
        for m in methods:
            status = "" if m["is_active"] else " [откл]" if lang == "ru" else " [off]"
            label = METHOD_LABELS.get(m["type"], m["type"])
            method_strs.append(f"    {label}: {m['address']}{status}")
        mgr = f"@{b['manager_username'] or '?'}"
        lines.append(f"• {b['name']} (менеджер: {mgr})" if lang == "ru"
                     else f"• {b['name']} (manager: {mgr})")
        lines.extend(method_strs)

    await update.message.reply_text("\n".join(lines))


# --------------------------------------------------------------------------- #
# /backup
# --------------------------------------------------------------------------- #
@admin_only
async def cmd_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user)

    if not os.path.exists(DB_PATH):
        await update.message.reply_text(
            "База данных не найдена." if lang == "ru" else "Database not found."
        )
        return

    await update.message.reply_text(
        "Создаю бэкап..." if lang == "ru" else "Creating backup..."
    )

    backup_file = _create_backup()
    size_kb = os.path.getsize(backup_file) // 1024

    await db_log(user["id"], "BACKUP_MANUAL", f"file={os.path.basename(backup_file)} | size={size_kb}kb")
    log_info("BACKUP_MANUAL", user_id=user["telegram_id"], username=user["username"],
             file=os.path.basename(backup_file), size=f"{size_kb}kb")

    with open(backup_file, "rb") as f:
        await update.message.reply_document(
            document=f,
            filename=os.path.basename(backup_file),
            caption=f"Бэкап базы данных | {size_kb} KB" if lang == "ru"
                    else f"Database backup | {size_kb} KB",
        )


# --------------------------------------------------------------------------- #
# /restore
# --------------------------------------------------------------------------- #
@admin_only
async def cmd_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user)

    await update.message.reply_text(
        "Отправьте файл .db для восстановления.\n"
        "Текущая база будет сохранена в backups перед заменой.\n"
        "/cancel — отмена"
        if lang == "ru" else
        "Send a .db file to restore.\n"
        "Current database will be backed up before replacement.\n"
        "/cancel — cancel"
    )
    context.user_data["awaiting_restore"] = True


async def handle_restore_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_restore"):
        return

    user = await get_user(update.effective_user.id)
    if not user or user["telegram_id"] != update.effective_user.id:
        return
    from config import ADMIN_ID
    if update.effective_user.id != ADMIN_ID:
        return

    lang = get_lang(user)
    doc: Document = update.message.document

    if not doc.file_name.endswith(".db"):
        await update.message.reply_text(
            "Файл должен быть .db" if lang == "ru" else "File must be .db"
        )
        return

    # Backup current DB first
    if os.path.exists(DB_PATH):
        _create_backup()

    # Download and replace
    file = await doc.get_file()
    await file.download_to_drive(DB_PATH)

    context.user_data.pop("awaiting_restore", None)

    await db_log(user["id"], "DB_RESTORED", f"from={doc.file_name}")
    log_info("DB_RESTORED", user_id=user["telegram_id"], username=user["username"], file=doc.file_name)

    await update.message.reply_text(
        "База данных восстановлена." if lang == "ru" else "Database restored."
    )


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_admin_handlers(app):
    app.add_handler(CommandHandler("admin",        cmd_admin))
    app.add_handler(CommandHandler("admin_logs",   cmd_admin_logs))
    app.add_handler(CommandHandler("admin_search", cmd_admin_search))
    app.add_handler(CommandHandler("backup",       cmd_backup))
    app.add_handler(CommandHandler("restore",      cmd_restore))
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("db"),
        handle_restore_file,
    ))