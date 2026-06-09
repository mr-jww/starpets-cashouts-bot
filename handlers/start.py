"""
Handlers: /start, /help, /settings, /reformat, fallback
"""

from __future__ import annotations
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters,
)

from database.queries import (upsert_user, get_user, set_user_lang, set_manager_filter,
    set_output_mode, set_default_fmt, set_filter_setting, db_log,
    set_manager_password, check_manager_password, reset_lockout, get_locked_users)
from services.logger import log_info
from handlers.common import get_user_or_reject, get_lang
from config import ADMIN_ID, TEAM_PASSWORD, MANAGER_BUTTON_ORDER

# States for /reformat conversation
WAIT_REFORMAT = 0



# --------------------------------------------------------------------------- #
# Persistent bottom keyboard (always visible)
# --------------------------------------------------------------------------- #
def _persistent_keyboard(lang: str = "en", role: str = "manager") -> ReplyKeyboardMarkup:
    row1 = [KeyboardButton("🏠 Home"), KeyboardButton("💸 Payout")]
    row2 = [KeyboardButton("👥 Bloggers"), KeyboardButton("⚙️ Settings")]
    return ReplyKeyboardMarkup(
        [row1, row2],
        resize_keyboard=True,
        is_persistent=True,
    )

# --------------------------------------------------------------------------- #
# /start  (English by default)
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    role = "admin" if tg.id == ADMIN_ID else "manager"
    user = await upsert_user(tg.id, tg.username, role)
    lang = get_lang(user)

    log_info("START", user_id=tg.id, username=tg.username, role=role)
    await db_log(user["id"], "START", f"role={role}")

    await update.message.reply_text(
        _start_text(tg.first_name, lang),
        reply_markup=_persistent_keyboard(lang, role),
    )


def _start_text(name: str, lang: str) -> str:
    if lang == "ru":
        return (
            f"Привет, {name}!\n\n"
            "Этот бот помогает оформлять выплаты блогерам StarPets. "
            "Ты копируешь строки из таблицы – бот сам считает суммы, "
            "подбирает реквизиты и готовит блок для бухгалтера.\n\n"
            "Чтобы начать: укажи своё имя из таблицы в настройках, "
            "добавь блогеров и их методы оплаты – дальше всё делается через /payout."
        )
    return (
        f"Hi, {name}!\n\n"
        "This bot helps you process payouts for StarPets bloggers. "
        "You paste rows from the spreadsheet – the bot calculates totals, "
        "picks the right payment details and formats a block for the accountant.\n\n"
        "To get started: set your manager name in settings, "
        "add your bloggers and their payment methods – then everything goes through /payout."
    )


def _main_keyboard(lang: str, role: str = "manager") -> InlineKeyboardMarkup:
    if lang == "ru":
        buttons = [
            [InlineKeyboardButton("➕ Блогер",   callback_data="bm:add_blogger_start:home")],
            [InlineKeyboardButton("💸 Заказать выплату", callback_data="start_payout")],
            [InlineKeyboardButton("📋 Инструкция", callback_data="show_help"),
             InlineKeyboardButton("⚙️ Настройки",  callback_data="show_settings")],
            [InlineKeyboardButton("••• Ещё",        callback_data="show_more")],
        ]
        if role == "admin":
            buttons.append([InlineKeyboardButton("🔧 Админ", callback_data="show_admin_hint")])
    else:
        buttons = [
            [InlineKeyboardButton("➕ Blogger",  callback_data="bm:add_blogger_start:home")],
            [InlineKeyboardButton("💸 Create payout",  callback_data="start_payout")],
            [InlineKeyboardButton("📋 Instructions", callback_data="show_help"),
             InlineKeyboardButton("⚙️ Settings",     callback_data="show_settings")],
            [InlineKeyboardButton("••• More",         callback_data="show_more")],
        ]
        if role == "admin":
            buttons.append([InlineKeyboardButton("🔧 Admin", callback_data="show_admin_hint")])
    return InlineKeyboardMarkup(buttons)


def _back_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("← Back" if lang == "en" else "← Назад", callback_data="show_start")
    ]])


# --------------------------------------------------------------------------- #
# Inline button: Instructions
# --------------------------------------------------------------------------- #
async def cb_show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    if lang == "ru":
        text = (
            "*Справка*\n\n"
            "*/payout* – главная команда. Скопируй строки из таблицы и вставь следующим сообщением."
            " Бот разберёт блогеров, сложит суммы и выдаст готовые блоки для бухгалтера.\n"
            "Если в настройках задано твоё имя менеджера, фильтр по нему применится автоматически."
            " Можно переопределить: `/payout amb-Name` – только строки Джона,"
            " `/payout amb-all` – все строки без фильтра.\n\n"
            "*/bloggers* – список блогеров с реквизитами. Нажми на любого, чтобы посмотреть карточку,"
            " добавить метод оплаты, заметку или удалить запись.\n\n"
            "*/import_bloggers* – добавить сразу нескольких блогеров из списка или файла .txt.\n"
            "Формат строки (через Tab): `имя | Site ID | USDT-TRC20 | PayPal | основной`\n"
            "Пустые ячейки пропускаются. Хотя бы один метод обязателен.\n\n"
            "*/history [ник]* – история выплат конкретному блогеру.\n\n"
            "*/reformat* – если у тебя уже есть готовый блок выплаты, эта команда переключит его"
            " между однострочным и многострочным форматом.\n\n"
            "*/settings* – язык, имя менеджера, формат вывода по умолчанию.\n\n"
            "Платформы: YouTube, YouTube Shorts, TikTok, Instagram, Facebook.\n"
            "Методы оплаты: Site, USDT-TRC20, PayPal. Валюты: $, €, ₽."
        )
    else:
        text = (
            "*Help*\n\n"
            "*/payout* – the main command. Copy rows from the spreadsheet and paste them as the next"
            " message. The bot will parse the bloggers, total up the amounts and produce ready-made"
            " blocks for the accountant.\n"
            "If your manager name is set in settings, it filters automatically."
            " You can override: `/payout amb-Name` – only John's rows,"
            " `/payout amb-all` – everything without a filter.\n\n"
            "*/bloggers* – your blogger list with payment details. Tap any blogger to open their card,"
            " add a payment method, leave a note or remove the entry.\n\n"
            "*/import_bloggers* – add multiple bloggers at once from a list or a .txt file.\n"
            "Row format (tab-separated): `name | Site ID | USDT-TRC20 | PayPal | primary`\n"
            "Empty cells are skipped. At least one method is required.\n\n"
            "*/history [username]* – payout history for a specific blogger.\n\n"
            "*/reformat* – if you already have a finished payout block, this switches it between"
            " one-line and multiline format.\n\n"
            "*/settings* – language, manager name, default output format.\n\n"
            "Platforms: YouTube, YouTube Shorts, TikTok, Instagram, Facebook.\n"
            "Payment methods: Site, USDT-TRC20, PayPal. Currencies: $, €, ₽."
        )
    await query.edit_message_text(text, reply_markup=_back_keyboard(lang), parse_mode="Markdown")


