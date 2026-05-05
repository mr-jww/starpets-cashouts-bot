"""
Admin panel — inline menu architecture, same pattern as blogger.py.

Entry: /admin
Screens:
  overview → users / bloggers / payouts / logs
  logs     → last N lines, download log file, download DB
  search   → global blogger search
  backup   → create + download
  restore  → upload .db file
"""

from __future__ import annotations
import io
import os
import shutil
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Document
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters,
)

from database.queries import (
    get_all_users, get_all_bloggers, get_all_recent_payouts,
    get_recent_logs, search_bloggers_global, db_log,
    get_all_methods, METHOD_LABELS, get_user,
)
from services.logger import log_info, log_system
from handlers.common import admin_only, get_lang
from config import DB_PATH, BACKUP_DIR, BACKUP_KEEP, ADMIN_ID


# --------------------------------------------------------------------------- #
# Backup helpers
# --------------------------------------------------------------------------- #
def _backup_path() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(BACKUP_DIR, f"starpets_backup_{ts}.db")


def _create_backup() -> str:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = _backup_path()
    shutil.copy2(DB_PATH, dest)
    _cleanup_old_backups()
    log_system("BACKUP_CREATED", file=os.path.basename(dest))
    return dest


def _cleanup_old_backups():
    files = sorted([
        os.path.join(BACKUP_DIR, f)
        for f in os.listdir(BACKUP_DIR)
        if f.startswith("starpets_backup_") and f.endswith(".db")
    ])
    while len(files) > BACKUP_KEEP:
        os.remove(files.pop(0))


def _log_file_path() -> str | None:
    """Current month log file path."""
    from config import LOG_DIR
    path = os.path.join(LOG_DIR, f"bot_{datetime.now().strftime('%Y%m')}.log")
    return path if os.path.exists(path) else None


# --------------------------------------------------------------------------- #
# Screen helpers
# --------------------------------------------------------------------------- #
def _admin_main_kb(lang: str) -> InlineKeyboardMarkup:
    if lang == "ru":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Пользователи",    callback_data="adm:users")],
            [InlineKeyboardButton("📊 Выплаты",         callback_data="adm:payouts")],
            [InlineKeyboardButton("📋 Логи",            callback_data="adm:logs:30")],
            [InlineKeyboardButton("🔍 Поиск блогера",   callback_data="adm:search_prompt")],
            [
                InlineKeyboardButton("💾 Бэкап",        callback_data="adm:backup"),
                InlineKeyboardButton("📥 Скачать БД",   callback_data="adm:dl_db"),
            ],
            [InlineKeyboardButton("♻️ Восстановить БД", callback_data="adm:restore_prompt")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users",               callback_data="adm:users")],
        [InlineKeyboardButton("📊 Payouts",             callback_data="adm:payouts")],
        [InlineKeyboardButton("📋 Logs",                callback_data="adm:logs:30")],
        [InlineKeyboardButton("🔍 Search blogger",      callback_data="adm:search_prompt")],
        [
            InlineKeyboardButton("💾 Backup",           callback_data="adm:backup"),
            InlineKeyboardButton("📥 Download DB",      callback_data="adm:dl_db"),
        ],
        [InlineKeyboardButton("♻️ Restore DB",          callback_data="adm:restore_prompt")],
    ])


def _back_kb(lang: str) -> InlineKeyboardMarkup:
    label = "← Назад" if lang == "ru" else "← Back"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="adm:main")]])


def _logs_kb(lang: str, current: int) -> InlineKeyboardMarkup:
    counts = [30, 100, 300]
    buttons = [
        InlineKeyboardButton(
            f"{'→ ' if n == current else ''}{n}",
            callback_data=f"adm:logs:{n}"
        )
        for n in counts
    ]
    dl_label = "📥 Скачать лог" if lang == "ru" else "📥 Download log"
    return InlineKeyboardMarkup([
        buttons,
        [InlineKeyboardButton(dl_label, callback_data="adm:dl_log")],
        [InlineKeyboardButton("← Назад" if lang == "ru" else "← Back", callback_data="adm:main")],
    ])


