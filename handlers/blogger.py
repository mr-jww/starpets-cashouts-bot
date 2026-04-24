"""
Handlers for blogger and payment method management.

Commands:
  /add_blogger  — add a blogger (with optional notes)
  /bloggers     — list bloggers with their payment methods
  /add_method   — add payment method to a blogger
  /edit_method  — edit or deactivate a payment method

All use ConversationHandler for multi-step dialogs.
"""

from __future__ import annotations
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters,
)

from database.queries import (
    get_user, add_blogger, get_bloggers_for_manager, get_bloggers_without_method,
    get_blogger_by_name, add_payment_method, get_active_methods,
    get_all_methods, get_method_by_id, deactivate_method,
    reactivate_method, update_method_address, set_primary_method, db_log,
    METHOD_TYPES, METHOD_LABELS,
)
from services.logger import log_info, log_warn
from handlers.common import get_user_or_reject, get_lang

# ConversationHandler states
(
    AB_NAME, AB_NOTES,
    AM_BLOGGER, AM_TYPE, AM_ADDRESS,
    EM_BLOGGER, EM_METHOD, EM_ACTION, EM_NEW_ADDRESS,
) = range(9)

CANCEL_TEXT = {
    "ru": "Отменено.",
    "en": "Cancelled.",
}


# =========================================================================== #
# /add_blogger
# =========================================================================== #

async def cmd_add_blogger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return ConversationHandler.END
    lang = get_lang(user)
    context.user_data["user"] = user

    if lang == "ru":
        await update.message.reply_text(
            "Введите никнейм блогера (как в таблице):\n/cancel — отмена"
        )
    else:
        await update.message.reply_text(
            "Enter the blogger's username (as in the spreadsheet):\n/cancel — cancel"
        )
    return AB_NAME


async def ab_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data["user"]
    lang = get_lang(user)
    name = update.message.text.strip()

    if not name:
        await update.message.reply_text("Пустое имя. Попробуйте ещё раз:" if lang == "ru" else "Empty name. Try again:")
        return AB_NAME

    context.user_data["ab_name"] = name

    if lang == "ru":
        await update.message.reply_text(
            f"Блогер: {name}\nДобавить заметку? (необязательно)\nОтправьте текст или /skip"
        )
    else:
        await update.message.reply_text(
            f"Blogger: {name}\nAdd a note? (optional)\nSend text or /skip"
        )
    return AB_NOTES


async def ab_got_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data["user"]
    lang = get_lang(user)
    notes = update.message.text.strip()
    return await _save_blogger(update, context, notes, lang)


async def ab_skip_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data["user"]
    lang = get_lang(user)
    return await _save_blogger(update, context, None, lang)


async def _save_blogger(update, context, notes, lang):
    user = context.user_data["user"]
    name = context.user_data["ab_name"]

    result = await add_blogger(name, user["id"], notes)
    if result is None:
        if lang == "ru":
            await update.message.reply_text(f"Блогер «{name}» уже есть в вашей базе.")
        else:
            await update.message.reply_text(f"Blogger '{name}' already exists in your list.")
        return ConversationHandler.END

    log_info("BLOGGER_ADDED", user_id=user["telegram_id"], username=user["username"], blogger=name)
    await db_log(user["id"], "BLOGGER_ADDED", f"blogger={name}")

    if lang == "ru":
        await update.message.reply_text(
            f"Блогер «{name}» добавлен.\n"
            "Теперь добавьте способ оплаты командой /add_method"
        )
    else:
        await update.message.reply_text(
            f"Blogger '{name}' added.\n"
            "Now add a payment method with /add_method"
        )
    return ConversationHandler.END


# =========================================================================== #
# /bloggers
# =========================================================================== #

async def cmd_bloggers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return
    lang = get_lang(user)

    bloggers = await get_bloggers_for_manager(user["id"])
    if not bloggers:
        if lang == "ru":
            await update.message.reply_text("У вас нет блогеров. Добавьте: /add_blogger")
        else:
            await update.message.reply_text("You have no bloggers. Add one: /add_blogger")
        return

    lines = []
    for b in bloggers:
        methods = await get_active_methods(b["id"])
        if methods:
            method_strs = [
                f"  {METHOD_LABELS.get(m['type'], m['type'])}: {m['address']}"
                + (" ★" if m.get("is_primary") else "")
                for m in methods
            ]
            lines.append(f"• {b['name']}\n" + "\n".join(method_strs))
        else:
            no_method = "нет способов оплаты" if lang == "ru" else "no payment methods"
            lines.append(f"• {b['name']} — {no_method}")

    header = f"Ваши блогеры ({len(bloggers)}):" if lang == "ru" else f"Your bloggers ({len(bloggers)}):"
    await update.message.reply_text(header + "\n\n" + "\n\n".join(lines))


