"""
StarPets CashOuts Bot — main entry point.
"""

import logging
from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder, MessageHandler, filters
from telegram.error import BadRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from database.db import init_db
from services.logger import log_system
from handlers.start import register_start_handlers, fallback_message
from handlers.blogger import register_blogger_handlers
from handlers.payout import register_payout_handlers
from handlers.admin import register_admin_handlers
from handlers.export_xlsx import register_export_handlers
from handlers.sheets_handler import register_sheets_handlers
from handlers.admin import _create_backup
from handlers.history import register_history_handlers
from handlers.import_bloggers import register_import_handlers


# --------------------------------------------------------------------------- #
# Bot commands (shown as hints when typing "/")
# --------------------------------------------------------------------------- #
async def set_commands(app: Application):
    commands = [
        BotCommand("start",        "Home / Главная"),
        BotCommand("payout",       "/payout [amb-Name|amb-all]"),
        BotCommand("reformat",     "Reformat existing payout block"),
        BotCommand("bloggers",     "List bloggers"),
        BotCommand("add_blogger",  "Add blogger"),
        BotCommand("add_method",   "Add payment method [blogger name]"),
        BotCommand("edit_method",  "Edit method / set primary"),
        BotCommand("settings",     "Language, manager name"),
        BotCommand("help",         "Commands reference"),
        BotCommand("history",         "Payout history [blogger name]"),
        BotCommand("import_bloggers",  "Bulk import bloggers from list or file"),
        BotCommand("cancel",       "Cancel current action"),
    ]
    await app.bot.set_my_commands(commands)
    log_system("COMMANDS_SET", count=len(commands))


# --------------------------------------------------------------------------- #
# Error handler
# --------------------------------------------------------------------------- #
async def error_handler(update, context):
    _logger = logging.getLogger("starpets")

    # Build user context string for all log messages
    def _user_ctx():
        if not update:
            return "[system]"
        u = update.effective_user
        if not u:
            return "[system]"
        return f"[user={u.username or '?'} | id={u.id}]"

    def _update_ctx():
        if not update:
            return ""
        parts = []
        if update.message and update.message.text:
            txt = update.message.text[:80].replace("\n", " ")
            parts.append(f"msg={repr(txt)}")
        if update.callback_query:
            parts.append(f"callback={update.callback_query.data}")
        if update.effective_chat:
            parts.append(f"chat_id={update.effective_chat.id}")
        last = context.user_data.get("_last_action") if hasattr(context, "user_data") else None
        if last:
            parts.append(f"last_action={last}")
        return " | ".join(parts)

    # Suppress noisy network errors
    _network_noise = (
        "NetworkError", "SSLError", "DECRYPTION_FAILED", "BAD_RECORD_MAC",
        "RemoteProtocolError", "ConnectError", "Server disconnected",
        "httpx.", "Bad Gateway", "ReadError",
    )
    if any(p in str(context.error) for p in _network_noise):
        _logger.warning(f"[system] NETWORK_ERROR | {context.error}")
        return

    # Ignore "Message is not modified" — harmless double-tap
    if isinstance(context.error, BadRequest) and "message is not modified" in str(context.error).lower():
        _logger.debug(f"{_user_ctx()} MSG_NOT_MODIFIED | {_update_ctx()}")
        return

    # Ignore stale button errors
    if isinstance(context.error, BadRequest) and "button_data_invalid" in str(context.error).lower():
        if update and update.callback_query:
            try:
                await update.callback_query.answer(
                    "Кнопка устарела, запросите выплату заново. / Button expired, request payout again.",
                    show_alert=True,
                )
            except Exception:
                pass
        _logger.warning(f"{_user_ctx()} STALE_BUTTON | {_update_ctx()}")
        return

    # Real error — log with full context
    upd_ctx = _update_ctx()
    _logger.error(
        f"{_user_ctx()} UNHANDLED_ERROR | error={context.error}"
        + (f" | {upd_ctx}" if upd_ctx else ""),
        exc_info=context.error,
    )

    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Произошла ошибка. Попробуйте ещё раз или используйте /cancel.\n"
                "An error occurred. Try again or use /cancel."
            )
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Scheduled backup
# --------------------------------------------------------------------------- #
def setup_scheduler(app: Application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _create_backup,
        trigger="cron",
        hour=3,
        minute=0,
        id="daily_backup",
        replace_existing=True,
    )
    scheduler.start()
    async def _daily_sheets_sync():
        from services.sheets_sync import run_full_sync, SPREADSHEET_ID, HAS_GSPREAD
        if not HAS_GSPREAD or not SPREADSHEET_ID:
            return
        log_system("SHEETS_AUTO_SYNC_START")
        try:
            results = await run_full_sync(mode="new_only")
            total_added = sum(len(r.added) for r in results.values())
            total_errors = sum(len(r.errors) for r in results.values())
            log_system("SHEETS_AUTO_SYNC_DONE", added=total_added, errors=total_errors)
            if total_added > 0:
                try:
                    await app.bot.send_message(
                        config.ADMIN_ID,
                        f"Авто-синхронизация Google Sheets: добавлено {total_added} блогеров."
                    )
                except Exception:
                    pass
        except ValueError as e:
            log_system("SHEETS_AUTO_SYNC_ABORTED", reason=str(e))
            try:
                await app.bot.send_message(config.ADMIN_ID, f"⚠️ Авто-синхронизация Google Sheets отменена:\n{e}")
            except Exception:
                pass
        except Exception as e:
            log_system("SHEETS_AUTO_SYNC_ERROR", error=str(e))

    scheduler.add_job(
        _daily_sheets_sync,
        trigger="cron",
        hour=4,
        minute=0,
        id="daily_sheets_sync",
    )
    log_system("SCHEDULER_STARTED", job="daily_backup at 03:00, sheets_sync at 04:00")


# --------------------------------------------------------------------------- #
# post_init
# --------------------------------------------------------------------------- #
async def post_init(app: Application):
    await init_db()
    await set_commands(app)
    log_system("BOT_STARTING")
    setup_scheduler(app)
    log_system("BOT_STARTED")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Group 0 — all commands and ConversationHandlers
    register_start_handlers(app)
    register_blogger_handlers(app)
    register_payout_handlers(app)
    register_admin_handlers(app)
    register_export_handlers(app)
    register_sheets_handlers(app)
    register_history_handlers(app)
    register_import_handlers(app)

    # Group 1 — fallback for plain text outside any active conversation
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_message),
        group=1,
    )

    app.add_error_handler(error_handler)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()