# --------------------------------------------------------------------------- #
# Inline button: Settings
# --------------------------------------------------------------------------- #

async def cb_show_more(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    if lang == "ru":
        text = "Дополнительные функции:"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Переформат",     callback_data="rf_again")],
            [InlineKeyboardButton("📥 Импорт блогеров", callback_data="go_import")],
            [InlineKeyboardButton("🔄 Синхр. с таблицей", callback_data="sync_my_sheet")],
            [InlineKeyboardButton("← Назад",           callback_data="show_start")],
        ])
    else:
        text = "Additional features:"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Reformat",        callback_data="rf_again")],
            [InlineKeyboardButton("📥 Import bloggers", callback_data="go_import")],
            [InlineKeyboardButton("🔄 Sync with sheet", callback_data="sync_my_sheet")],
            [InlineKeyboardButton("← Back",             callback_data="show_start")],
        ])
    await query.edit_message_text(text, reply_markup=kb)



async def cb_sync_my_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show sync mode selection."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    mgr_name = user.get("manager_filter") if user else None

    if not mgr_name:
        await query.edit_message_text(
            "Сначала выбери своё имя в настройках." if lang == "ru"
            else "Set your manager name in settings first.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад" if lang == "ru" else "← Back",
                                     callback_data="show_more")
            ]])
        )
        return

    if lang == "ru":
        text = f"Синхронизация листа \u00ab{mgr_name}\u00bb.\nВыбери режим:"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Только новые", callback_data="sync_sheet:new_only")],
            [InlineKeyboardButton("🔄 Полная синхронизация", callback_data="sync_sheet:full")],
            [InlineKeyboardButton("← Назад", callback_data="show_more")],
        ])
    else:
        text = f"Sync sheet \u00ab{mgr_name}\u00bb.\nChoose mode:"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ New only", callback_data="sync_sheet:new_only")],
            [InlineKeyboardButton("🔄 Full sync", callback_data="sync_sheet:full")],
            [InlineKeyboardButton("← Back", callback_data="show_more")],
        ])
    await query.edit_message_text(text, reply_markup=kb)


async def cb_sync_sheet_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run the actual sheet sync."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    mode = query.data.split(":")[1]
    mgr_name = user.get("manager_filter") if user else None

    from services.sheets_sync import read_sheets, sync_sheets_to_db, HAS_GSPREAD, SPREADSHEET_ID
    if not HAS_GSPREAD or not SPREADSHEET_ID:
        await query.edit_message_text(
            "Google Sheets не настроен." if lang == "ru"
            else "Google Sheets is not configured."
        )
        return

    await query.edit_message_text(
        f"Читаю лист «{mgr_name}»..." if lang == "ru"
        else f"Reading sheet «{mgr_name}»..."
    )

    try:
        sheets_data = read_sheets(sheet_names=[mgr_name])
        rows = sheets_data.get(mgr_name, [])
        if not rows:
            await query.message.reply_text(
                f"Лист «{mgr_name}» пуст или не найден." if lang == "ru"
                else f"Sheet «{mgr_name}» is empty or not found."
            )
        else:
            result = await sync_sheets_to_db(mgr_name, rows, user["id"], mode=mode)
            if lang == "ru":
                text = f"Готово: добавлено {len(result.added)}, ошибок {len(result.errors)}."
            else:
                text = f"Done: added {len(result.added)}, errors {len(result.errors)}."
            await query.message.reply_text(text)
    except Exception as e:
        await query.message.reply_text(
            f"Ошибка: {e}" if lang == "ru" else f"Error: {e}"
        )


async def cb_show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    await query.edit_message_text(
        _settings_text(user, lang),
        reply_markup=_settings_keyboard(user, lang),
    )