# --------------------------------------------------------------------------- #
# /admin entry
# --------------------------------------------------------------------------- #
@admin_only
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user)
    log_info("ADMIN_VIEW", user_id=user["telegram_id"], username=user["username"])

    users   = await get_all_users()
    bloggers = await get_all_bloggers()
    text = (
        f"Панель администратора\n\n"
        f"Пользователей: {len(users)}\n"
        f"Блогеров в базе: {len(bloggers)}"
        if lang == "ru" else
        f"Admin panel\n\n"
        f"Users: {len(users)}\n"
        f"Bloggers in DB: {len(bloggers)}"
    )
    await update.message.reply_text(text, reply_markup=_admin_main_kb(lang))


# --------------------------------------------------------------------------- #
# Callback router: adm:<action>[:<arg>]
# --------------------------------------------------------------------------- #
async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    if not user or user["telegram_id"] != ADMIN_ID:
        await query.answer("Access denied.", show_alert=True)
        return
    lang = get_lang(user)

    parts  = query.data.split(":", 2)
    action = parts[1]
    arg    = parts[2] if len(parts) > 2 else ""

    # ---- MAIN ----
    if action == "main":
        users    = await get_all_users()
        bloggers = await get_all_bloggers()
        text = (
            f"Панель администратора\n\nПользователей: {len(users)}\nБлогеров: {len(bloggers)}"
            if lang == "ru" else
            f"Admin panel\n\nUsers: {len(users)}\nBloggers: {len(bloggers)}"
        )
        await query.edit_message_text(text, reply_markup=_admin_main_kb(lang))

    # ---- USERS ----
    elif action == "users":
        users = await get_all_users()
        lines = ["👥 Пользователи:" if lang == "ru" else "👥 Users:"]
        for u in users:
            role = "admin" if u["role"] == "admin" else "manager"
            mgr  = f" [{u['manager_filter']}]" if u.get("manager_filter") else ""
            reg  = u.get("created_at", "")[:10]
            bloggers = await get_all_bloggers()
            count = len([b for b in bloggers if b["manager_id"] == u["id"]])
            lines.append(
                f"\n@{u['username'] or '?'} · {role}{mgr}\n"
                f"  id={u['telegram_id']} · {count} блогеров · с {reg}"
                if lang == "ru" else
                f"\n@{u['username'] or '?'} · {role}{mgr}\n"
                f"  id={u['telegram_id']} · {count} bloggers · since {reg}"
            )
        text = "\n".join(lines)
        if len(text) > 3800:
            text = text[:3800] + "\n..."
        await query.edit_message_text(text, reply_markup=_back_kb(lang))

    # ---- PAYOUTS ----
    elif action == "payouts":
        payouts = await get_all_recent_payouts(limit=20)
        if not payouts:
            text = "Выплат нет." if lang == "ru" else "No payouts."
        else:
            lines = ["📊 Последние 20 выплат:" if lang == "ru" else "📊 Last 20 payouts:"]
            for p in payouts:
                lines.append(
                    f"\n{p['created_at'][:16]}  @{p['manager_username'] or '?'}\n"
                    f"  {p['blogger_name']} · {p['amount_raw']} · {p.get('game','?')}"
                )
            text = "\n".join(lines)
            if len(text) > 3800:
                text = text[:3800] + "\n..."
        await query.edit_message_text(text, reply_markup=_back_kb(lang))

    # ---- LOGS ----
    elif action == "logs":
        limit = int(arg) if arg.isdigit() else 30
        logs  = await get_recent_logs(limit=limit)
        if not logs:
            text = "Логов нет." if lang == "ru" else "No logs."
        else:
            lines = []
            for entry in logs:
                uname  = f"@{entry['username']}" if entry.get("username") else "system"
                time   = entry.get("created_at", "")[:16]
                level  = entry.get("level", "INFO")[:4]
                action_name = entry.get("action", "")
                details = entry.get("details", "")
                line = f"[{time}] [{level}] {uname}  {action_name}"
                if details:
                    line += f"\n          {details}"
                lines.append(line)
            text = "\n".join(lines)
            if len(text) > 3800:
                text = text[:3800] + "\n..."
        await query.edit_message_text(
            f"```\n{text}\n```",
            parse_mode="Markdown",
            reply_markup=_logs_kb(lang, limit),
        )

    # ---- DOWNLOAD LOG ----
    elif action == "dl_log":
        log_path = _log_file_path()
        if not log_path:
            await query.answer(
                "Лог-файл не найден." if lang == "ru" else "Log file not found.",
                show_alert=True
            )
            return
        size_kb = os.path.getsize(log_path) // 1024
        fname   = os.path.basename(log_path)
        with open(log_path, "rb") as f:
            await query.message.reply_document(
                document=f,
                filename=fname,
                caption=f"Лог за текущий месяц · {size_kb} KB" if lang == "ru"
                        else f"Current month log · {size_kb} KB",
            )

    # ---- DOWNLOAD DB ----
    elif action == "dl_db":
        if not os.path.exists(DB_PATH):
            await query.answer(
                "БД не найдена." if lang == "ru" else "DB not found.",
                show_alert=True
            )
            return
        size_kb = os.path.getsize(DB_PATH) // 1024
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname   = f"starpets_{ts}.db"
        with open(DB_PATH, "rb") as f:
            await query.message.reply_document(
                document=f,
                filename=fname,
                caption=f"База данных · {size_kb} KB" if lang == "ru"
                        else f"Database · {size_kb} KB",
            )
        log_info("DB_DOWNLOADED", user_id=user["telegram_id"], username=user["username"],
                 size=f"{size_kb}kb")

    # ---- BACKUP ----
    elif action == "backup":
        if not os.path.exists(DB_PATH):
            await query.answer("DB not found.", show_alert=True)
            return
        await query.edit_message_text(
            "Создаю бэкап..." if lang == "ru" else "Creating backup...",
            reply_markup=None
        )
        backup_file = _create_backup()
        size_kb = os.path.getsize(backup_file) // 1024
        await db_log(user["id"], "BACKUP_MANUAL",
                     f"file={os.path.basename(backup_file)} | size={size_kb}kb")
        log_info("BACKUP_MANUAL", user_id=user["telegram_id"], username=user["username"],
                 file=os.path.basename(backup_file), size=f"{size_kb}kb")
        with open(backup_file, "rb") as f:
            await query.message.reply_document(
                document=f,
                filename=os.path.basename(backup_file),
                caption=f"Бэкап · {size_kb} KB" if lang == "ru" else f"Backup · {size_kb} KB",
            )
        users    = await get_all_users()
        bloggers = await get_all_bloggers()
        text = (
            f"Панель администратора\n\nПользователей: {len(users)}\nБлогеров: {len(bloggers)}"
            if lang == "ru" else
            f"Admin panel\n\nUsers: {len(users)}\nBloggers: {len(bloggers)}"
        )
        await query.edit_message_text(text, reply_markup=_admin_main_kb(lang))

    # ---- RESTORE PROMPT ----
    elif action == "restore_prompt":
        context.user_data["awaiting_restore"] = True
        await query.edit_message_text(
            "Отправьте файл .db для восстановления.\n"
            "Текущая база будет сохранена перед заменой.\n"
            "/cancel — отмена"
            if lang == "ru" else
            "Send a .db file to restore.\n"
            "Current database will be backed up first.\n"
            "/cancel — cancel",
            reply_markup=_back_kb(lang),
        )

    # ---- SEARCH PROMPT ----
    elif action == "search_prompt":
        context.user_data["awaiting_admin_search"] = True
        await query.edit_message_text(
            "Введите имя блогера для поиска по всей базе:"
            if lang == "ru" else
            "Enter blogger name to search across all data:",
            reply_markup=_back_kb(lang),
        )

    # ---- SEARCH RESULT ----
    elif action == "search":
        q       = arg
        results = await search_bloggers_global(q)
        if not results:
            text = (f"Ничего не найдено по «{q}»." if lang == "ru"
                    else f"Nothing found for '{q}'.")
        else:
            lines = [f"🔍 «{q}» — {len(results)} результатов:" if lang == "ru"
                     else f"🔍 '{q}' — {len(results)} results:"]
            for b in results:
                methods = await get_all_methods(b["id"])
                mgr     = f"@{b.get('manager_username') or '?'}"
                lines.append(f"\n• {b['name']} (менеджер: {mgr})" if lang == "ru"
                              else f"\n• {b['name']} (manager: {mgr})")
                for m in methods:
                    active = "" if m["is_active"] else (" [откл]" if lang == "ru" else " [off]")
                    star   = " ★" if m.get("is_primary") else ""
                    label  = METHOD_LABELS.get(m["type"], m["type"])
                    lines.append(f"  {label}: {m['address']}{star}{active}")
            text = "\n".join(lines)
            if len(text) > 3800:
                text = text[:3800] + "\n..."
        await query.edit_message_text(text, reply_markup=_back_kb(lang))


