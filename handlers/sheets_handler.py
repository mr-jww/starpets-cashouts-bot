"""
/sync_sheets — admin command to sync Google Sheets into DB.
Also adds Sync button to admin panel.
"""
from __future__ import annotations
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from handlers.common import admin_only, get_lang
from database.queries import get_user, get_all_users
from services.logger import log_info, log_system
from config import ADMIN_ID


async def _do_sync(update_or_query, context, lang: str, mode: str = "new_only"):
    from services.sheets_sync import run_full_sync, HAS_GSPREAD, SPREADSHEET_ID

    chat = (
        update_or_query.effective_chat
        if hasattr(update_or_query, "effective_chat")
        else update_or_query.message.chat
    )

    if not HAS_GSPREAD:
        await chat.send_message(
            "Библиотека gspread не установлена.\n"
            "Выполни на сервере: pip install gspread"
            if lang == "ru" else
            "gspread library is not installed.\n"
            "Run on server: pip install gspread"
        )
        return

    if not SPREADSHEET_ID:
        await chat.send_message(
            "SHEETS_ID не задан в .env" if lang == "ru" else "SHEETS_ID not set in .env"
        )
        return

    mode_label = ("только новые" if mode == "new_only" else "полная") if lang == "ru" \
                 else ("new only" if mode == "new_only" else "full")
    await chat.send_message(
        f"Читаю таблицу ({mode_label})..." if lang == "ru"
        else f"Reading spreadsheet ({mode_label})..."
    )

    try:
        results = await run_full_sync(mode=mode, skip_sanity=(mode == "full"))
    except ValueError as e:
        # Sanity check failed
        if lang == "ru":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⚠️ Всё равно применить", callback_data="sync:force_full")],
                [InlineKeyboardButton("✕ Отмена", callback_data="sync:cancel")],
            ])
            await chat.send_message(
                f"Проверка данных не прошла:\n{e}\n\nПрименить принудительно?",
                reply_markup=kb
            )
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⚠️ Apply anyway", callback_data="sync:force_full")],
                [InlineKeyboardButton("✕ Cancel", callback_data="sync:cancel")],
            ])
            await chat.send_message(
                f"Data check failed:\n{e}\n\nApply anyway?",
                reply_markup=kb
            )
        return
    except Exception as e:
        await chat.send_message(
            f"Ошибка при чтении таблицы: {e}" if lang == "ru"
            else f"Error reading spreadsheet: {e}"
        )
        return

    lines = []
    total_added = total_updated = total_errors = 0
    for sheet_name, r in results.items():
        total_added   += len(r.added)
        total_updated += len(r.updated)
        total_errors  += len(r.errors)
        if r.errors and len(r.errors) == 1 and "No user found" in r.errors[0]:
            lines.append(f"  ⚠️ {sheet_name}: нет пользователя в БД" if lang == "ru"
                         else f"  ⚠️ {sheet_name}: no matching user in DB")
        elif r.added or r.updated:
            lines.append(f"  {sheet_name}: +{len(r.added)} / ~{len(r.updated)}")
        elif r.errors:
            lines.append(f"  {sheet_name}: {len(r.errors)} ошибок" if lang == "ru"
                         else f"  {sheet_name}: {len(r.errors)} errors")

    if lang == "ru":
        summary = (
            f"Синхронизация завершена\n\n"
            f"Добавлено: {total_added}\n"
            f"Обновлено: {total_updated}\n"
            f"Ошибок: {total_errors}\n\n"
        )
    else:
        summary = (
            f"Sync complete\n\n"
            f"Added: {total_added}\n"
            f"Updated: {total_updated}\n"
            f"Errors: {total_errors}\n\n"
        )
    if lines:
        summary += "\n".join(lines)

    await chat.send_message(summary)
    log_system("SHEETS_SYNC_DONE",
               added=total_added, updated=total_updated, errors=total_errors)


# --------------------------------------------------------------------------- #
# /sync_sheets command
# --------------------------------------------------------------------------- #
@admin_only
async def cmd_sync_sheets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user)
    # Ask for mode
    if lang == "ru":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Только новые",  callback_data="sync:new_only")],
            [InlineKeyboardButton("🔄 Полная синхронизация", callback_data="sync:full")],
            [InlineKeyboardButton("✕ Отмена", callback_data="sync:cancel")],
        ])
        await update.message.reply_text(
            "Выбери режим синхронизации с Google Sheets:", reply_markup=kb
        )
    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ New only",     callback_data="sync:new_only")],
            [InlineKeyboardButton("🔄 Full sync",   callback_data="sync:full")],
            [InlineKeyboardButton("✕ Cancel",       callback_data="sync:cancel")],
        ])
        await update.message.reply_text(
            "Select sync mode for Google Sheets:", reply_markup=kb
        )


# --------------------------------------------------------------------------- #
# Callback handler
# --------------------------------------------------------------------------- #
async def cb_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    if not user or user["telegram_id"] != ADMIN_ID:
        return
    lang = get_lang(user)
    mode = query.data.split(":")[1]

    if mode == "cancel":
        await query.edit_message_text("Отменено." if lang == "ru" else "Cancelled.")
        return

    if mode == "force_full":
        await query.edit_message_reply_markup(reply_markup=None)
        # Run full sync bypassing sanity check
        from services.sheets_sync import run_full_sync as _rsf, HAS_GSPREAD, SPREADSHEET_ID
        if not HAS_GSPREAD or not SPREADSHEET_ID:
            await query.message.reply_text("gspread or SHEETS_ID not configured.")
            return
        await query.message.reply_text("Применяю принудительно..." if lang == "ru" else "Applying forcefully...")
        try:
            results = await _rsf(mode="full", skip_sanity=True)
            total_added = sum(len(r.added) for r in results.values())
            total_updated = sum(len(r.updated) for r in results.values())
            await query.message.reply_text(
                f"Готово. Добавлено: {total_added}, обновлено: {total_updated}"
                if lang == "ru" else
                f"Done. Added: {total_added}, updated: {total_updated}"
            )
        except Exception as e:
            await query.message.reply_text(f"Ошибка: {e}" if lang == "ru" else f"Error: {e}")
        return

    await query.edit_message_reply_markup(reply_markup=None)
    await _do_sync(query, context, lang, mode=mode)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_sheets_handlers(app):
    app.add_handler(CommandHandler("sync_sheets", cmd_sync_sheets))
    app.add_handler(CallbackQueryHandler(cb_sync, pattern=r"^sync:"))