def _settings_text(user: dict, lang: str) -> str:
    mgr = user.get("manager_filter") or ("не задано" if lang == "ru" else "not set")
    cur_lang = "Русский 🇷🇺" if lang == "ru" else "English 🇬🇧"
    out_mode = user.get("output_mode") or "block"
    out_label = ("Блок" if out_mode == "block" else "Текст") if lang == "ru" \
                else ("Block" if out_mode == "block" else "Text")
    def_fmt = user.get("default_fmt") or "oneline"
    fmt_label = ("Однострочный" if def_fmt == "oneline" else "Многострочный") if lang == "ru" \
                else ("One line" if def_fmt == "oneline" else "Multiline")
    ip  = bool(user.get("include_paid",    0))
    wp  = bool(user.get("warn_paid",       1))
    ipe = bool(user.get("include_pending", 0))
    wpe = bool(user.get("warn_pending",    1))
    if lang == "ru":
        return (
            f"Настройки:\n\n"
            f"Язык: {cur_lang}\n"
            f"Имя менеджера (фильтр): {mgr}\n"
            f"Оформление: {out_label}\n"
            f"Формат выплаты: {fmt_label}\n"
            f"PAID – включать в выплату: {'да' if ip  else 'нет'}\n"
            f"PAID – предупреждать: {'да' if wp  else 'нет'}\n"
            f"PENDING – включать в выплату: {'да' if ipe else 'нет'}\n"
            f"PENDING – предупреждать: {'да' if wpe else 'нет'}"
        )
    return (
        f"Settings:\n\n"
        f"Language: {cur_lang}\n"
        f"Manager name (filter): {mgr}\n"
        f"Output style: {out_label}\n"
        f"Payout format: {fmt_label}\n"
        f"PAID – include in payout: {'yes' if ip  else 'no'}\n"
        f"PAID – warn about: {'yes' if wp  else 'no'}\n"
        f"PENDING – include in payout: {'yes' if ipe else 'no'}\n"
        f"PENDING – warn about: {'yes' if wpe else 'no'}"
    )


def _settings_keyboard(user: dict, lang: str) -> InlineKeyboardMarkup:
    out_mode = user.get("output_mode") or "block"
    def_fmt  = user.get("default_fmt") or "oneline"
    ip  = bool(user.get("include_paid",    0))
    wp  = bool(user.get("warn_paid",       1))
    ipe = bool(user.get("include_pending", 0))
    wpe = bool(user.get("warn_pending",    1))

    if lang == "ru":
        out_btn = f"Оформление: {'Блок' if out_mode == 'block' else 'Текст'}"
        fmt_btn = f"Формат: {'Однострочный' if def_fmt == 'oneline' else 'Многострочный'}"
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🇷🇺 Русский", callback_data="set_lang:ru"),
                InlineKeyboardButton("🇬🇧 English", callback_data="set_lang:en"),
            ],
            [InlineKeyboardButton("👤 Изменить имя менеджера", callback_data="set_mgr")],
            [InlineKeyboardButton(out_btn, callback_data="toggle_output_mode")],
            [InlineKeyboardButton(fmt_btn, callback_data="toggle_default_fmt")],
            [
                InlineKeyboardButton(f"PAID: {'включать' if ip else 'пропускать'}", callback_data="toggle_filter_paid"),
                InlineKeyboardButton(f"Предупрежд.: {'вкл' if wp else 'выкл'}", callback_data="toggle_warn_paid"),
            ],
            [
                InlineKeyboardButton(f"PENDING: {'включать' if ipe else 'пропускать'}", callback_data="toggle_filter_pending"),
                InlineKeyboardButton(f"Предупрежд.: {'вкл' if wpe else 'выкл'}", callback_data="toggle_warn_pending"),
            ],
            [InlineKeyboardButton("← Назад", callback_data="show_start")],
        ])

    out_btn = f"Style: {'Block' if out_mode == 'block' else 'Text'}"
    fmt_btn = f"Format: {'One line' if def_fmt == 'oneline' else 'Multiline'}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇷🇺 Русский", callback_data="set_lang:ru"),
            InlineKeyboardButton("🇬🇧 English", callback_data="set_lang:en"),
        ],
        [InlineKeyboardButton("👤 Set manager name", callback_data="set_mgr")],
        [InlineKeyboardButton(out_btn, callback_data="toggle_output_mode")],
        [InlineKeyboardButton(fmt_btn, callback_data="toggle_default_fmt")],
        [
            InlineKeyboardButton(f"PAID: {'include' if ip else 'skip'}", callback_data="toggle_filter_paid"),
            InlineKeyboardButton(f"Warn: {'on' if wp else 'off'}", callback_data="toggle_warn_paid"),
        ],
        [
            InlineKeyboardButton(f"PENDING: {'include' if ipe else 'skip'}", callback_data="toggle_filter_pending"),
            InlineKeyboardButton(f"Warn: {'on' if wpe else 'off'}", callback_data="toggle_warn_pending"),
        ],
        [InlineKeyboardButton("← Back", callback_data="show_start")],
    ])


async def cb_set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg = update.effective_user
    user = await get_user_or_reject(update)
    if not user:
        return
    lang = query.data.split(":")[1]
    await set_user_lang(tg.id, lang)
    await db_log(user["id"], "LANG_CHANGED", f"lang={lang}")
    log_info("LANG_CHANGED", user_id=tg.id, username=tg.username, lang=lang)
    # Refresh settings view
    user["lang"] = lang
    await query.edit_message_text(
        _settings_text(user, lang),
        reply_markup=_settings_keyboard(user, lang),
    )