# =========================================================================== #
# /add_method
# =========================================================================== #

async def cmd_add_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return ConversationHandler.END
    lang = get_lang(user)
    context.user_data["user"] = user

    # If blogger name passed as argument — skip list, go straight to type selection
    arg_name = " ".join(context.args).strip() if context.args else ""
    if arg_name:
        db_b = await get_blogger_by_name(arg_name, user["id"])
        if db_b is None:
            await update.message.reply_text(
                f"Блогер «{arg_name}» не найден. Проверьте имя или добавьте: /add_blogger"
                if lang == "ru" else
                f"Blogger '{arg_name}' not found. Check the name or add: /add_blogger"
            )
            return ConversationHandler.END
        context.user_data["am_blogger_id"]   = db_b["id"]
        context.user_data["am_blogger_name"] = db_b["name"]
        return await _show_type_selection(update, context, db_b["name"], lang)

    # Show bloggers without active methods first; fall back to all if none
    bloggers = await get_bloggers_without_method(user["id"])
    show_all = False
    if not bloggers:
        bloggers = await get_bloggers_for_manager(user["id"])
        show_all = True

    if not bloggers:
        await update.message.reply_text(
            "Сначала добавьте блогера: /add_blogger" if lang == "ru"
            else "Add a blogger first: /add_blogger"
        )
        return ConversationHandler.END

    buttons = [
        [InlineKeyboardButton(b["name"], callback_data=f"am_blogger:{b['id']}:{b['name']}")]
        for b in bloggers
    ]
    # Add "Show all" button if currently showing only those without methods
    if not show_all:
        all_btn_label = "Показать всех" if lang == "ru" else "Show all"
        buttons.append([InlineKeyboardButton(all_btn_label, callback_data="am_blogger:showall")])

    keyboard = InlineKeyboardMarkup(buttons)
    if lang == "ru":
        header = "Блогеры без метода оплаты:" if not show_all else "Все блогеры:"
    else:
        header = "Bloggers without payment method:" if not show_all else "All bloggers:"
    await update.message.reply_text(header, reply_markup=keyboard)
    return AM_BLOGGER



async def _show_type_selection(update, context, blogger_name: str, lang: str):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(METHOD_LABELS[t], callback_data=f"am_type:{t}")]
        for t in METHOD_TYPES
    ])
    text = f"Блогер: {blogger_name}\nВыберите тип:" if lang == "ru" else f"Blogger: {blogger_name}\nSelect type:"
    await update.effective_message.reply_text(text, reply_markup=keyboard)
    return AM_TYPE


async def am_got_blogger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = context.user_data["user"]
    lang = get_lang(user)

    # "Show all" button
    if query.data == "am_blogger:showall":
        bloggers = await get_bloggers_for_manager(user["id"])
        buttons = [
            [InlineKeyboardButton(b["name"], callback_data=f"am_blogger:{b['id']}:{b['name']}")]
            for b in bloggers
        ]
        header = "Все блогеры:" if lang == "ru" else "All bloggers:"
        await query.edit_message_text(header, reply_markup=InlineKeyboardMarkup(buttons))
        return AM_BLOGGER

    _, blogger_id, blogger_name = query.data.split(":", 2)
    context.user_data["am_blogger_id"] = int(blogger_id)
    context.user_data["am_blogger_name"] = blogger_name

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(METHOD_LABELS[t], callback_data=f"am_type:{t}")]
        for t in METHOD_TYPES
    ])
    text = f"Блогер: {blogger_name}\nВыберите тип:" if lang == "ru" else f"Blogger: {blogger_name}\nSelect type:"
    await query.edit_message_text(text, reply_markup=keyboard)
    return AM_TYPE


async def am_got_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = context.user_data["user"]
    lang = get_lang(user)

    method_type = query.data.split(":")[1]
    context.user_data["am_type"] = method_type

    hints = {
        "site":       {"ru": "Profile ID (например: 690779e7e54ed806f3d730b4)",    "en": "Profile ID (e.g. 690779e7e54ed806f3d730b4)"},
        "usdt-trc20": {"ru": "Адрес кошелька TRC20 (например: TLBwE3...)",          "en": "TRC20 wallet address (e.g. TLBwE3...)"},
        "paypal":     {"ru": "Email PayPal (например: user@gmail.com)",             "en": "PayPal email (e.g. user@gmail.com)"},
    }
    hint = hints.get(method_type, {}).get(lang, "Address")
    label = METHOD_LABELS.get(method_type, method_type)

    text = f"Тип: {label}\nВведите адрес:\n{hint}" if lang == "ru" else f"Type: {label}\nEnter address:\n{hint}"
    await query.edit_message_text(text)
    return AM_ADDRESS