# --------------------------------------------------------------------------- #
# Text input handler for search and restore
# --------------------------------------------------------------------------- #
async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    if context.user_data.get("awaiting_admin_search"):
        context.user_data.pop("awaiting_admin_search", None)
        q = update.message.text.strip()
        results = await search_bloggers_global(q)
        if not results:
            text = (f"Ничего не найдено по «{q}»." if lang == "ru"
                    else f"Nothing found for '{q}'.")
            await update.message.reply_text(text, reply_markup=_back_kb(lang))
            return
        lines = [f"🔍 «{q}» — {len(results)} результатов:" if lang == "ru"
                 else f"🔍 '{q}' — {len(results)} results:"]
        for b in results:
            methods = await get_all_methods(b["id"])
            mgr     = f"@{b.get('manager_username') or '?'}"
            lines.append(f"\n• {b['name']} (менеджер: {mgr})" if lang == "ru"
                         else f"\n• {b['name']} (manager: {mgr})")
            for m in methods:
                active = "" if m["is_active"] else (" [откл]" if lang == "ru" else " [off]")
                star   = " ★" if m.get("is_primary") else ""
                label  = METHOD_LABELS.get(m["type"], m["type"])
                lines.append(f"  {label}: {m['address']}{star}{active}")
        text = "\n".join(lines)
        if len(text) > 3800:
            text = text[:3800] + "\n..."
        await update.message.reply_text(text, reply_markup=_back_kb(lang))