async def _mgr_selection_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Build 3-column manager selection keyboard."""
    buttons = []
    row = []
    for name in MANAGER_BUTTON_ORDER:
        row.append(InlineKeyboardButton(name, callback_data=f"mgr_pick:{name}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # Bottom row: manual input + clear
    if lang == "ru":
        buttons.append([
            InlineKeyboardButton("✏ Ввести вручную", callback_data="mgr_manual"),
            InlineKeyboardButton("✕ Убрать фильтр",  callback_data="mgr_clear"),
        ])
        buttons.append([InlineKeyboardButton("← Назад", callback_data="show_settings")])
    else:
        buttons.append([
            InlineKeyboardButton("✏ Enter manually", callback_data="mgr_manual"),
            InlineKeyboardButton("✕ Clear filter",   callback_data="mgr_clear"),
        ])
        buttons.append([InlineKeyboardButton("← Back", callback_data="show_settings")])
    return InlineKeyboardMarkup(buttons)


async def cb_set_mgr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data["awaiting_mgr_msg_id"] = query.message.message_id
    text = (
        "Выбери своё имя или введи вручную:"
        if lang == "ru" else
        "Select your name or enter manually:"
    )
    await query.edit_message_text(text, reply_markup=await _mgr_selection_keyboard(lang))


async def cb_mgr_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manager selected from button."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    # Warn if payout is active
    _payout_active = any(k.startswith("pd_") or k in ("known", "unknown", "payout_raw")
                         for k in context.user_data)
    if _payout_active:
        await query.answer(
            "Сначала завершите текущую выплату" if lang == "ru"
            else "Finish the current payout first",
            show_alert=True
        )
        return

    name = query.data.split(":", 1)[1]
    context.user_data["mgr_pending_name"] = name
    context.user_data["awaiting_mgr_msg_id"] = query.message.message_id

    # If already confirmed team password once — skip for all future name selections
    if user and user.get("mgr_password"):
        await _apply_mgr_name(update, context, name, lang)
        return
    # Also skip if confirmed in this session
    if context.user_data.get("team_confirmed"):
        await _apply_mgr_name(update, context, name, lang)
        return

    # If no team password set — skip confirmation entirely
    if not TEAM_PASSWORD:
        await _apply_mgr_name(update, context, name, lang)
        return

    context.user_data["awaiting_mgr_pw"] = True

    if lang == "ru":
        await query.edit_message_text(
            f"Выбрано: {name}\n\nВведи командный пароль:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад", callback_data="set_mgr")
            ]])
        )
    else:
        await query.edit_message_text(
            f"Selected: {name}\n\nEnter team password:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Back", callback_data="set_mgr")
            ]])
        )


async def cb_mgr_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual name input — no password required."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data["awaiting_mgr"] = True
    context.user_data["awaiting_mgr_manual"] = True
    context.user_data["awaiting_mgr_msg_id"] = query.message.message_id
    if lang == "ru":
        await query.edit_message_text(
            "Введи своё имя менеджера (точно как в таблице):\n/cancel — отмена",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад", callback_data="set_mgr")
            ]])
        )
    else:
        await query.edit_message_text(
            "Enter your manager name (exactly as in the spreadsheet):\n/cancel — cancel",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Back", callback_data="set_mgr")
            ]])
        )


async def _apply_mgr_name(update, context, name: str, lang: str):
    """Set manager filter and return to settings."""
    await set_manager_filter(update.effective_user.id, name)
    log_info("MGR_FILTER_SET", user_id=update.effective_user.id,
             username=update.effective_user.username, name=name)
    user = await get_user(update.effective_user.id)
    user["manager_filter"] = name
    msg_id = context.user_data.pop("awaiting_mgr_msg_id", None)
    context.user_data.pop("awaiting_mgr", None)
    context.user_data.pop("awaiting_mgr_manual", None)
    context.user_data.pop("mgr_pending_name", None)
    context.user_data.pop("awaiting_mgr_pw", None)
    if msg_id:
        try:
            await update.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg_id,
                text=_settings_text(user, lang),
                reply_markup=_settings_keyboard(user, lang),
            )
            return
        except Exception:
            pass
    await update.effective_chat.send_message(
        _settings_text(user, lang),
        reply_markup=_settings_keyboard(user, lang),
    )


async def handle_mgr_pw_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle password input for manager selection."""
    if not context.user_data.get("awaiting_mgr_pw"):
        return False
    user_db = await get_user(update.effective_user.id)
    lang = get_lang(user_db) if user_db else "en"
    pw = (update.message.text or "").strip()
    name = context.user_data.get("mgr_pending_name", "")
    msg_id = context.user_data.get("awaiting_mgr_msg_id")

    try:
        await update.message.delete()
    except Exception:
        pass

    # No team password set — skip confirmation
    if not TEAM_PASSWORD:
        await _apply_mgr_name(update, context, name, lang)
        return True

    if pw == TEAM_PASSWORD:
        from database.queries import set_manager_password
        await set_manager_password(update.effective_user.id, "confirmed")
        context.user_data["team_confirmed"] = True
        await _apply_mgr_name(update, context, name, lang)
        return True

    # Wrong password — track attempts in user_data (not DB, simpler)
    attempts = context.user_data.get("mgr_pw_attempts", 0) + 1
    context.user_data["mgr_pw_attempts"] = attempts
    remaining = 5 - attempts

    if remaining <= 0:
        context.user_data.pop("awaiting_mgr_pw", None)
        context.user_data.pop("mgr_pw_attempts", None)
        context.user_data.pop("mgr_pending_name", None)
        if msg_id:
            try:
                await update.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=msg_id,
                    text=(
                        "Слишком много неверных попыток. Подожди 10 минут."
                        if lang == "ru" else
                        "Too many wrong attempts. Wait 10 minutes."
                    ),
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            "← К выбору" if lang == "ru" else "← Back to selection",
                            callback_data="set_mgr"
                        )
                    ]])
                )
            except Exception:
                pass
        # Notify admin
        from config import ADMIN_ID as _ADMIN_ID
        try:
            await update.bot.send_message(
                _ADMIN_ID,
                f"⚠️ 5 неверных попыток входа за {name} от @{update.effective_user.username} (id={update.effective_user.id})"
            )
        except Exception:
            pass
        return True

    if msg_id:
        try:
            await update.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg_id,
                text=(
                    f"Неверный пароль. Осталось попыток: {remaining}\nВведи пароль:"
                    if lang == "ru" else
                    f"Wrong password. Attempts left: {remaining}\nEnter password:"
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "← Назад" if lang == "ru" else "← Back",
                        callback_data="set_mgr"
                    )
                ]])
            )
        except Exception:
            pass
    return True


async def handle_mgr_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_mgr"):
        return
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    name = update.message.text.strip()
    await set_manager_filter(update.effective_user.id, name)
    context.user_data.pop("awaiting_mgr", None)
    await db_log(user["id"], "MGR_FILTER_SET", f"name={name}")
    log_info("MGR_FILTER_SET", user_id=user["telegram_id"], username=user["username"], name=name)
    if lang == "ru":
        await update.message.reply_text(
            f"Имя менеджера установлено: {name}\n"
            f"Теперь /payout автоматически фильтрует по «{name}»."
        )
    else:
        await update.message.reply_text(
            f"Manager name set: {name}\n"
            f"Now /payout automatically filters by '{name}'."
        )


