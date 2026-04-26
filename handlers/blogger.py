"""
Blogger and payment method management.

Commands:
  /add_blogger [name_or_prefix]
  /bloggers
  /add_method [name_or_prefix]
  /add_note [name_or_prefix]
  /edit_method
"""

from __future__ import annotations
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters,
)

from database.queries import (
    get_user, add_blogger, get_bloggers_for_manager, get_bloggers_without_method,
    get_blogger_by_name, get_blogger_by_id, add_payment_method, get_active_methods,
    get_all_methods, get_method_by_id, deactivate_method, deactivate_blogger,
    reactivate_method, update_method_address, set_primary_method,
    search_bloggers_by_prefix, update_blogger_notes, db_log,
    METHOD_TYPES, METHOD_LABELS,
)
from services.logger import log_info
from handlers.common import get_user_or_reject, get_lang, nav_keyboard

# States
(
    AB_NAME, AB_NOTES,
    AM_BLOGGER, AM_TYPE, AM_ADDRESS,
    EM_BLOGGER, EM_METHOD, EM_ACTION, EM_NEW_ADDRESS,
    AN_BLOGGER, AN_TEXT,
    DEL_CONFIRM,
) = range(12)

CANCEL_TEXT = {"ru": "Отменено.", "en": "Cancelled."}


# --------------------------------------------------------------------------- #
# Prefix search helper
# --------------------------------------------------------------------------- #
async def _resolve_blogger(
    name_or_prefix: str, manager_id: int, lang: str
) -> tuple[dict | None, list[dict]]:
    """
    Returns (exact_match, candidates).
    If exact match found -> (blogger, []).
    If prefix matches multiple -> (None, [list]).
    If nothing found -> (None, []).
    """
    exact = await get_blogger_by_name(name_or_prefix, manager_id)
    if exact:
        return exact, []
    candidates = await search_bloggers_by_prefix(name_or_prefix, manager_id)
    if len(candidates) == 1:
        return candidates[0], []
    return None, candidates


async def _send_prefix_choice(
    target, candidates: list[dict], cb_prefix: str, lang: str, text: str
) -> None:
    buttons = [
        [InlineKeyboardButton(b["name"], callback_data=f"{cb_prefix}:{b['id']}:{b['name']}")]
        for b in candidates
    ]
    await target.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


# --------------------------------------------------------------------------- #
# /bloggers
# --------------------------------------------------------------------------- #
async def cmd_bloggers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return
    lang = get_lang(user)
    bloggers = await get_bloggers_for_manager(user["id"])

    if not bloggers:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "➕ Добавить блогера" if lang == "ru" else "➕ Add blogger",
                callback_data="bl_add_blogger"
            )
        ]])
        await update.message.reply_text(
            "У вас нет блогеров." if lang == "ru" else "You have no bloggers.",
            reply_markup=keyboard,
        )
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
            no_method = "нет методов оплаты" if lang == "ru" else "no payment methods"
            lines.append(f"• {b['name']} — {no_method}")
        if b.get("notes"):
            lines[-1] += f"\n  📝 {b['notes']}"

    header = f"Ваши блогеры ({len(bloggers)}):" if lang == "ru" else f"Your bloggers ({len(bloggers)}):"

    # Bottom action buttons
    if lang == "ru":
        action_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➕ Блогер",   callback_data="bl_add_blogger"),
                InlineKeyboardButton("💳 Метод",    callback_data="bl_add_method"),
            ],
            [
                InlineKeyboardButton("📝 Заметка",  callback_data="bl_add_note"),
                InlineKeyboardButton("🗑 Управление", callback_data="bl_manage"),
            ],
        ])
    else:
        action_keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➕ Blogger",  callback_data="bl_add_blogger"),
                InlineKeyboardButton("💳 Method",   callback_data="bl_add_method"),
            ],
            [
                InlineKeyboardButton("📝 Note",     callback_data="bl_add_note"),
                InlineKeyboardButton("🗑 Manage",   callback_data="bl_manage"),
            ],
        ])

    await update.message.reply_text(
        header + "\n\n" + "\n\n".join(lines),
        reply_markup=action_keyboard,
    )