async def am_got_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data["user"]
    lang = get_lang(user)
    address = update.message.text.strip()

    if not address:
        await update.message.reply_text("Пустой адрес. Попробуйте ещё раз:" if lang == "ru" else "Empty address. Try again:")
        return AM_ADDRESS

    context.user_data["am_address"] = address
    return await _save_method(update, context, lang)





async def _save_method(update, context, lang):
    user = context.user_data["user"]
    blogger_id   = context.user_data["am_blogger_id"]
    blogger_name = context.user_data["am_blogger_name"]
    method_type  = context.user_data["am_type"]
    address      = context.user_data["am_address"]

    await add_payment_method(blogger_id, method_type, address)
    log_info("METHOD_ADDED", user_id=user["telegram_id"], username=user["username"],
             blogger=blogger_name, type=method_type)
    await db_log(user["id"], "METHOD_ADDED", f"blogger={blogger_name} | type={method_type}")

    type_label = METHOD_LABELS.get(method_type, method_type)
    if lang == "ru":
        await update.message.reply_text(
            f"Способ оплаты добавлен:\n{blogger_name} — {type_label}: {address}"
        )
    else:
        await update.message.reply_text(
            f"Payment method added:\n{blogger_name} — {type_label}: {address}"
        )
    return ConversationHandler.END


# =========================================================================== #
# /edit_method
# =========================================================================== #

async def cmd_edit_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return ConversationHandler.END
    lang = get_lang(user)
    context.user_data["user"] = user

    bloggers = await get_bloggers_for_manager(user["id"])
    if not bloggers:
        msg = "Нет блогеров." if lang == "ru" else "No bloggers."
        await update.message.reply_text(msg)
        return ConversationHandler.END

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(b["name"], callback_data=f"em_blogger:{b['id']}:{b['name']}")]
        for b in bloggers
    ])
    text = "Выберите блогера:" if lang == "ru" else "Select blogger:"
    await update.message.reply_text(text, reply_markup=keyboard)
    return EM_BLOGGER


async def em_got_blogger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = context.user_data["user"]
    lang = get_lang(user)

    _, blogger_id, blogger_name = query.data.split(":", 2)
    context.user_data["em_blogger_id"] = int(blogger_id)
    context.user_data["em_blogger_name"] = blogger_name

    methods = await get_all_methods(int(blogger_id))
    if not methods:
        msg = "Нет способов оплаты." if lang == "ru" else "No payment methods."
        await query.edit_message_text(msg)
        return ConversationHandler.END

    buttons = []
    for m in methods:
        status = "" if m["is_active"] else " [откл]" if lang == "ru" else " [off]"
        label = METHOD_LABELS.get(m["type"], m["type"])
        text  = f"{label}: {m['address']}{status}"
        buttons.append([InlineKeyboardButton(text, callback_data=f"em_method:{m['id']}")])

    keyboard = InlineKeyboardMarkup(buttons)
    header = f"Методы {blogger_name}:" if lang == "ru" else f"Methods for {blogger_name}:"
    await query.edit_message_text(header, reply_markup=keyboard)
    return EM_METHOD


async def em_got_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = context.user_data["user"]
    lang = get_lang(user)

    method_id = int(query.data.split(":")[1])
    context.user_data["em_method_id"] = method_id

    method = await get_method_by_id(method_id)
    type_label = METHOD_LABELS.get(method["type"], method["type"])
    status = ("активен" if method["is_active"] else "отключён") if lang == "ru" else ("active" if method["is_active"] else "disabled")

    if lang == "ru":
        toggle_text = "Отключить" if method["is_active"] else "Включить"
    else:
        toggle_text = "Disable" if method["is_active"] else "Enable"

    is_primary = method.get("is_primary", 0)
    primary_label = ("✅ Основной" if is_primary else "⭐ Сделать основным") if lang == "ru"                     else ("✅ Primary" if is_primary else "⭐ Set as primary")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏ Изменить адрес" if lang == "ru" else "✏ Edit address",
                              callback_data="em_action:edit")],
        [InlineKeyboardButton(primary_label, callback_data="em_action:primary")],
        [InlineKeyboardButton(f"{'🔴' if method['is_active'] else '🟢'} {toggle_text}",
                              callback_data="em_action:toggle")],
        [InlineKeyboardButton("← Назад" if lang == "ru" else "← Back",
                              callback_data="em_action:back")],
    ])

    text = (
        f"{type_label}: {method['address']}\n"
        f"Статус: {status}"
        if lang == "ru" else
        f"{type_label}: {method['address']}\n"
        f"Status: {status}"
    )
    await query.edit_message_text(text, reply_markup=keyboard)
    return EM_ACTION