async def handle_mgr_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_mgr"):
        return
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    await set_manager_filter(update.effective_user.id, None)
    context.user_data.pop("awaiting_mgr", None)
    # Try to edit the original settings message; fallback to reply
    msg_id = context.user_data.pop("awaiting_mgr_msg_id", None)
    user["manager_filter"] = None
    if msg_id:
        try:
            await update.message.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg_id,
                text=_settings_text(user, lang),
                reply_markup=_settings_keyboard(user, lang),
            )
            return
        except Exception:
            pass
    await update.message.reply_text(
        "Фильтр сброшен." if lang == "ru" else "Filter cleared.",
        reply_markup=_settings_keyboard(user, lang),
    )



async def cb_mgr_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear manager filter via inline button."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data.pop("awaiting_mgr", None)
    context.user_data.pop("awaiting_mgr_msg_id", None)
    await set_manager_filter(update.effective_user.id, None)
    log_info("MGR_FILTER_CLEARED", user_id=update.effective_user.id,
             username=update.effective_user.username)
    user["manager_filter"] = None
    await query.edit_message_text(
        _settings_text(user, lang),
        reply_markup=_settings_keyboard(user, lang),
    )


# --------------------------------------------------------------------------- #
# Inline button: Admin hint
# --------------------------------------------------------------------------- #
async def cb_show_admin_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open real admin panel instead of showing stale text."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    from handlers.admin import _admin_main_kb
    from database.queries import get_all_users, get_all_bloggers
    users    = await get_all_users()
    bloggers = await get_all_bloggers()
    text = (
        f"Панель администратора\n\nПользователей: {len(users)}\nБлогеров: {len(bloggers)}"
        if lang == "ru" else
        f"Admin panel\n\nUsers: {len(users)}\nBloggers: {len(bloggers)}"
    )
    await query.edit_message_text(text, reply_markup=_admin_main_kb(lang))


# --------------------------------------------------------------------------- #
# Inline button: Back to start
# --------------------------------------------------------------------------- #
async def cb_show_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tg = update.effective_user
    user = await get_user(tg.id)
    lang = get_lang(user) if user else "en"
    role = user["role"] if user else "manager"
    await query.edit_message_text(
        _start_text(tg.first_name, lang),
        reply_markup=_main_keyboard(lang, role),
    )



async def cb_toggle_output_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    current = user.get("output_mode") or "block"
    new_mode = "text" if current == "block" else "block"
    await set_output_mode(update.effective_user.id, new_mode)
    user["output_mode"] = new_mode
    user["output_mode"] = new_mode
    await query.edit_message_text(
        _settings_text(user, lang),
        reply_markup=_settings_keyboard(user, lang),
    )



async def cb_toggle_default_fmt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    current = user.get("default_fmt") or "oneline"
    new_fmt = "multiline" if current == "oneline" else "oneline"
    await set_default_fmt(update.effective_user.id, new_fmt)
    user["default_fmt"] = new_fmt
    await query.edit_message_text(
        _settings_text(user, lang),
        reply_markup=_settings_keyboard(user, lang),
    )



async def cb_go_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    if lang == "ru":
        text = (
            "Массовое добавление блогеров – команда /import_bloggers.\n\n"
            "Отправь список текстом или файлом .txt. Каждая строка – один блогер,"
            " столбцы через Tab:\n"
            "`имя | Site ID | USDT-TRC20 | PayPal | основной`\n\n"
            "Пример:\n"
            "`braba7x.ff1\t690779e7...\t\t\tsite`\n"
            "`taypk7\t\tTLBwE3...\t\tusdt-trc20`"
        )
    else:
        text = (
            "Bulk import – use /import_bloggers.\n\n"
            "Send a list as text or as a .txt file. One line = one blogger,"
            " tab-separated columns:\n"
            "`name | Site ID | USDT-TRC20 | PayPal | primary`\n\n"
            "Example:\n"
            "`braba7x.ff1\t690779e7...\t\t\tsite`\n"
            "`taypk7\t\tTLBwE3...\t\tusdt-trc20`"
        )
    await query.edit_message_text(text, reply_markup=_back_keyboard(lang))



async def cb_start_payout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start payout directly from inline button on home screen."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data["user"] = user
    context.user_data["effective_filter"] = user.get("manager_filter") or None if user else None
    from handlers.payout import WAIT_ROWS
    if lang == "ru":
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✕ Отмена", callback_data="payout_cancel"),
        ]])
        await query.message.reply_text("Вставьте строки из таблицы.", reply_markup=keyboard)
    else:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✕ Cancel", callback_data="payout_cancel"),
        ]])
        await query.message.reply_text("Paste rows from the spreadsheet.", reply_markup=keyboard)



async def cb_toggle_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    field_map = {
        "toggle_filter_paid":    "include_paid",
        "toggle_warn_paid":      "warn_paid",
        "toggle_filter_pending": "include_pending",
        "toggle_warn_pending":   "warn_pending",
    }
    field = field_map.get(query.data)
    if not field:
        return
    default = 1 if field.startswith("warn_") else 0
    current = bool(user.get(field, default))
    await set_filter_setting(update.effective_user.id, field, 0 if current else 1)
    user[field] = 0 if current else 1
    await query.edit_message_text(
        _settings_text(user, lang),
        reply_markup=_settings_keyboard(user, lang),
    )



