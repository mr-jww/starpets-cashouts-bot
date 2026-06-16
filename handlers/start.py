"""
Handlers: /start, /help, /settings, /reformat, fallback
"""

from __future__ import annotations
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters,
)

from database.queries import (
    set_show_all_bloggers, get_show_all_bloggers, upsert_user, get_user, set_user_lang, set_manager_filter,
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

    if _is_new_user(user) and role != "admin":
        await update.message.reply_text(
            _onboarding_text(tg.first_name, lang),
            reply_markup=_onboarding_keyboard(lang),
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            _start_text(tg.first_name, lang),
            reply_markup=_persistent_keyboard(lang, role),
            parse_mode="Markdown",
        )



def _md_escape(text: str) -> str:
    """Escape Markdown v1 special characters in user-provided strings."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _is_new_user(user: dict) -> bool:
    """True if user has never set a manager name."""
    return not user.get("manager_filter")


def _onboarding_text(name: str, lang: str) -> str:
    if lang == "ru":
        return (
            f"Добро пожаловать, {_md_escape(name)}!\n\n"
            "Этот бот помогает оформлять выплаты амбассадорам StarPets. "
            "Вы вставляете строки из таблицы — бот считает суммы, "
            "подбирает реквизиты и формирует готовые блоки для отправки.\n\n"
            "*Первый шаг: укажите своё имя.*\n"
            "Без этого бот не сможет фильтровать строки по вашему листу. "
            "Нажмите кнопку ниже и выберите своё имя из списка."
        )
    return (
        f"Welcome, {_md_escape(name)}!\n\n"
        "This bot handles payouts for StarPets ambassadors. "
        "You paste rows from the spreadsheet — the bot calculates totals, "
        "finds the right payment details and produces ready-made blocks.\n\n"
        "*First step: set your manager name.*\n"
        "Without it the bot cannot filter rows by your sheet. "
        "Tap the button below and select your name from the list."
    )


def _onboarding_keyboard(lang: str) -> InlineKeyboardMarkup:
    if lang == "ru":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👤 Выбрать имя", callback_data="set_mgr")],
            [InlineKeyboardButton("📋 Инструкция", callback_data="show_help")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Set my name", callback_data="set_mgr")],
        [InlineKeyboardButton("📋 Instructions", callback_data="show_help")],
    ])


def _start_text(name: str, lang: str) -> str:
    if lang == "ru":
        return (
            f"Привет, {_md_escape(name)}!\n\n"
            "Этот бот помогает оформлять выплаты амбассадорам StarPets.\n\n"
            "Принцип простой: ты вставляешь строки из таблицы, "
            "бот сам разбирает блогеров, считает суммы и формирует готовые блоки для отправки. "
            "Реквизиты подставляются автоматически из базы данных.\n\n"
            "*Первые шаги*\n"
            "1. Откройте _Настройки_ и выберите своё имя менеджера.\n"
            "2. Добавьте блогеров через кнопку _Блогер_ или импорт из таблицы.\n"
            "3. Укажите каждому метод оплаты.\n"
            "4. Нажмите _Заказать выплату_ и вставьте строки из таблицы."
        )
    return (
        f"Hi, {_md_escape(name)}!\n\n"
        "This bot handles payouts for StarPets ambassadors.\n\n"
        "The idea is simple: you paste rows from the spreadsheet, "
        "the bot parses each blogger, totals up the amounts and produces ready-made payout blocks. "
        "Payment details are pulled automatically from the database.\n\n"
        "*Getting started*\n"
        "1. Open _Settings_ and select your manager name.\n"
        "2. Add bloggers using the _Blogger_ button or import from the spreadsheet.\n"
        "3. Set a payment method for each blogger.\n"
        "4. Tap _Create payout_ and paste rows from the spreadsheet."
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
            "*Как пользоваться ботом*\n\n"
            "*Выплаты*\n"
            "Основная команда – /payout. Скопируйте строки из таблицы и вставьте следующим сообщением. "
            "Бот разберёт каждого блогера, сложит суммы по видео и выдаст готовые блоки.\n"
            "Если в настройках указано ваше имя, строки фильтруются по нему автоматически. "
            "Чтобы обработать конкретного менеджера вручную: `/payout amb-Ambassador`, "
            "все строки без фильтра: `/payout amb-all`.\n\n"
            "*База блогеров*\n"
                        "/bloggers – список ваших блогеров. Нажмите на любого, чтобы открыть карточку: "
            "там можно добавить или изменить метод оплаты, оставить заметку, посмотреть историю выплат.\n\n"
            "*Добавление блогеров*\n"
            "Одного – кнопка *Блогер* на главном экране, затем введите никнейм.\n"
            "Нескольких сразу – **Ещё** → *Импорт блогеров*. "
            "Можно вставить список или прикрепить файл .txt. "
            "Формат каждой строки (поля разделены табуляцией):\n"
            "`nickname\tSite ID\tUSDT-TRC20\tPayPal\tосновной`\n"
            "Пустые ячейки пропускаются, хотя бы одно поле оплаты должно быть заполнено.\n\n"
            "*Синхронизация с таблицей*\n"
            "Если ваши блогеры ведутся в Google Sheets, откройте *Ещё* → *Синхр. с таблицей*. "
            "Бот прочитает ваш лист и добавит новых блогеров в базу.\n\n"
            "*Переформат*\n"
            "/reformat или **Ещё** → *Переформат* – вставьте готовый блок выплаты, "
            "и бот переключит его между однострочным и многострочным форматом. "
            "Можно также переключить язык блока: кнопка EN/RU под сообщением.\n\n"
            "*История выплат*\n"
            "/history nickname – все выплаты конкретному блогеру в хронологии.\n\n"
            "*Настройки*\n"
            "/settings – язык интерфейса, имя менеджера, формат вывода по умолчанию.\n\n"
            "Поддерживаемые платформы: YouTube, YouTube Shorts, TikTok, Instagram, Facebook.\n"
            "Методы оплаты: Site, USDT-TRC20, PayPal."
        )
    else:
        text = (
            "*How to use the bot*\n\n"
            "*Payouts*\n"
            "The main command is /payout. Copy rows from the spreadsheet and paste them as the next "
            "message. The bot will parse each blogger, sum up the video amounts and produce ready-made "
            "payout blocks.\n"
            "If your manager name is set in settings, rows are filtered by it automatically. "
            "To process a specific manager manually: `/payout amb-Ambassador`, "
            "all rows without a filter: `/payout amb-all`.\n\n"
            "*Blogger database*\n"
            "/bloggers – your blogger list. Tap any entry to open the card: "
            "you can add or change the payment method, leave a note or view payout history.\n\n"
            "*Adding bloggers*\n"
            "One blogger – use the *Blogger* button on the main screen, then enter the username.\n"
            "Multiple at once – **More** → *Import bloggers*. "
            "You can paste a list or attach a .txt file. "
            "Each row format (tab-separated fields):\n"
            "`nickname\tSite ID\tUSDT-TRC20\tPayPal\tprimary`\n"
            "Empty cells are skipped; at least one payment field must be filled.\n\n"
            "*Spreadsheet sync*\n"
            "If your bloggers are tracked in Google Sheets, open *More* → *Sync with sheet*. "
            "The bot will read your sheet and add any new bloggers to the database.\n\n"
            "*Reformat*\n"
                        "/reformat or *More* → *Reformat* – paste an existing payout block and the bot will "
            "switch it between one-line and multiline format. "
            "You can also switch the block language using the EN/RU button below the message.\n\n"
            "*Payout history*\n"
            "/history username – all payouts to a specific blogger in chronological order.\n\n"
            "*Settings*\n"
            "/settings – interface language, manager name, default output format.\n\n"
            "Supported platforms: YouTube, YouTube Shorts, TikTok, Instagram, Facebook.\n"
            "Payment methods: Site, USDT-TRC20, PayPal."
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
        text = "Дополнительные возможности"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Переформат",     callback_data="rf_again")],
            [InlineKeyboardButton("📥 Импорт блогеров", callback_data="go_import")],
            [InlineKeyboardButton("🔄 Синхр. с таблицей", callback_data="sync_my_sheet")],
            [InlineKeyboardButton("← Назад",           callback_data="show_start")],
        ])
    else:
        text = "More options"
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
            "Сначала укажите имя менеджера в настройках." if lang == "ru"
            else "Please set your manager name in settings first.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад" if lang == "ru" else "← Back",
                                     callback_data="show_more")
            ]])
        )
        return

    if lang == "ru":
        text = f"Синхронизация с листом {mgr_name}.\nВыберите режим:"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Только новые", callback_data="sync_sheet:new_only")],
            [InlineKeyboardButton("🔄 Полная синхронизация", callback_data="sync_sheet:full")],
            [InlineKeyboardButton("← Назад", callback_data="show_more")],
        ])
    else:
        text = f"Sync sheet {mgr_name}.\nChoose mode:"
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
            "Google Sheets не подключён. Обратитесь к администратору." if lang == "ru"
            else "Google Sheets is not connected. Please contact the administrator."
        )
        return

    await query.edit_message_text(
        f"Читаю лист {mgr_name}, подождите..." if lang == "ru"
        else f"Reading sheet {mgr_name}, please wait..."
    )

    try:
        sheets_data = read_sheets(sheet_names=[mgr_name])
        rows = sheets_data.get(mgr_name, [])
        if not rows:
            await query.message.reply_text(
                f"Лист {mgr_name} не найден или пуст." if lang == "ru"
                else f"Sheet {mgr_name} not found or is empty."
            )
        else:
            result = await sync_sheets_to_db(mgr_name, rows, user["id"], mode=mode)
            if lang == "ru":
                text = (
                    f"Синхронизация завершена. Добавлено: {len(result.added)}."
                    if not result.errors else
                    f"Синхронизация завершена. Добавлено: {len(result.added)}, не удалось обработать: {len(result.errors)}."
                )
            else:
                text = (
                    f"Sync complete. Added: {len(result.added)}."
                    if not result.errors else
                    f"Sync complete. Added: {len(result.added)}, failed to process: {len(result.errors)}."
                )
            await query.message.reply_text(text)
    except Exception as e:
        await query.message.reply_text(
            f"Не удалось выполнить синхронизацию: {e}" if lang == "ru" else f"Sync failed: {e}"
        )




async def cb_delete_all_my_bloggers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask for confirmation before deleting all bloggers."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    from database.queries import get_bloggers_for_manager
    bloggers = await get_bloggers_for_manager(user["id"])
    count = len(bloggers)
    if not count:
        await query.edit_message_text(
            "В базе нет блогеров для удаления." if lang == "ru"
            else "No bloggers to delete.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад" if lang == "ru" else "← Back",
                                     callback_data="show_settings")
            ]])
        )
        return
    if lang == "ru":
        text = (
            f"Вы уверены? Это удалит *{count} блогеров* из вашей базы в боте.\n\n"
            "Данные в таблице Google Sheets останутся нетронутыми. "
            "Блогеров можно будет снова добавить через синхронизацию или импорт."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Да, удалить всех", callback_data="confirm_delete_all_bloggers")],
            [InlineKeyboardButton("← Отмена", callback_data="show_settings")],
        ])
    else:
        text = (
            f"Are you sure? This will delete *{count} bloggers* from your database in the bot.\n\n"
            "Your Google Sheets data will not be affected. "
            "You can re-add bloggers later via sync or import."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Yes, delete all", callback_data="confirm_delete_all_bloggers")],
            [InlineKeyboardButton("← Cancel", callback_data="show_settings")],
        ])
    await query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")


async def cb_confirm_delete_all_bloggers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute deletion after confirmation."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    from database.queries import get_bloggers_for_manager
    import aiosqlite
    from database.db import DB_PATH
    bloggers = await get_bloggers_for_manager(user["id"])
    count = len(bloggers)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bloggers SET is_active = 0 WHERE manager_id = ?",
            (user["id"],)
        )
        await db.commit()
    log_info("ALL_BLOGGERS_DELETED", user_id=user["telegram_id"],
             username=user["username"], count=count)
    if lang == "ru":
        text = f"Готово. Удалено {count} блогеров из вашей базы в боте."
    else:
        text = f"Done. {count} bloggers removed from your bot database."
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("← Настройки" if lang == "ru" else "← Settings",
                                 callback_data="show_settings")
        ]])
    )


async def cb_toggle_show_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    new_val = not bool(user.get("show_all_bloggers", 0))
    await set_show_all_bloggers(update.effective_user.id, new_val)
    user = await get_user(update.effective_user.id)
    await query.edit_message_text(
        _settings_text(user, lang),
        reply_markup=_settings_keyboard(user, lang),
        parse_mode="Markdown",
    )


async def cb_show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    await _universal_cancel(update, context)
    await query.edit_message_text(
        _settings_text(user, lang),
        reply_markup=_settings_keyboard(user, lang),
        parse_mode="Markdown",
    )


def _settings_text(user: dict, lang: str) -> str:
    mgr = user.get("manager_filter") or ("не указан" if lang == "ru" else "not set")
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
            f"*Настройки*\n\n"
            f"Язык: {cur_lang}\n"
            f"Менеджер: {mgr}\n"
            f"Оформление блоков: {out_label}\n"
            f"Формат выплаты: {fmt_label}\n"
            f"Строки PAID: {'включать' if ip else 'пропускать'} / предупреждать: {'да' if wp else 'нет'}\n"
            f"Строки PENDING: {'включать' if ipe else 'пропускать'} / предупреждать: {'да' if wpe else 'нет'}"
        )
    return (
        f"*Settings*\n\n"
        f"Language: {cur_lang}\n"
        f"Manager: {mgr}\n"
        f"Output style: {out_label}\n"
        f"Payout format: {fmt_label}\n"
        f"PAID rows: {'include' if ip else 'skip'} / warn: {'yes' if wp else 'no'}\n"
        f"PENDING rows: {'include' if ipe else 'skip'} / warn: {'yes' if wpe else 'no'}"
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
            [InlineKeyboardButton(
                ("👁 Список: все импортированные" if user.get("show_all_bloggers") else "👁 Список: только мой лист"),
                callback_data="toggle_show_all"
            )],
            [InlineKeyboardButton("🗑 Удалить всех моих блогеров", callback_data="delete_all_my_bloggers")],
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
        [InlineKeyboardButton(
            ("👁 List: all imported" if user.get("show_all_bloggers") else "👁 List: my sheet only"),
            callback_data="toggle_show_all"
        )],
        [InlineKeyboardButton("🗑 Delete all my bloggers", callback_data="delete_all_my_bloggers")],
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
        parse_mode="Markdown",
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
        "Выберите имя из списка или введите вручную:"
        if lang == "ru" else
        "Select your name from the list or enter it manually:"
    )
    await query.edit_message_text(text, reply_markup=await _mgr_selection_keyboard(lang))


async def cb_mgr_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manager selected from button."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    # Switching manager identity always cancels any stuck conversation state
    await _universal_cancel(update, context)

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
            f"Выбрано: {name}\n\nВведите командный пароль для подтверждения:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад", callback_data="set_mgr")
            ]])
        )
    else:
        await query.edit_message_text(
            f"Selected: {name}\n\nEnter the team password to confirm:",
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
            "Введите своё имя точно так, как оно указано в таблице:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад", callback_data="set_mgr")
            ]])
        )
    else:
        await query.edit_message_text(
            "Enter your manager name exactly as it appears in the spreadsheet:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Back", callback_data="set_mgr")
            ]])
        )


async def _apply_mgr_name(update, context, name: str, lang: str):
    """Set manager filter and return to settings or main screen (onboarding)."""
    was_new = not (await get_user(update.effective_user.id) or {}).get("manager_filter")
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

    tg = update.effective_user
    role = "admin" if tg.id == ADMIN_ID else "manager"

    # After onboarding name selection — show main screen with a prompt
    if was_new:
        confirm_text = (
            f"Отлично, {_md_escape(tg.first_name)}! Имя установлено: *{_md_escape(name)}*\n\n"
            "Теперь добавьте блогеров и укажите им методы оплаты. "
            "После этого можно приступать к выплатам."
            if lang == "ru" else
            f"All set, {_md_escape(tg.first_name)}! Manager name: *{_md_escape(name)}*\n\n"
            "Now add your bloggers and set their payment methods. "
            "After that you can start processing payouts."
        )
        if msg_id:
            try:
                await update.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=msg_id,
                    text=confirm_text,
                    reply_markup=_main_keyboard(lang, role),
                    parse_mode="Markdown",
                )
                return
            except Exception:
                pass
        await update.effective_chat.send_message(
            confirm_text,
            reply_markup=_main_keyboard(lang, role),
            parse_mode="Markdown",
        )
        return

    if msg_id:
        try:
            await update.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=msg_id,
                text=_settings_text(user, lang),
                reply_markup=_settings_keyboard(user, lang),
                parse_mode="Markdown",
            )
            return
        except Exception:
            pass
    await update.effective_chat.send_message(
        _settings_text(user, lang),
        reply_markup=_settings_keyboard(user, lang),
        parse_mode="Markdown",
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
                        "Слишком много неверных попыток. Повторите через 10 минут."
                        if lang == "ru" else
                        "Too many incorrect attempts. Please try again in 10 minutes."
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
                    f"Неверный пароль. Осталось попыток: {remaining}."
                    if lang == "ru" else
                    f"Incorrect password. Attempts remaining: {remaining}."
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

    # Manually typed name matching a protected manager name requires the
    # team password too — closes the bypass where typing "John" skipped auth.
    matched = next(
        (n for n in MANAGER_BUTTON_ORDER if n.lower() == name.lower()), None
    )
    if matched and not (user and user.get("mgr_password")) and not context.user_data.get("team_confirmed"):
        context.user_data.pop("awaiting_mgr", None)
        context.user_data.pop("awaiting_mgr_manual", None)
        context.user_data["mgr_pending_name"] = matched
        context.user_data["awaiting_mgr_pw"] = True
        if lang == "ru":
            await update.message.reply_text(
                f"Выбрано: {matched}\n\nВведите командный пароль для подтверждения:"
            )
        else:
            await update.message.reply_text(
                f"Selected: {matched}\n\nEnter the team password to confirm:"
            )
        return

    await set_manager_filter(update.effective_user.id, name)
    context.user_data.pop("awaiting_mgr", None)
    context.user_data.pop("awaiting_mgr_manual", None)
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
        parse_mode="Markdown",
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
        parse_mode="Markdown",
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
        parse_mode="Markdown",
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
        parse_mode="Markdown",
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
        parse_mode="Markdown",
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
        "Вставьте готовый блок выплаты для переформатирования:"
        if lang == "ru" else
        "Paste the payout block you want to reformat:"
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
        parse_mode="Markdown",
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
            "Вставьте готовый блок выплаты — однострочный или многострочный. "
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
            r"\$(\d[\d,\.]*) для ([^з]+) за (\d+) (?:видео|вид\.) по ([^:]+):",
            lambda m: f"${m.group(1)} for {m.group(2).strip()} for {m.group(3)} video for {m.group(4).strip()}:",
            result
        )
        # N пр. → N views
        result = re.sub(r"(\d[\d\s]*) пр\.", lambda m: f"{m.group(1).strip()} views", result)
    else:  # ru
        # $X for NAME for N video for GAME: → $X для NAME за N видео по GAME:
        result = re.sub(
            r"\$(\d[\d,\.]*) for ([^f]+) for (\d+) video for ([^:]+):",
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
    # Block language is always RU (для/за/пр.) regardless of interface lang
    import re as _re
    block_lang = "ru" if _re.search(r" для .+ за \d+", oneline) else "en"
    alt_lang = "en" if block_lang == "ru" else "ru"
    oneline_alt   = _translate_rf_block(oneline, alt_lang)
    multiline_alt = _translate_rf_block(multiline, alt_lang)

    context.user_data["rf_oneline"]       = oneline
    context.user_data["rf_multiline"]     = multiline
    context.user_data["rf_oneline_alt"]   = oneline_alt
    context.user_data["rf_multiline_alt"] = multiline_alt
    context.user_data["rf_fmt"]           = "oneline"
    context.user_data["rf_lang"]          = block_lang
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
    for k in list(context.user_data.keys()):
        if k.startswith(("ib_", "rf_", "bm_", "qm_", "pd_", "awaiting_",
                         "mgr_", "known", "unknown", "skipped", "user",
                         "payout_raw", "no_method_queue", "all_payout_texts",
                         "chm_", "_last", "_rf", "team_")):
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
            parse_mode="Markdown",
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
        await update.message.reply_text("Не распознал команду. Воспользуйся кнопками ниже.", reply_markup=kb)
    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💸 Payout", callback_data="start_payout"),
             InlineKeyboardButton("🏠 Home",   callback_data="show_start")],
        ])
        await update.message.reply_text("Command not recognized. Use the buttons below.", reply_markup=kb)


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
            CallbackQueryHandler(_universal_cancel, pattern=r"^show_settings$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^show_start$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^show_more$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^set_mgr$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^mgr_pick:"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^mgr_manual$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^mgr_clear$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^start_payout$"),
            CallbackQueryHandler(_universal_cancel, pattern=r"^go_import$"),
        ],
        conversation_timeout=300,
    ))

    app.add_handler(CallbackQueryHandler(cb_show_help,       pattern=r"^show_help$"))
    app.add_handler(CallbackQueryHandler(cb_show_settings,   pattern=r"^show_settings$"))
    app.add_handler(CallbackQueryHandler(cb_show_admin_hint, pattern=r"^show_admin_hint$"))
    app.add_handler(CallbackQueryHandler(cb_show_start,      pattern=r"^show_start$"))
    app.add_handler(CallbackQueryHandler(cb_show_more,        pattern=r"^show_more$"))
    app.add_handler(CallbackQueryHandler(cb_sync_my_sheet,   pattern=r"^sync_my_sheet$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_show_all,           pattern=r"^toggle_show_all$"))
    app.add_handler(CallbackQueryHandler(cb_delete_all_my_bloggers,      pattern=r"^delete_all_my_bloggers$"))
    app.add_handler(CallbackQueryHandler(cb_confirm_delete_all_bloggers, pattern=r"^confirm_delete_all_bloggers$"))
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