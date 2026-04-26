"""
Handlers: /start, /help, /settings, /reformat, fallback
"""

from __future__ import annotations
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters,
)

from database.queries import upsert_user, get_user, set_user_lang, set_manager_filter, set_output_mode, set_default_fmt, db_log
from services.logger import log_info
from handlers.common import get_user_or_reject, get_lang
from config import ADMIN_ID

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
            "Бот для формирования выплат блогерам StarPets.\n\n"
            "Как работать:\n"
            "1. Задайте своё имя менеджера в /settings\n"
            "2. Добавьте блогеров через /add_blogger\n"
            "3. Добавьте им методы оплаты через /add_method\n"
            "4. Скопируйте строки из таблицы и отправьте /payout"
        )
    return (
        f"Hi, {name}!\n\n"
        "StarPets payout bot.\n\n"
        "Getting started:\n"
        "1. Set your manager name in /settings\n"
        "2. Add bloggers via /add_blogger\n"
        "3. Add payment methods via /add_method\n"
        "4. Copy rows from the spreadsheet and send /payout"
    )


def _main_keyboard(lang: str, role: str = "manager") -> InlineKeyboardMarkup:
    if lang == "ru":
        buttons = [
            [InlineKeyboardButton("📋 Инструкция", callback_data="show_help")],
            [InlineKeyboardButton("⚙️ Настройки",  callback_data="show_settings")],
        ]
        if role == "admin":
            buttons.append([InlineKeyboardButton("🔧 Админ", callback_data="show_admin_hint")])
    else:
        buttons = [
            [InlineKeyboardButton("📋 Instructions", callback_data="show_help")],
            [InlineKeyboardButton("⚙️ Settings",     callback_data="show_settings")],
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
            "Команды и флаги:\n\n"

            "/payout\n"
            "  Вставьте строки из таблицы следующим сообщением.\n"
            "  Флаги (можно комбинировать):\n"
            "  • /payout amb-John — только блогеры менеджера John\n"
            "  • /payout amb-all  — все блогеры без фильтра\n"
            "  Если имя менеджера задано в настройках — фильтр применяется автоматически.\n\n"

            "/reformat\n"
            "  Переформатировать готовый блок выплаты.\n"
            "  Вставьте блок в формате:\n"
            "  $X для Y за N видео по Z: - Platform (...) - ...\n"
            "  Method – address\n\n"

            "/add_blogger — добавить блогера\n"
            "/bloggers — список блогеров и методов оплаты\n"
            "/add_method — добавить метод оплаты\n"
            "/edit_method — изменить метод / сделать основным\n"
            "/settings — язык, имя менеджера\n"
            "/cancel — отменить текущее действие\n\n"

            "Платформы: YouTube, YouTube Shorts, TikTok, Instagram, Facebook\n"
            "Методы: Site, USDT-TRC20, PayPal\n\n"

            "Под каждым блоком выплаты:\n"
            "[ ↕ Многострочный ]  [ 💳 Поменять метод ]"
        )
    else:
        text = (
            "Commands and flags:\n\n"

            "/payout\n"
            "  Paste spreadsheet rows as the next message.\n"
            "  Flags (combinable):\n"
            "  • /payout amb-John — only bloggers of manager John\n"
            "  • /payout amb-all  — all bloggers, ignore filter\n"
            "  If manager name is set in settings — filter applies automatically.\n\n"

            "/reformat\n"
            "  Reformat an existing payout block.\n"
            "  Paste a block in this exact format:\n"
            "  $X for Y for N videos on Z: - Platform (...) - ...\n"
            "  Method – address\n\n"

            "/add_blogger — add a blogger\n"
            "/bloggers — list bloggers and payment methods\n"
            "/add_method — add a payment method\n"
            "/edit_method — edit method / set as primary\n"
            "/settings — language, manager name\n"
            "/cancel — cancel current action\n\n"

            "Platforms: YouTube, YouTube Shorts, TikTok, Instagram, Facebook\n"
            "Methods: Site, USDT-TRC20, PayPal\n\n"

            "Buttons under each payout block:\n"
            "[ ↕ Multiline ]  [ 💳 Change method ]"
        )

    await query.edit_message_text(text, reply_markup=_back_keyboard(lang))