async def cb_rf_again(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start a new /reformat — entry point for ConversationHandler."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    # Clear any active blogger input state
    for k in ("bm_action", "bm_msg_id", "bm_blogger_id", "bm_user_id",
              "awaiting_mgr", "awaiting_mgr_pw", "ib_user"):
        context.user_data.pop(k, None)
    context.user_data["rf_user"] = user
    await query.message.reply_text(
        "Вставьте блок для переформатирования:\n/cancel — отмена"
        if lang == "ru" else
        "Paste the payout block to reformat:\n/cancel — cancel"
    )
    return WAIT_REFORMAT


# --------------------------------------------------------------------------- #
# /settings (command)
# --------------------------------------------------------------------------- #
async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return
    lang = get_lang(user)
    await update.message.reply_text(
        _settings_text(user, lang),
        reply_markup=_settings_keyboard(user, lang),
    )


# --------------------------------------------------------------------------- #
# /help (command)
# --------------------------------------------------------------------------- #
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):  # noqa
    user = await get_user_or_reject(update)
    if not user:
        return
    lang = get_lang(user)
    if lang == "ru":
        text = (
            "*Команды*\n\n"
            "*/payout* – оформить выплаты. Флаги: `amb-Name` (только твои), `amb-all` (без фильтра)\n"
            "*/bloggers* – блогеры, реквизиты, управление\n"
            "*/import_bloggers* – массовое добавление из списка или файла\n"
            "*/history [ник]* – история выплат\n"
            "*/reformat* – сменить формат готового блока\n"
            "*/settings* – язык, имя менеджера, формат вывода\n"
            "*/cancel* – отменить текущее действие\n\n"
            "Полная инструкция: кнопка _Инструкция_ на главном экране."
        )
    else:
        text = (
            "*Commands*\n\n"
            "*/payout* – create payouts. Flags: `amb-Name` (yours only), `amb-all` (no filter)\n"
            "*/bloggers* – bloggers, payment details, management\n"
            "*/import_bloggers* – bulk import from list or file\n"
            "*/history [username]* – payout history\n"
            "*/reformat* – switch finished block format\n"
            "*/settings* – language, manager name, output format\n"
            "*/cancel* – cancel current action\n\n"
            "Full guide: _Instructions_ button on the home screen."
        )
    await update.message.reply_text(text, parse_mode="Markdown")


# --------------------------------------------------------------------------- #
# /reformat — reformat an existing payout block
# --------------------------------------------------------------------------- #
async def cmd_reformat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return ConversationHandler.END
    lang = get_lang(user)
    context.user_data["rf_user"] = user
    if lang == "ru":
        await update.message.reply_text(
            "Вставь готовый блок выплаты — однострочный или многострочный. "
            "Бот переключит формат на противоположный.\n"
            "/cancel – отмена"
        )
    else:
        await update.message.reply_text(
            "Paste a finished payout block – one-line or multiline. "
            "The bot will switch it to the opposite format.\n"
            "/cancel – cancel"
        )
    return WAIT_REFORMAT



def _split_payout_items(items_raw: str) -> list[str]:
    """
    Split oneline items by ' - ' only outside parentheses.
    'Platform (...) - Platform (...)' -> ['- Platform (...)', '- Platform (...)']
    """
    items = []
    current = ""
    depth = 0
    i = 0
    while i < len(items_raw):
        ch = items_raw[i]
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif items_raw[i:i+3] == " - " and depth == 0:
            if current.strip():
                items.append("- " + current.strip())
            current = ""
            i += 3
            continue
        else:
            current += ch
        i += 1
    if current.strip():
        items.append("- " + current.strip())
    return items



_RF_TRANSLATE = {
    # RU → EN
    "за":      {"ru": "за",     "en": "for"},
    "видео":   {"ru": "видео",  "en": "video"},
    "по":      {"ru": "по",     "en": "for"},
    "пр.":     {"ru": "пр.",    "en": "views"},
    "для":     {"ru": "для",    "en": "for"},
}

def _translate_rf_block(text: str, target_lang: str) -> str:
    """Translate payout block header between ru/en."""
    import re
    result = text

    if target_lang == "en":
        # $X для NAME за N видео по GAME: → $X for NAME for N video for GAME:
        result = re.sub(
            r"\$([\d,\.]+) для ([^з]+) за (\d+) (видео|вид\.) по ([^:]+):",
            lambda m: f"${m.group(1)} for {m.group(2).strip()} for {m.group(3)} video for {m.group(5).strip()}:",
            result
        )
        # N пр. → N views
        result = re.sub(r"(\d[\d\s]*) пр\.", lambda m: f"{m.group(1).strip()} views", result)
    else:  # ru
        # $X for NAME for N video for GAME: → $X для NAME за N видео по GAME:
        result = re.sub(
            r"\$([\d,\.]+) for ([^f]+) for (\d+) video for ([^:]+):",
            lambda m: f"${m.group(1)} для {m.group(2).strip()} за {m.group(3)} видео по {m.group(4).strip()}:",
            result
        )
        # N views → N пр.
        result = re.sub(r"(\d[\d\s]*) views", lambda m: f"{m.group(1).strip()} пр.", result)
    return result