# --------------------------------------------------------------------------- #
# Restore file handler
# --------------------------------------------------------------------------- #
async def handle_restore_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_restore"):
        return
    if update.effective_user.id != ADMIN_ID:
        return

    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    doc: Document = update.message.document

    if not doc.file_name.endswith(".db"):
        await update.message.reply_text(
            "Файл должен быть .db" if lang == "ru" else "File must be .db"
        )
        return

    if os.path.exists(DB_PATH):
        _create_backup()

    file = await doc.get_file()
    await file.download_to_drive(DB_PATH)
    context.user_data.pop("awaiting_restore", None)

    await db_log(user["id"], "DB_RESTORED", f"from={doc.file_name}")
    log_info("DB_RESTORED", user_id=user["telegram_id"], username=user["username"],
             file=doc.file_name)

    await update.message.reply_text(
        "База данных восстановлена." if lang == "ru" else "Database restored.",
        reply_markup=_back_kb(lang),
    )


# --------------------------------------------------------------------------- #
# Legacy commands (kept for backward compatibility)
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
    backup_file = _create_backup()
    size_kb = os.path.getsize(backup_file) // 1024
    await db_log(user["id"], "BACKUP_MANUAL",
                 f"file={os.path.basename(backup_file)} | size={size_kb}kb")
    log_info("BACKUP_MANUAL", user_id=user["telegram_id"], username=user["username"],
             file=os.path.basename(backup_file), size=f"{size_kb}kb")
    with open(backup_file, "rb") as f:
        await update.message.reply_document(
            document=f,
            filename=os.path.basename(backup_file),
            caption=f"Бэкап · {size_kb} KB" if lang == "ru" else f"Backup · {size_kb} KB",
        )


@admin_only
async def cmd_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user)
    await update.message.reply_text(
        "Отправьте файл .db для восстановления." if lang == "ru"
        else "Send a .db file to restore."
    )
    context.user_data["awaiting_restore"] = True


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_admin_handlers(app):
    app.add_handler(CommandHandler("admin",   cmd_admin))
    app.add_handler(CommandHandler("backup",  cmd_backup))
    app.add_handler(CommandHandler("restore", cmd_restore))

    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^adm:"))

    app.add_handler(MessageHandler(
        filters.Document.FileExtension("db"),
        handle_restore_file,
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_admin_text,
    ), group=3)