# --------------------------------------------------------------------------- #
# Inline button: Settings
# --------------------------------------------------------------------------- #
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
    if lang == "ru":
        return (
            f"Настройки:\n\n"
            f"Язык: {cur_lang}\n"
            f"Имя менеджера (фильтр): {mgr}\n"
            f"Оформление: {out_label}\n"
            f"Формат выплаты: {fmt_label}"
        )
    return (
        f"Settings:\n\n"
        f"Language: {cur_lang}\n"
        f"Manager name (filter): {mgr}\n"
        f"Output style: {out_label}\n"
        f"Payout format: {fmt_label}"
    )


def _settings_keyboard(user: dict, lang: str) -> InlineKeyboardMarkup:
    out_mode = user.get("output_mode") or "block"
    def_fmt  = user.get("default_fmt") or "oneline"
    if lang == "ru":
        # Buttons show current value, clicking toggles
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
            [InlineKeyboardButton("📥 Импорт блогеров", callback_data="go_import")],
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
        [InlineKeyboardButton("📥 Import bloggers", callback_data="go_import")],
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


async def cb_set_mgr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data["awaiting_mgr"] = True
    if lang == "ru":
        await query.edit_message_text(
            "Введите ваше имя менеджера как оно написано в столбце Manager таблицы.\n"
            "Например: John\n\n"
            "/skip — убрать фильтр (показывать всех)"
        )
    else:
        await query.edit_message_text(
            "Enter your manager name exactly as it appears in the Manager column.\n"
            "Example: John\n\n"
            "/skip — clear filter (show all)"
        )


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
    await update.message.reply_text(
        "Фильтр сброшен. /payout будет показывать всех блогеров."
        if lang == "ru" else
        "Filter cleared. /payout will show all bloggers."
    )