async def reformat_got_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data.get("rf_user") or await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    raw = update.message.text.strip()

    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    if len(lines) < 2:
        await update.message.reply_text(
            "Неверный формат. Нужно минимум 2 строки."
            if lang == "ru" else
            "Invalid format. Need at least 2 lines."
        )
        return WAIT_REFORMAT

    method_line = lines[-1]

    # Detect format: multiline has lines starting with "- "
    # Oneline has everything on one line with ": - " separator
    is_multiline_input = any(l.startswith("- ") for l in lines[1:-1])

    if is_multiline_input:
        # Input is multiline — header is first line, items are "- ..." lines
        header_line = lines[0]
        item_lines = [l for l in lines[1:-1] if l.startswith("- ")]

        # Build oneline: header + all items joined with " "
        items_joined = " ".join(item_lines)
        oneline = f"{header_line} {items_joined}\n{method_line}"

        # Build multiline: header + each item on own line
        multiline = header_line + "\n" + "\n".join(item_lines) + "\n" + method_line

    else:
        # Input is oneline — everything (except method) on one or merged lines
        payout_body = " ".join(lines[:-1])

        # Split into header and items on ": - "
        if ": - " in payout_body:
            header_part, items_raw = payout_body.split(": - ", 1)
            # Items separated by " - "
            item_list = _split_payout_items(items_raw)
            items_str = " ".join(item_list)
            oneline = f"{header_part}: {items_str}\n{method_line}"
            multiline = header_part + ":\n" + "\n".join(item_list) + "\n" + method_line
        else:
            # Unrecognised — return as-is with toggle disabled
            oneline = payout_body + "\n" + method_line
            multiline = oneline

    user = context.user_data.get("rf_user") or await get_user(update.effective_user.id)
    output_mode = (user.get("output_mode") or "block") if user else "block"
    # Build alternate-language versions
    alt_lang = "en" if lang == "ru" else "ru"
    oneline_alt   = _translate_rf_block(oneline, alt_lang)
    multiline_alt = _translate_rf_block(multiline, alt_lang)

    context.user_data["rf_oneline"]       = oneline
    context.user_data["rf_multiline"]     = multiline
    context.user_data["rf_oneline_alt"]   = oneline_alt
    context.user_data["rf_multiline_alt"] = multiline_alt
    context.user_data["rf_fmt"]           = "oneline"
    context.user_data["rf_lang"]          = lang
    context.user_data["rf_output_mode"]   = output_mode

    context.user_data["_rf_just_done"] = True
    await _send_rf_block(update.message, oneline, "oneline", lang, output_mode=output_mode)
    return ConversationHandler.END


async def _send_rf_block(target, text: str, fmt: str, lang: str, edit: bool = False,
                          output_mode: str = "text", show_lang_toggle: bool = True):
    toggle_fmt = ("↕ Многострочный" if fmt == "oneline" else "↕ Однострочный") if lang == "ru" \
                 else ("↕ Multiline" if fmt == "oneline" else "↕ One line")
    _other_lang = "en" if lang == "ru" else "ru"
    lang_btn = InlineKeyboardButton(
        "🇬🇧 EN" if _other_lang == "en" else "🇷🇺 RU",
        callback_data=f"rf_lang:{_other_lang}"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(toggle_fmt, callback_data=f"rf_toggle:{fmt}"),
        lang_btn,
    ]])
    if output_mode == "block":
        escaped = text.replace("`", "'")
        msg = f"```\n{escaped}\n```"
        parse_mode = "Markdown"
    else:
        msg = text
        parse_mode = None
    if edit:
        await target.edit_message_text(msg, reply_markup=keyboard, parse_mode=parse_mode)
    else:
        await target.reply_text(msg, reply_markup=keyboard, parse_mode=parse_mode)



async def cb_rf_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch payout block language."""
    query = update.callback_query
    await query.answer()
    target_lang = query.data.split(":")[1]
    fmt = context.user_data.get("rf_fmt", "oneline")
    output_mode = context.user_data.get("rf_output_mode", "text")

    if fmt == "oneline":
        key = "rf_oneline" if target_lang == context.user_data.get("rf_lang") else "rf_oneline_alt"
    else:
        key = "rf_multiline" if target_lang == context.user_data.get("rf_lang") else "rf_multiline_alt"

    # Swap: if switching to alt, use alt keys; update rf_lang
    if target_lang != context.user_data.get("rf_lang"):
        # Swap main and alt
        context.user_data["rf_oneline"],   context.user_data["rf_oneline_alt"]   = \
            context.user_data.get("rf_oneline_alt", ""), context.user_data.get("rf_oneline", "")
        context.user_data["rf_multiline"], context.user_data["rf_multiline_alt"] = \
            context.user_data.get("rf_multiline_alt", ""), context.user_data.get("rf_multiline", "")
        context.user_data["rf_lang"] = target_lang

    text = context.user_data.get(
        "rf_multiline" if fmt == "multiline" else "rf_oneline", ""
    )
    if not text:
        await query.answer("Data expired." if target_lang == "en" else "Данные устарели.", show_alert=True)
        return

    await _send_rf_block(query, text, fmt, target_lang, edit=True, output_mode=output_mode)


async def cb_rf_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    current_fmt = query.data.split(":")[1]
    new_fmt = "multiline" if current_fmt == "oneline" else "oneline"

    text = context.user_data.get(
        "rf_multiline" if new_fmt == "multiline" else "rf_oneline", ""
    )
    if not text:
        await query.answer("Data expired." if lang == "en" else "Данные устарели.", show_alert=True)
        return

    context.user_data["rf_fmt"] = new_fmt
    output_mode = context.user_data.get("rf_output_mode", "text")
    current_lang = context.user_data.get("rf_lang", lang)
    await _send_rf_block(query, text, new_fmt, current_lang, edit=True, output_mode=output_mode)


# --------------------------------------------------------------------------- #
# /cancel
# --------------------------------------------------------------------------- #
async def cmd_cancel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data.clear()
    await update.message.reply_text(
        "Отменено." if lang == "ru" else "Cancelled."
    )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Fallback for plain text outside conversations
# --------------------------------------------------------------------------- #

_NAV_CMDS = {"bloggers", "payout", "start", "help", "settings", "cancel",
             "reformat", "import_bloggers", "sync_sheets", "export", "admin"}

async def _universal_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any active conversation when user sends a command or nav button."""
    # Clear all known conversation state keys
    for k in list(context.user_data.keys()):
        if k.startswith(("ib_", "rf_", "bm_", "qm_", "pd_", "awaiting_",
                         "mgr_", "known", "unknown", "skipped", "user",
                         "payout_raw", "no_method_queue", "all_payout_texts")):
            context.user_data.pop(k, None)
    return ConversationHandler.END