async def em_got_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = context.user_data["user"]
    lang = get_lang(user)

    action = query.data.split(":")[1]
    method_id = context.user_data["em_method_id"]
    method = await get_method_by_id(method_id)

    if action == "toggle":
        if method["is_active"]:
            await deactivate_method(method_id)
            msg = "Метод отключён." if lang == "ru" else "Method disabled."
        else:
            await reactivate_method(method_id)
            msg = "Метод включён." if lang == "ru" else "Method enabled."
        await db_log(user["id"], "METHOD_TOGGLED", f"method_id={method_id} | active={not method['is_active']}")
        log_info("METHOD_TOGGLED", user_id=user["telegram_id"], username=user["username"], method_id=method_id)
        await query.edit_message_text(msg)
        return ConversationHandler.END

    elif action == "primary":
        method = await get_method_by_id(method_id)
        blogger_id = method["blogger_id"]
        await set_primary_method(method_id, blogger_id)
        await db_log(user["id"], "METHOD_SET_PRIMARY", f"method_id={method_id}")
        log_info("METHOD_SET_PRIMARY", user_id=user["telegram_id"], username=user["username"], method_id=method_id)
        await query.edit_message_text("Метод установлен как основной." if lang == "ru" else "Method set as primary.")
        return ConversationHandler.END

    elif action == "edit":
        await query.edit_message_text("Введите новый адрес:" if lang == "ru" else "Enter new address:")
        return EM_NEW_ADDRESS

    elif action == "back":
        await query.edit_message_text(CANCEL_TEXT[lang])
        return ConversationHandler.END


async def em_got_new_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data["user"]
    lang = get_lang(user)
    method_id = context.user_data["em_method_id"]
    new_address = update.message.text.strip()

    if not new_address:
        await update.message.reply_text("Пустой адрес. Попробуйте:" if lang == "ru" else "Empty address. Try again:")
        return EM_NEW_ADDRESS

    await update_method_address(method_id, new_address)
    await db_log(user["id"], "METHOD_UPDATED", f"method_id={method_id}")
    log_info("METHOD_UPDATED", user_id=user["telegram_id"], username=user["username"], method_id=method_id)

    await update.message.reply_text("Адрес обновлён." if lang == "ru" else "Address updated.")
    return ConversationHandler.END


# =========================================================================== #
# /cancel — universal
# =========================================================================== #

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "ru"
    context.user_data.clear()
    await update.message.reply_text(CANCEL_TEXT[lang])
    return ConversationHandler.END


# =========================================================================== #
# Registration
# =========================================================================== #

def register_blogger_handlers(app):
    cancel_handler = CommandHandler("cancel", cmd_cancel)

    # /add_blogger
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_blogger", cmd_add_blogger)],
        states={
            AB_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, ab_got_name)],
            AB_NOTES: [
                CommandHandler("skip", ab_skip_notes),
                MessageHandler(filters.TEXT & ~filters.COMMAND, ab_got_notes),
            ],
        },
        fallbacks=[cancel_handler],
        conversation_timeout=300,
    ))

    # /add_method
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add_method", cmd_add_method)],
        states={
            AM_BLOGGER:  [CallbackQueryHandler(am_got_blogger, pattern=r"^am_blogger:")],
            AM_TYPE:     [CallbackQueryHandler(am_got_type,    pattern=r"^am_type:")],
            AM_ADDRESS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, am_got_address)],
        },
        fallbacks=[cancel_handler],
        conversation_timeout=300,
        per_message=False,
    ))

    # /edit_method
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("edit_method", cmd_edit_method)],
        states={
            EM_BLOGGER:     [CallbackQueryHandler(em_got_blogger, pattern=r"^em_blogger:")],
            EM_METHOD:      [CallbackQueryHandler(em_got_method,  pattern=r"^em_method:")],
            EM_ACTION:      [CallbackQueryHandler(em_got_action,  pattern=r"^em_action:")],
            EM_NEW_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, em_got_new_address)],
        },
        fallbacks=[cancel_handler],
        conversation_timeout=300,
        per_message=False,
    ))

    app.add_handler(CommandHandler("bloggers", cmd_bloggers))