# --------------------------------------------------------------------------- #
# Inline button: Admin hint
# --------------------------------------------------------------------------- #
async def cb_show_admin_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    if lang == "ru":
        text = (
            "Команды администратора:\n\n"
            "/admin — пользователи, блогеры, последние выплаты\n"
            "/admin_logs — последние 30 записей лога\n"
            "/admin_search <имя> — поиск блогера по всей базе\n"
            "/backup — создать бэкап\n"
            "/restore — восстановить БД из файла"
        )
    else:
        text = (
            "Admin commands:\n\n"
            "/admin — users, bloggers, recent payouts\n"
            "/admin_logs — last 30 log entries\n"
            "/admin_search <name> — search blogger across all managers\n"
            "/backup — create backup\n"
            "/restore — restore DB from file"
        )
    await query.edit_message_text(text, reply_markup=_back_keyboard(lang))


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
            "Для импорта блогеров используйте /import_bloggers\n\n"
            "Формат (tab-разделитель):\n"
            "имя\tSite_ID\tUSDT-TRC20\tPayPal\tосновной\n\n"
            "Пример:\n"
            "braba7x.ff1\t690779e7e54ed806\t\t\tsite\n"
            "taypk7\t\tTLBwE3pdG9UY...\t\tusdt-trc20\n\n"
            "Пустые ячейки — пропускать. Хотя бы один метод обязателен."
        )
    else:
        text = (
            "To import bloggers use /import_bloggers\n\n"
            "Format (tab-separated):\n"
            "name\tSite_ID\tUSDT-TRC20\tPayPal\tprimary\n\n"
            "Example:\n"
            "braba7x.ff1\t690779e7e54ed806\t\t\tsite\n"
            "taypk7\t\tTLBwE3pdG9UY...\t\tusdt-trc20\n\n"
            "Empty cells — skip. At least one method required."
        )
    await query.edit_message_text(text, reply_markup=_back_keyboard(lang))


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
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return
    lang = get_lang(user)
    if lang == "ru":
        text = (
            "Команды:\n\n"
            "/payout [amb-Name|amb-all] — оформить выплату\n"
            "/reformat — переформатировать готовый блок\n"
            "/import_bloggers — импорт блогеров из списка или файла\n"
            "/add_blogger — добавить блогера\n"
            "/bloggers — список блогеров\n"
            "/add_method — добавить метод оплаты\n"
            "/edit_method — изменить метод / сделать основным\n"
            "/settings — язык, имя менеджера\n"
            "/cancel — отменить действие\n\n"
            "Подробнее — кнопка Инструкция в /start"
        )
    else:
        text = (
            "Commands:\n\n"
            "/payout [amb-Name|amb-all] — create payout\n"
            "/reformat — reformat existing payout block\n"
            "/import_bloggers — bulk import bloggers from list or file\n"
            "/add_blogger — add a blogger\n"
            "/bloggers — list bloggers\n"
            "/add_method — add payment method\n"
            "/edit_method — edit method / set as primary\n"
            "/settings — language, manager name\n"
            "/cancel — cancel action\n\n"
            "Full instructions — Instructions button in /start"
        )
    await update.message.reply_text(text)


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
            "Вставьте блок выплаты для переформатирования.\n"
            "Формат строго:\n"
            "$X для Y за N видео по Z: - Platform (...) - ...\n"
            "Method – address\n\n"
            "/cancel — отмена"
        )
    else:
        await update.message.reply_text(
            "Paste the payout block to reformat.\n"
            "Strict format:\n"
            "$X for Y for N videos on Z: - Platform (...) - ...\n"
            "Method – address\n\n"
            "/cancel — cancel"
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

    context.user_data["rf_oneline"]   = oneline
    context.user_data["rf_multiline"] = multiline
    context.user_data["rf_fmt"]       = "oneline"

    await _send_rf_block(update.message, oneline, "oneline", lang)
    return ConversationHandler.END


async def _send_rf_block(target, text: str, fmt: str, lang: str, edit: bool = False):
    escaped = text.replace("`", "'")
    msg = f"```\n{escaped}\n```"
    toggle = ("↕ Многострочный" if fmt == "oneline" else "↕ Однострочный") if lang == "ru" \
             else ("↕ Multiline" if fmt == "oneline" else "↕ One line")
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(toggle, callback_data=f"rf_toggle:{fmt}"),
    ]])
    if edit:
        await target.edit_message_text(msg, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await target.reply_text(msg, reply_markup=keyboard, parse_mode="Markdown")


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
    await _send_rf_block(query, text, new_fmt, lang, edit=True)


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
async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_mgr"):
        text = (update.message.text or "").strip()
        if text.lower() in {"/skip", "skip"}:
            await handle_mgr_skip(update, context)
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

    await update.message.reply_text(
        "Use /payout to create a payout or /help for instructions."
        if lang == "en" else
        "Используйте /payout для выплаты или /help для справки."
    )


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_start_handlers(app):
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("settings", cmd_settings))

    # /reformat conversation
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("reformat", cmd_reformat)],
        states={
            WAIT_REFORMAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reformat_got_block),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel_start)],
        conversation_timeout=300,
    ))

    app.add_handler(CallbackQueryHandler(cb_show_help,       pattern=r"^show_help$"))
    app.add_handler(CallbackQueryHandler(cb_show_settings,   pattern=r"^show_settings$"))
    app.add_handler(CallbackQueryHandler(cb_show_admin_hint, pattern=r"^show_admin_hint$"))
    app.add_handler(CallbackQueryHandler(cb_show_start,      pattern=r"^show_start$"))
    app.add_handler(CallbackQueryHandler(cb_set_lang,        pattern=r"^set_lang:"))
    app.add_handler(CallbackQueryHandler(cb_set_mgr,             pattern=r"^set_mgr$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_output_mode,  pattern=r"^toggle_output_mode$"))
    app.add_handler(CallbackQueryHandler(cb_toggle_default_fmt,   pattern=r"^toggle_default_fmt$"))
    app.add_handler(CallbackQueryHandler(cb_go_import,            pattern=r"^go_import$"))
    app.add_handler(CallbackQueryHandler(cb_rf_toggle,       pattern=r"^rf_toggle:"))

    # Manager name input (outside conversation, triggered by awaiting_mgr flag)