async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Don't interfere with active import conversation
    if context.user_data.get("ib_user") is not None:
        return

    # Skip if reformat just completed (PTB re-dispatches to group 1)
    if context.user_data.pop("_rf_just_done", False):
        return

    # Password input for manager selection
    if context.user_data.get("awaiting_mgr_pw"):
        text = (update.message.text or "").strip()
        _nav = {"🏠 Home", "🏠 Главная", "💸 Payout", "💸 Выплата",
                "👥 Bloggers", "👥 Блогеры", "⚙️ Settings", "⚙️ Настройки"}
        if text in _nav or any(text.startswith(e) for e in ("🏠", "💸", "👥", "⚙️")):
            context.user_data.pop("awaiting_mgr_pw", None)
        else:
            handled = await handle_mgr_pw_input(update, context)
            if handled:
                return

    # Don't interfere with active blogger menu text input
    if context.user_data.get("bm_action"):
        # Nav button pressed during text input — let handle_text_input in group 2 handle it
        return
    if context.user_data.get("awaiting_mgr"):
        text = (update.message.text or "").strip()
        # Nav keyboard buttons must not be treated as manager name input
        _nav = {"🏠 Home", "🏠 Главная", "🏠", "💸 Payout", "💸 Выплата",
                "👥 Bloggers", "👥 Блогеры", "⚙️ Settings", "⚙️ Настройки"}
        if text in _nav or any(text.startswith(e) for e in ("🏠", "💸", "👥", "⚙️")):
            context.user_data.pop("awaiting_mgr", None)
            # Fall through to normal nav handling below
        elif text.lower() in {"/skip", "skip"}:
            await handle_mgr_skip(update, context)
            return
        else:
            await handle_mgr_input(update, context)
            return

    text = (update.message.text or "").strip()

    # 💸 button is handled by ConversationHandler entry_point — ignore here
    if text in {"💸", "💸 Payout", "💸 Выплата"} or text.startswith("💸"):
        return
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    role = user["role"] if user else "manager"

    # Handle persistent keyboard buttons
    home_labels   = {"🏠 Home", "🏠 Главная", "🏠"}
    payout_labels = set()  # 💸 handled as ConversationHandler entry_point
    bloggers_labels = {"👥 Bloggers", "👥 Блогеры", "👥", "👥 Bloggers"}
    settings_labels = {"⚙️ Settings", "⚙️ Настройки", "⚙️"}

    tg = update.effective_user
    if text in home_labels:
        await update.message.reply_text(
            _start_text(tg.first_name, lang),
            reply_markup=_main_keyboard(lang, role),
        )
        return
    # 💸 button is handled as ConversationHandler entry_point in payout.py
    if text in bloggers_labels:
        from handlers.blogger import cmd_bloggers
        await cmd_bloggers(update, context)
        return
    if text in settings_labels:
        await cmd_settings(update, context)
        return

    # Silently ignore table data
    if "\t" in text or ("http" in text and ("  " in text or "\u00a0" in text)):
        return

    if lang == "ru":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💸 Выплата", callback_data="start_payout"),
             InlineKeyboardButton("🏠 Главная", callback_data="show_start")],
        ])
        await update.message.reply_text("Не понял команду.", reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💸 Payout", callback_data="start_payout"),
             InlineKeyboardButton("🏠 Home",   callback_data="show_start")],
        ])
        await update.message.reply_text("Command not recognized.", reply_markup=kb)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_start_handlers(app):
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("settings", cmd_settings))

    # /reformat conversation
    app.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("reformat", cmd_reformat),
            CallbackQueryHandler(cb_rf_again, pattern=r"^rf_again$"),
        ],
        states={
            WAIT_REFORMAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reformat_got_block),
            ],
        },
        fallbacks=[
            CommandHandler("cancel",          cmd_cancel_start),
            CommandHandler("payout",           _universal_cancel),
            CommandHandler("bloggers",          _universal_cancel),
            CommandHandler("start",             _universal_cancel),
            CommandHandler("help",              _universal_cancel),
            CommandHandler("settings",          _universal_cancel),
            CommandHandler("import_bloggers",   _universal_cancel),
            MessageHandler(filters.Regex(r"^(🏠|💸|👥|⚙️)"), _universal_cancel),
        ],
        conversation_timeout=300,
    ))

    app.add_handler(CallbackQueryHandler(cb_show_help,       pattern=r"^show_help$"))
    app.add_handler(CallbackQueryHandler(cb_show_settings,   pattern=r"^show_settings$"))
    app.add_handler(CallbackQueryHandler(cb_show_admin_hint, pattern=r"^show_admin_hint$"))
    app.add_handler(CallbackQueryHandler(cb_show_start,      pattern=r"^show_start$"))
    app.add_handler(CallbackQueryHandler(cb_show_more,        pattern=r"^show_more$"))
    app.add_handler(CallbackQueryHandler(cb_sync_my_sheet,   pattern=r"^sync_my_sheet$"))
    app.add_handler(CallbackQueryHandler(cb_sync_sheet_run,   pattern=r"^sync_sheet:"))
    app.add_handler(CallbackQueryHandler(cb_set_lang,        pattern=r"^set_lang:"))
    app.add_handler(CallbackQueryHandler(cb_set_mgr,             pattern=r"^set_mgr$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_output_mode,  pattern=r"^toggle_output_mode$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_default_fmt,   pattern=r"^toggle_default_fmt$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_filter, pattern=r"^toggle_(filter|warn)_(paid|pending)$"))
    app.add_handler(CallbackQueryHandler(cb_mgr_clear,    pattern=r"^mgr_clear$"))
    app.add_handler(CallbackQueryHandler(cb_mgr_pick,     pattern=r"^mgr_pick:"))
    app.add_handler(CallbackQueryHandler(cb_mgr_manual,   pattern=r"^mgr_manual$"))
    app.add_handler(CallbackQueryHandler(cb_rf_lang,    pattern=r"^rf_lang:"))
    app.add_handler(CallbackQueryHandler(cb_rf_toggle,       pattern=r"^rf_toggle:"))

    # Manager name input (outside conversation, triggered by awaiting_mgr flag)