# Inline button shortcuts from /bloggers
async def cb_bl_add_blogger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data["user"] = user
    await query.message.reply_text(
        "Введите никнейм блогера:\n/cancel — отмена"
        if lang == "ru" else
        "Enter blogger username:\n/cancel — cancel"
    )
    return AB_NAME


async def cb_bl_add_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # Simulate /add_method
    update.callback_query.data = ""
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data["user"] = user
    await _show_blogger_list_for_method(query.message, user, lang)
    return AM_BLOGGER


async def cb_bl_add_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data["user"] = user
    bloggers = await get_bloggers_for_manager(user["id"])
    if not bloggers:
        await query.answer("No bloggers." if lang == "en" else "Нет блогеров.", show_alert=True)
        return
    buttons = [
        [InlineKeyboardButton(b["name"], callback_data=f"an_pick:{b['id']}:{b['name']}")]
        for b in bloggers
    ]
    await query.message.reply_text(
        "Выберите блогера для заметки:" if lang == "ru" else "Select blogger for note:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return AN_BLOGGER


async def cb_bl_manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show manage menu: delete blogger or delete method."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data["user"] = user
    bloggers = await get_bloggers_for_manager(user["id"])
    if not bloggers:
        await query.answer("No bloggers." if lang == "en" else "Нет блогеров.", show_alert=True)
        return
    buttons = [
        [InlineKeyboardButton(b["name"], callback_data=f"mg_pick:{b['id']}:{b['name']}")]
        for b in bloggers
    ]
    text = "Выберите блогера для управления:" if lang == "ru" else "Select blogger to manage:"
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_mg_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show manage options for a specific blogger."""
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    _, bid, bname = query.data.split(":", 2)
    blogger_id = int(bid)
    methods = await get_active_methods(blogger_id)

    if lang == "ru":
        buttons = [
            [InlineKeyboardButton(f"🗑 Удалить блогера «{bname}»", callback_data=f"mg_del_b:{blogger_id}:{bname}")],
        ]
        for m in methods:
            label = METHOD_LABELS.get(m["type"], m["type"])
            buttons.append([InlineKeyboardButton(
                f"🗑 Удалить метод {label}: {m['address'][:20]}...",
                callback_data=f"mg_del_m:{m['id']}:{bname}"
            )])
        buttons.append([InlineKeyboardButton("← Назад", callback_data="bl_manage")])
        text = f"Управление: {bname}"
    else:
        buttons = [
            [InlineKeyboardButton(f"🗑 Delete blogger '{bname}'", callback_data=f"mg_del_b:{blogger_id}:{bname}")],
        ]
        for m in methods:
            label = METHOD_LABELS.get(m["type"], m["type"])
            buttons.append([InlineKeyboardButton(
                f"🗑 Delete method {label}: {m['address'][:20]}...",
                callback_data=f"mg_del_m:{m['id']}:{bname}"
            )])
        buttons.append([InlineKeyboardButton("← Back", callback_data="bl_manage")])
        text = f"Manage: {bname}"

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def cb_mg_del_blogger_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    _, bid, bname = query.data.split(":", 2)
    context.user_data["mg_del_bid"] = int(bid)
    context.user_data["mg_del_bname"] = bname

    if lang == "ru":
        text = f"Удалить блогера «{bname}»?\nИстория выплат сохранится, блогер исчезнет из списков."
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Да, удалить", callback_data="mg_confirm_del_b:yes"),
            InlineKeyboardButton("Отмена",      callback_data="mg_confirm_del_b:no"),
        ]])
    else:
        text = f"Delete blogger '{bname}'?\nPayout history will be kept, blogger will disappear from lists."
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Yes, delete", callback_data="mg_confirm_del_b:yes"),
            InlineKeyboardButton("Cancel",      callback_data="mg_confirm_del_b:no"),
        ]])
    await query.edit_message_text(text, reply_markup=keyboard)


async def cb_mg_confirm_del_blogger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    action = query.data.split(":")[1]

    if action == "no":
        await query.edit_message_text("Отменено." if lang == "ru" else "Cancelled.")
        return

    bid = context.user_data.get("mg_del_bid")
    bname = context.user_data.get("mg_del_bname", "?")
    if bid:
        await deactivate_blogger(bid)
        log_info("BLOGGER_DEACTIVATED", user_id=user["telegram_id"], username=user["username"], blogger=bname)
        await db_log(user["id"], "BLOGGER_DEACTIVATED", f"blogger={bname}")
    await query.edit_message_text(
        f"Блогер «{bname}» удалён из списка." if lang == "ru"
        else f"Blogger '{bname}' removed from list.",
        reply_markup=nav_keyboard(lang),
    )


async def cb_mg_del_method_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"

    _, mid, bname = query.data.split(":", 2)
    method_id = int(mid)
    method = await get_method_by_id(method_id)
    context.user_data["mg_del_mid"] = method_id
    context.user_data["mg_del_bname"] = bname

    label = METHOD_LABELS.get(method["type"], method["type"]) if method else "?"
    addr  = method["address"] if method else "?"

    if lang == "ru":
        text = f"Отключить метод {label}: {addr} у блогера «{bname}»?"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Да, отключить", callback_data="mg_confirm_del_m:yes"),
            InlineKeyboardButton("Отмена",        callback_data="mg_confirm_del_m:no"),
        ]])
    else:
        text = f"Disable method {label}: {addr} for '{bname}'?"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Yes, disable", callback_data="mg_confirm_del_m:yes"),
            InlineKeyboardButton("Cancel",       callback_data="mg_confirm_del_m:no"),
        ]])
    await query.edit_message_text(text, reply_markup=keyboard)


async def cb_mg_confirm_del_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    action = query.data.split(":")[1]

    if action == "no":
        await query.edit_message_text("Отменено." if lang == "ru" else "Cancelled.")
        return

    mid = context.user_data.get("mg_del_mid")
    bname = context.user_data.get("mg_del_bname", "?")
    if mid:
        await deactivate_method(mid)
        log_info("METHOD_DEACTIVATED", user_id=user["telegram_id"], username=user["username"], blogger=bname, method_id=mid)
        await db_log(user["id"], "METHOD_DEACTIVATED", f"blogger={bname} | method_id={mid}")
    await query.edit_message_text(
        f"Метод отключён." if lang == "ru" else "Method disabled.",
        reply_markup=nav_keyboard(lang),
    )


# --------------------------------------------------------------------------- #
# /add_blogger [name_or_prefix]
# --------------------------------------------------------------------------- #
async def cmd_add_blogger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return ConversationHandler.END
    lang = get_lang(user)
    context.user_data["user"] = user

    # If name passed as arg — prefill
    arg = " ".join(context.args).strip() if context.args else ""
    if arg:
        context.user_data["ab_name"] = arg
        await update.message.reply_text(
            f"Добавить блогера «{arg}»? Отправьте /skip для подтверждения без заметки,\n"
            f"или введите заметку.\n/cancel — отмена"
            if lang == "ru" else
            f"Add blogger '{arg}'? Send /skip to confirm without note,\n"
            f"or enter a note.\n/cancel — cancel"
        )
        return AB_NOTES

    await update.message.reply_text(
        "Введите никнейм блогера (как в таблице):\n/cancel — отмена"
        if lang == "ru" else
        "Enter blogger username (as in the spreadsheet):\n/cancel — cancel"
    )
    return AB_NAME


async def ab_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data["user"]
    lang = get_lang(user)
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Пустое имя." if lang == "ru" else "Empty name.")
        return AB_NAME
    context.user_data["ab_name"] = name
    await update.message.reply_text(
        f"Блогер: {name}\n/skip — добавить без заметки\nИли введите заметку:"
        if lang == "ru" else
        f"Blogger: {name}\n/skip — add without note\nOr enter a note:"
    )
    return AB_NOTES


async def ab_skip_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _save_blogger(update, context, None)


async def ab_got_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    notes = update.message.text.strip()
    return await _save_blogger(update, context, notes)


async def _save_blogger(update, context, notes):
    user = context.user_data["user"]
    lang = get_lang(user)
    name = context.user_data["ab_name"]
    result = await add_blogger(name, user["id"], notes)
    if result is None:
        await update.message.reply_text(
            f"Блогер «{name}» уже есть." if lang == "ru" else f"Blogger '{name}' already exists."
        )
        context.user_data.clear()
        return ConversationHandler.END
    log_info("BLOGGER_ADDED", user_id=user["telegram_id"], username=user["username"], blogger=name)
    await db_log(user["id"], "BLOGGER_ADDED", f"blogger={name}")
    await update.message.reply_text(
        f"Блогер «{name}» добавлен.\nДобавьте способ оплаты: /add_method"
        if lang == "ru" else
        f"Blogger '{name}' added.\nAdd payment method: /add_method",
        reply_markup=nav_keyboard(lang),
    )
    context.user_data.clear()
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /add_note [name_or_prefix]
# --------------------------------------------------------------------------- #
async def cmd_add_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return ConversationHandler.END
    lang = get_lang(user)
    context.user_data["user"] = user

    arg = " ".join(context.args).strip() if context.args else ""
    if arg:
        exact, candidates = await _resolve_blogger(arg, user["id"], lang)
        if exact:
            context.user_data["an_blogger_id"] = exact["id"]
            context.user_data["an_blogger_name"] = exact["name"]
            await update.message.reply_text(
                f"Введите заметку для {exact['name']}:\n/cancel — отмена"
                if lang == "ru" else
                f"Enter note for {exact['name']}:\n/cancel — cancel"
            )
            return AN_TEXT
        elif candidates:
            await _send_prefix_choice(
                update.message, candidates, "an_pick", lang,
                f"Несколько совпадений для «{arg}»:" if lang == "ru" else f"Multiple matches for '{arg}':"
            )
            return AN_BLOGGER
        else:
            await update.message.reply_text(
                f"Блогер «{arg}» не найден." if lang == "ru" else f"Blogger '{arg}' not found."
            )
            return ConversationHandler.END

    bloggers = await get_bloggers_for_manager(user["id"])
    if not bloggers:
        await update.message.reply_text("Нет блогеров." if lang == "ru" else "No bloggers.")
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(b["name"], callback_data=f"an_pick:{b['id']}:{b['name']}")]
        for b in bloggers
    ]
    await update.message.reply_text(
        "Выберите блогера:" if lang == "ru" else "Select blogger:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return AN_BLOGGER


async def an_got_blogger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = context.user_data["user"]
    lang = get_lang(user)
    _, bid, bname = query.data.split(":", 2)
    context.user_data["an_blogger_id"] = int(bid)
    context.user_data["an_blogger_name"] = bname
    await query.edit_message_text(
        f"Введите заметку для {bname}:\n/cancel — отмена"
        if lang == "ru" else
        f"Enter note for {bname}:\n/cancel — cancel"
    )
    return AN_TEXT


async def an_got_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data["user"]
    lang = get_lang(user)
    note = update.message.text.strip()
    bid = context.user_data["an_blogger_id"]
    bname = context.user_data["an_blogger_name"]
    await update_blogger_notes(bid, note)
    log_info("NOTE_UPDATED", user_id=user["telegram_id"], username=user["username"], blogger=bname)
    await update.message.reply_text(
        f"Заметка для «{bname}» обновлена." if lang == "ru" else f"Note for '{bname}' updated.",
        reply_markup=nav_keyboard(lang),
    )
    context.user_data.clear()
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /add_method [name_or_prefix]
# --------------------------------------------------------------------------- #
async def cmd_add_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return ConversationHandler.END
    lang = get_lang(user)
    context.user_data["user"] = user

    arg = " ".join(context.args).strip() if context.args else ""
    if arg:
        exact, candidates = await _resolve_blogger(arg, user["id"], lang)
        if exact:
            context.user_data["am_blogger_id"]   = exact["id"]
            context.user_data["am_blogger_name"] = exact["name"]
            return await _show_type_selection(update.message, context, exact["name"], lang)
        elif candidates:
            await _send_prefix_choice(
                update.message, candidates, "am_blogger", lang,
                f"Несколько совпадений для «{arg}»:" if lang == "ru" else f"Multiple matches for '{arg}':"
            )
            return AM_BLOGGER
        else:
            await update.message.reply_text(
                f"Блогер «{arg}» не найден." if lang == "ru" else f"Blogger '{arg}' not found."
            )
            return ConversationHandler.END

    return await _show_blogger_list_for_method(update.message, user, lang)


async def _show_blogger_list_for_method(target, user, lang):
    bloggers = await get_bloggers_without_method(user["id"])
    show_all = not bloggers
    if show_all:
        bloggers = await get_bloggers_for_manager(user["id"])
    if not bloggers:
        await target.reply_text(
            "Сначала добавьте блогера: /add_blogger" if lang == "ru"
            else "Add a blogger first: /add_blogger"
        )
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(b["name"], callback_data=f"am_blogger:{b['id']}:{b['name']}")]
        for b in bloggers
    ]
    if not show_all:
        buttons.append([InlineKeyboardButton(
            "Показать всех" if lang == "ru" else "Show all",
            callback_data="am_blogger:showall"
        )])
    header = ("Блогеры без метода оплаты:" if not show_all else "Все блогеры:") if lang == "ru" \
             else ("Bloggers without payment method:" if not show_all else "All bloggers:")
    await target.reply_text(header, reply_markup=InlineKeyboardMarkup(buttons))
    return AM_BLOGGER


async def am_got_blogger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = context.user_data["user"]
    lang = get_lang(user)

    if query.data == "am_blogger:showall":
        bloggers = await get_bloggers_for_manager(user["id"])
        buttons = [
            [InlineKeyboardButton(b["name"], callback_data=f"am_blogger:{b['id']}:{b['name']}")]
            for b in bloggers
        ]
        await query.edit_message_text(
            "Все блогеры:" if lang == "ru" else "All bloggers:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return AM_BLOGGER

    _, bid, bname = query.data.split(":", 2)
    context.user_data["am_blogger_id"]   = int(bid)
    context.user_data["am_blogger_name"] = bname
    await _show_type_selection(query.message, context, bname, lang, edit=True, query=query)
    return AM_TYPE


async def _show_type_selection(target, context, blogger_name, lang, edit=False, query=None):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(METHOD_LABELS[t], callback_data=f"am_type:{t}")]
        for t in METHOD_TYPES
    ])
    text = f"Блогер: {blogger_name}\nВыберите тип:" if lang == "ru" \
           else f"Blogger: {blogger_name}\nSelect type:"
    if edit and query:
        await query.edit_message_text(text, reply_markup=keyboard)
    else:
        await target.reply_text(text, reply_markup=keyboard)
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
        "usdt-trc20": {"ru": "Адрес кошелька TRC20",                               "en": "TRC20 wallet address"},
        "paypal":     {"ru": "Email PayPal",                                        "en": "PayPal email"},
    }
    hint = hints.get(method_type, {}).get(lang, "")
    label = METHOD_LABELS.get(method_type, method_type)
    await query.edit_message_text(
        f"Тип: {label}\n{hint}\n/cancel — отмена" if lang == "ru"
        else f"Type: {label}\n{hint}\n/cancel — cancel"
    )
    return AM_ADDRESS


async def am_got_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data["user"]
    lang = get_lang(user)
    address = update.message.text.strip()
    if not address:
        await update.message.reply_text("Пустой адрес." if lang == "ru" else "Empty address.")
        return AM_ADDRESS
    context.user_data["am_address"] = address
    return await _save_method(update, context, lang)


async def _save_method(update, context, lang):
    user = context.user_data["user"]
    bid   = context.user_data["am_blogger_id"]
    bname = context.user_data["am_blogger_name"]
    mtype = context.user_data["am_type"]
    addr  = context.user_data["am_address"]
    await add_payment_method(bid, mtype, addr)
    log_info("METHOD_ADDED", user_id=user["telegram_id"], username=user["username"], blogger=bname, type=mtype)
    await db_log(user["id"], "METHOD_ADDED", f"blogger={bname} | type={mtype}")
    type_label = METHOD_LABELS.get(mtype, mtype)
    await update.message.reply_text(
        f"Способ оплаты добавлен:\n{bname} — {type_label}: {addr}"
        if lang == "ru" else
        f"Payment method added:\n{bname} — {type_label}: {addr}",
        reply_markup=nav_keyboard(lang),
    )
    context.user_data.clear()
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /edit_method
# --------------------------------------------------------------------------- #
async def cmd_edit_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return ConversationHandler.END
    lang = get_lang(user)
    context.user_data["user"] = user
    bloggers = await get_bloggers_for_manager(user["id"])
    if not bloggers:
        await update.message.reply_text("Нет блогеров." if lang == "ru" else "No bloggers.")
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(b["name"], callback_data=f"em_blogger:{b['id']}:{b['name']}")]
        for b in bloggers
    ]
    await update.message.reply_text(
        "Выберите блогера:" if lang == "ru" else "Select blogger:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return EM_BLOGGER


async def em_got_blogger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = context.user_data["user"]
    lang = get_lang(user)
    _, bid, bname = query.data.split(":", 2)
    methods = await get_all_methods(int(bid))
    if not methods:
        await query.edit_message_text("Нет методов." if lang == "ru" else "No methods.")
        return ConversationHandler.END
    buttons = []
    for m in methods:
        status = "" if m["is_active"] else (" [откл]" if lang == "ru" else " [off]")
        primary = " ★" if m.get("is_primary") else ""
        label = METHOD_LABELS.get(m["type"], m["type"])
        buttons.append([InlineKeyboardButton(
            f"{label}: {m['address']}{primary}{status}",
            callback_data=f"em_method:{m['id']}"
        )])
    await query.edit_message_text(
        f"Методы {bname}:" if lang == "ru" else f"Methods for {bname}:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return EM_METHOD


async def em_got_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = context.user_data["user"]
    lang = get_lang(user)
    method_id = int(query.data.split(":")[1])
    context.user_data["em_method_id"] = method_id
    method = await get_method_by_id(method_id)
    label = METHOD_LABELS.get(method["type"], method["type"])
    is_primary = method.get("is_primary", 0)
    toggle = ("🔴 Отключить" if method["is_active"] else "🟢 Включить") if lang == "ru" \
             else ("🔴 Disable" if method["is_active"] else "🟢 Enable")
    primary_btn = ("✅ Основной" if is_primary else "⭐ Сделать основным") if lang == "ru" \
                  else ("✅ Primary" if is_primary else "⭐ Set as primary")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏ Изменить адрес" if lang == "ru" else "✏ Edit address", callback_data="em_action:edit")],
        [InlineKeyboardButton(primary_btn, callback_data="em_action:primary")],
        [InlineKeyboardButton(toggle, callback_data="em_action:toggle")],
        [InlineKeyboardButton("← Назад" if lang == "ru" else "← Back", callback_data="em_action:back")],
    ])
    status = ("активен" if method["is_active"] else "отключён") if lang == "ru" \
             else ("active" if method["is_active"] else "disabled")
    await query.edit_message_text(
        f"{label}: {method['address']}\nСтатус: {status}" if lang == "ru"
        else f"{label}: {method['address']}\nStatus: {status}",
        reply_markup=keyboard
    )
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
        await db_log(user["id"], "METHOD_TOGGLED", f"method_id={method_id}")
        await query.edit_message_text(msg, reply_markup=nav_keyboard(lang))
        return ConversationHandler.END

    elif action == "primary":
        await set_primary_method(method_id, method["blogger_id"])
        await db_log(user["id"], "METHOD_SET_PRIMARY", f"method_id={method_id}")
        await query.edit_message_text(
            "Метод установлен как основной." if lang == "ru" else "Method set as primary.",
            reply_markup=nav_keyboard(lang),
        )
        return ConversationHandler.END

    elif action == "edit":
        await query.edit_message_text(
            "Введите новый адрес:\n/cancel — отмена" if lang == "ru"
            else "Enter new address:\n/cancel — cancel"
        )
        return EM_NEW_ADDRESS

    elif action == "back":
        await query.edit_message_text("Отменено." if lang == "ru" else "Cancelled.")
        return ConversationHandler.END


async def em_got_new_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = context.user_data["user"]
    lang = get_lang(user)
    method_id = context.user_data["em_method_id"]
    new_address = update.message.text.strip()
    if not new_address:
        await update.message.reply_text("Пустой адрес." if lang == "ru" else "Empty address.")
        return EM_NEW_ADDRESS
    await update_method_address(method_id, new_address)
    await db_log(user["id"], "METHOD_UPDATED", f"method_id={method_id}")
    await update.message.reply_text(
        "Адрес обновлён." if lang == "ru" else "Address updated.",
        reply_markup=nav_keyboard(lang),
    )
    context.user_data.clear()
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /cancel
# --------------------------------------------------------------------------- #
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data.clear()
    await update.message.reply_text(CANCEL_TEXT[lang])
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_blogger_handlers(app):
    cancel_handler = CommandHandler("cancel", cmd_cancel)

    # /add_blogger
    app.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("add_blogger", cmd_add_blogger),
            CallbackQueryHandler(cb_bl_add_blogger, pattern=r"^bl_add_blogger$"),
        ],
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
        entry_points=[
            CommandHandler("add_method", cmd_add_method),
            CallbackQueryHandler(cb_bl_add_method, pattern=r"^bl_add_method$"),
        ],
        states={
            AM_BLOGGER:  [CallbackQueryHandler(am_got_blogger, pattern=r"^am_blogger:")],
            AM_TYPE:     [CallbackQueryHandler(am_got_type,    pattern=r"^am_type:")],
            AM_ADDRESS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, am_got_address)],
        },
        fallbacks=[cancel_handler],
        conversation_timeout=300,
        per_message=False,
    ))

    # /add_note
    app.add_handler(ConversationHandler(
        entry_points=[
            CommandHandler("add_note", cmd_add_note),
            CallbackQueryHandler(cb_bl_add_note, pattern=r"^bl_add_note$"),
        ],
        states={
            AN_BLOGGER: [CallbackQueryHandler(an_got_blogger, pattern=r"^an_pick:")],
            AN_TEXT:    [MessageHandler(filters.TEXT & ~filters.COMMAND, an_got_text)],
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

    # Manage callbacks (outside conversations)
    app.add_handler(CommandHandler("bloggers", cmd_bloggers))
    app.add_handler(CommandHandler("add_note", cmd_add_note))
    app.add_handler(CallbackQueryHandler(cb_bl_manage,                pattern=r"^bl_manage$"))
    app.add_handler(CallbackQueryHandler(cb_mg_pick,                  pattern=r"^mg_pick:"))
    app.add_handler(CallbackQueryHandler(cb_mg_del_blogger_confirm,   pattern=r"^mg_del_b:"))
    app.add_handler(CallbackQueryHandler(cb_mg_confirm_del_blogger,   pattern=r"^mg_confirm_del_b:"))
    app.add_handler(CallbackQueryHandler(cb_mg_del_method_confirm,    pattern=r"^mg_del_m:"))
    app.add_handler(CallbackQueryHandler(cb_mg_confirm_del_method,    pattern=r"^mg_confirm_del_m:"))