"""
Blogger management — inline menu architecture.

All navigation happens via edit_message_text (one message, no chat spam).
Text input (address, note, name) triggers a temporary prompt message
that gets deleted after user replies.

Screen flow:
  /bloggers → screen_list
            → screen_blogger (card)
              → screen_add_method_type → prompt address → screen_blogger
              → screen_edit_note       → prompt note   → screen_blogger
              → screen_method (method card)
                → confirm set_primary  → screen_blogger
                → confirm toggle       → screen_blogger
                → prompt new_address   → screen_blogger
              → confirm delete_blogger → screen_list
              → confirm delete_method  → screen_blogger

State stored in context.user_data:
  bm_msg_id      — message id of the menu message (for delete/edit)
  bm_blogger_id  — currently selected blogger id
  bm_method_id   — currently selected method id
  bm_action      — pending text input: 'add_address'|'edit_note'|'add_name'|'edit_address'
"""

from __future__ import annotations
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    ContextTypes, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters,
)

from database.queries import (
    get_all_bloggers,
    get_recent_blogger_ids,
    get_user, add_blogger, get_bloggers_for_manager, get_bloggers_without_method,
    get_blogger_by_name, get_blogger_by_id, search_bloggers_by_prefix,
    add_payment_method, get_active_methods, get_all_methods, get_method_by_id,
    deactivate_method, reactivate_method, deactivate_blogger,
    update_method_address, set_primary_method, update_blogger_notes,
    db_log, METHOD_TYPES, METHOD_LABELS,
)
from services.logger import log_info
from handlers.common import get_user_or_reject, get_lang


# --------------------------------------------------------------------------- #
# Keyboard builders
# --------------------------------------------------------------------------- #
def _back(label: str, cb: str) -> list:
    return [InlineKeyboardButton(label, callback_data=cb)]


def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(list(rows))


# --------------------------------------------------------------------------- #
# Screen: blogger list
# --------------------------------------------------------------------------- #
PAGE_SIZE = 8  # bloggers per page




async def _build_blogger_button(b: dict, lang: str) -> InlineKeyboardButton:
    methods = await get_active_methods(b["id"])
    note      = " 📝" if b.get("notes") else ""
    no_method = " ⚠️" if not methods else ""
    return InlineKeyboardButton(
        f"{b['name']}{note}{no_method}",
        callback_data=f"bm:blogger:{b['id']}"
    )


async def screen_list(target, user: dict, lang: str,
                      edit: bool = True, page: int = 0, search: str = ""):
    """
    Blogger list with:
    - Recent bloggers section (last 8 paid)
    - Search by name prefix
    - Pagination (PAGE_SIZE per page, 2 columns)
    - Add blogger + Home buttons
    """
    show_all = bool(user.get("show_all_bloggers", 0))
    if show_all:
        all_bloggers = await get_all_bloggers()
    else:
        all_bloggers = await get_bloggers_for_manager(user["id"])
    total = len(all_bloggers)

    if not all_bloggers:
        text = "У вас нет блогеров." if lang == "ru" else "You have no bloggers."
        buttons = [[InlineKeyboardButton(
            "➕ Добавить блогера" if lang == "ru" else "➕ Add blogger",
            callback_data="bm:add_blogger_start"
        )], [InlineKeyboardButton(
            "🏠 Главная" if lang == "ru" else "🏠 Home",
            callback_data="nav_home"
        )]]
        return await _edit_or_reply(target, text, edit, InlineKeyboardMarkup(buttons))

    buttons = []

    if search:
        # Search mode
        q = search.lower()
        filtered = [b for b in all_bloggers if q in b["name"].lower()]
        if lang == "ru":
            text = f"🔍 «{search}» — {len(filtered)} блогеров из {total}:"
        else:
            text = f"🔍 «{search}» — {len(filtered)} of {total} bloggers:"

        # Single column for search results
        for b in filtered[:40]:
            methods = await get_active_methods(b["id"])
            no_method = " ⚠️" if not methods else ""
            buttons.append([InlineKeyboardButton(
                f"{b['name']}{no_method}",
                callback_data=f"bm:blogger:{b['id']}"
            )])

        # Clear search + actions
        buttons.append([
            InlineKeyboardButton("✕ Сбросить" if lang == "ru" else "✕ Clear",
                                 callback_data="bm:list:0:"),
            InlineKeyboardButton("➕ Добавить" if lang == "ru" else "➕ Add",
                                 callback_data="bm:add_blogger_start"),
        ])
        buttons.append([InlineKeyboardButton(
            "🏠 Главная" if lang == "ru" else "🏠 Home", callback_data="nav_home"
        )])
        return await _edit_or_reply(target, text, edit, InlineKeyboardMarkup(buttons))

    # Normal mode — recent + paginated list


    # Separator + pagination
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    start = page * PAGE_SIZE
    page_bloggers = all_bloggers[start:start + PAGE_SIZE]

    if total_pages > 1 or page > 0:
        for b in page_bloggers:
            methods = await get_active_methods(b["id"])
            no_method = " ⚠️" if not methods else ""
            buttons.append([InlineKeyboardButton(
                f"{b['name']}{no_method}",
                callback_data=f"bm:blogger:{b['id']}:{page}"
            )])

        # Pagination row: |« 1| ← prev | p/total | next → |last »|
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("« 1", callback_data="bm:list:0:"))
        if page > 0:
            nav.append(InlineKeyboardButton(f"← {page}", callback_data=f"bm:list:{page-1}:"))
        nav.append(InlineKeyboardButton(
            f"{page+1}/{total_pages}", callback_data="bm:noop"
        ))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(f"{page+2} →", callback_data=f"bm:list:{page+1}:"))
        if page < total_pages - 2:
            nav.append(InlineKeyboardButton(f"{total_pages} »", callback_data=f"bm:list:{total_pages-1}:"))
        buttons.append(nav)
    else:
        # All fit on one page — single column
        for b in all_bloggers:
            methods = await get_active_methods(b["id"])
            no_method = " ⚠️" if not methods else ""
            buttons.append([InlineKeyboardButton(
                f"{b['name']}{no_method}",
                callback_data=f"bm:blogger:{b['id']}:0"
            )])

    # Action buttons
    buttons.append([
        InlineKeyboardButton("🔍 Найти" if lang == "ru" else "🔍 Search",
                             callback_data="bm:search_prompt"),
        InlineKeyboardButton("➕ Добавить" if lang == "ru" else "➕ Add",
                             callback_data="bm:add_blogger_start"),
    ])
    buttons.append([InlineKeyboardButton(
        "🏠 Главная" if lang == "ru" else "🏠 Home", callback_data="nav_home"
    )])

    if lang == "ru":
        text = f"Блогеры ({total}) · стр. {page+1}/{total_pages if total_pages > 1 else 1}:"
    else:
        text = f"Bloggers ({total}) · p. {page+1}/{total_pages if total_pages > 1 else 1}:"

    return await _edit_or_reply(target, text, edit, InlineKeyboardMarkup(buttons))


# --------------------------------------------------------------------------- #
# Screen: blogger card
# --------------------------------------------------------------------------- #
async def screen_blogger(target, blogger_id: int, lang: str, edit: bool = True, back_page: int = 0):
    b = await get_blogger_by_id(blogger_id)
    if not b or not b.get("is_active", 1):
        await _edit_or_reply(target, "Блогер не найден или был удалён." if lang == "ru" else "Blogger not found or has been removed.", edit)
        return

    methods = await get_active_methods(blogger_id)
    lines = [f"👤 {b['name']}"]
    if b.get("notes"):
        lines.append(f"📝 {b['notes']}")
    if methods:
        for m in methods:
            star = " ★" if m.get("is_primary") else ""
            lines.append(f"  {METHOD_LABELS.get(m['type'], m['type'])}: {m['address']}{star}")
    else:
        lines.append("  " + ("методы оплаты не указаны" if lang == "ru" else "no payment methods set"))

    text = "\n".join(lines)

    buttons = []
    # Method buttons
    for m in methods:
        star = "★ " if m.get("is_primary") else ""
        label = f"{star}{METHOD_LABELS.get(m['type'], m['type'])}: {m['address'][:24]}..."
        buttons.append([InlineKeyboardButton(label, callback_data=f"bm:method:{m['id']}")])

    # Actions
    if lang == "ru":
        row_actions = [
            InlineKeyboardButton("➕ Метод", callback_data=f"bm:add_method_type:{blogger_id}"),
            InlineKeyboardButton("📝 Заметка", callback_data=f"bm:edit_note:{blogger_id}"),
        ]
        if b.get("notes"):
            row_actions.append(InlineKeyboardButton("🗑 Заметку", callback_data=f"bm:del_note:{blogger_id}"))
        row_del = [InlineKeyboardButton("🗑 Удалить блогера", callback_data=f"bm:del_blogger_confirm:{blogger_id}")]
        row_back = [InlineKeyboardButton("← Назад", callback_data=f"bm:list:{back_page}:")]
    else:
        row_actions = [
            InlineKeyboardButton("➕ Method", callback_data=f"bm:add_method_type:{blogger_id}"),
            InlineKeyboardButton("📝 Note",   callback_data=f"bm:edit_note:{blogger_id}"),
        ]
        if b.get("notes"):
            row_actions.append(InlineKeyboardButton("🗑 Note", callback_data=f"bm:del_note:{blogger_id}"))
        row_del  = [InlineKeyboardButton("🗑 Delete blogger", callback_data=f"bm:del_blogger_confirm:{blogger_id}")]
        row_back = [InlineKeyboardButton("← Back", callback_data=f"bm:list:{back_page}:")]

    buttons.append(row_actions)
    buttons.append(row_del)
    buttons.append(row_back)

    await _edit_or_reply(target, text, edit, InlineKeyboardMarkup(buttons))


# --------------------------------------------------------------------------- #
# Screen: method card
# --------------------------------------------------------------------------- #
async def screen_method(target, method_id: int, blogger_id: int, lang: str, edit: bool = True):
    m = await get_method_by_id(method_id)
    if not m:
        await _edit_or_reply(target, "Метод оплаты не найден." if lang == "ru" else "Payment method not found.", edit)
        return

    label = METHOD_LABELS.get(m["type"], m["type"])
    is_primary = m.get("is_primary", 0)
    is_active  = m.get("is_active", 1)
    star = "★ " if is_primary else ""

    if lang == "ru":
        text = (
            f"{star}{label}\n"
            f"Адрес: {m['address']}\n"
            f"Статус: {'активен' if is_active else 'отключён'}"
            + ("\nОсновной ✓" if is_primary else "")
        )
        toggle_label = "🔴 Отключить" if is_active else "🟢 Включить"
        primary_label = "✅ Основной" if is_primary else "⭐ Сделать основным"
        buttons = [
            [InlineKeyboardButton("✏ Изменить адрес", callback_data=f"bm:edit_address:{method_id}:{blogger_id}")],
            [InlineKeyboardButton(primary_label, callback_data=f"bm:set_primary:{method_id}:{blogger_id}")],
            [InlineKeyboardButton(toggle_label,  callback_data=f"bm:toggle_method:{method_id}:{blogger_id}")],
            [InlineKeyboardButton("🗑 Удалить метод", callback_data=f"bm:del_method_confirm:{method_id}:{blogger_id}")],
            [InlineKeyboardButton("← Назад", callback_data=f"bm:blogger:{blogger_id}")],
        ]
    else:
        text = (
            f"{star}{label}\n"
            f"Address: {m['address']}\n"
            f"Status: {'active' if is_active else 'disabled'}"
            + ("\nPrimary ✓" if is_primary else "")
        )
        toggle_label = "🔴 Disable" if is_active else "🟢 Enable"
        primary_label = "✅ Primary" if is_primary else "⭐ Set as primary"
        buttons = [
            [InlineKeyboardButton("✏ Edit address", callback_data=f"bm:edit_address:{method_id}:{blogger_id}")],
            [InlineKeyboardButton(primary_label,    callback_data=f"bm:set_primary:{method_id}:{blogger_id}")],
            [InlineKeyboardButton(toggle_label,     callback_data=f"bm:toggle_method:{method_id}:{blogger_id}")],
            [InlineKeyboardButton("🗑 Delete method", callback_data=f"bm:del_method_confirm:{method_id}:{blogger_id}")],
            [InlineKeyboardButton("← Back", callback_data=f"bm:blogger:{blogger_id}")],
        ]

    await _edit_or_reply(target, text, edit, InlineKeyboardMarkup(buttons))


# --------------------------------------------------------------------------- #
# Confirmation screens
# --------------------------------------------------------------------------- #
async def screen_confirm(target, text: str, yes_cb: str, no_cb: str, lang: str, edit: bool = True):
    buttons = [[
        InlineKeyboardButton("✓ Да" if lang == "ru" else "✓ Yes", callback_data=yes_cb),
        InlineKeyboardButton("✗ Нет" if lang == "ru" else "✗ No",  callback_data=no_cb),
    ]]
    await _edit_or_reply(target, text, edit, InlineKeyboardMarkup(buttons))


# --------------------------------------------------------------------------- #
# Add method: type selection screen
# --------------------------------------------------------------------------- #
async def screen_add_method_type(target, blogger_id: int, lang: str, edit: bool = True):
    b = await get_blogger_by_id(blogger_id)
    name = b["name"] if b else "?"
    text = (f"Добавить метод для {name}:\nВыберите тип:" if lang == "ru"
            else f"Add method for {name}:\nSelect type:")
    buttons = [
        [InlineKeyboardButton(METHOD_LABELS[t], callback_data=f"bm:add_method_addr:{t}:{blogger_id}")]
        for t in METHOD_TYPES
    ]
    buttons.append([InlineKeyboardButton(
        "← Назад" if lang == "ru" else "← Back",
        callback_data=f"bm:blogger:{blogger_id}"
    )])
    await _edit_or_reply(target, text, edit, InlineKeyboardMarkup(buttons))


# --------------------------------------------------------------------------- #
# Helper: edit or reply
# --------------------------------------------------------------------------- #
async def _edit_or_reply(
    target, text: str, edit: bool,
    keyboard: InlineKeyboardMarkup | None = None
) -> Message | None:
    kwargs = {"reply_markup": keyboard} if keyboard else {}
    if edit:
        if hasattr(target, "edit_message_text"):
            return await target.edit_message_text(text, **kwargs)
        elif hasattr(target, "message") and target.message:
            return await target.message.edit_text(text, **kwargs)
        elif hasattr(target, "edit_text"):
            return await target.edit_text(text, **kwargs)
    msg = getattr(target, "effective_message", None) or getattr(target, "message", None) or target
    if hasattr(msg, "reply_text"):
        return await msg.reply_text(text, **kwargs)
    return None


# --------------------------------------------------------------------------- #
# Text input handler (addresses, notes, blogger names)
# --------------------------------------------------------------------------- #
_NAV_BUTTONS = {"🏠 Home", "💸 Payout", "👥 Bloggers", "⚙️ Settings",
                   "🏠 Главная", "💸 Выплата", "👥 Блогеры", "⚙️ Настройки"}


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text replies for pending bm_action states."""
    # Don't interfere if import conversation is active
    if context.user_data.get("ib_user") is not None:
        return

    action = context.user_data.get("bm_action")
    if not action:
        return  # Not our message

    # Nav keyboard button pressed while waiting for input — cancel silently
    text_raw = (update.message.text or "").strip()
    if text_raw in _NAV_BUTTONS or any(text_raw.startswith(e) for e in ("🏠", "💸", "👥", "⚙️")):
        context.user_data.pop("bm_action", None)
        context.user_data.pop("bm_prompt_msg_id", None)
        # Let fallback handle the nav button normally
        return

    # A spreadsheet paste is never a blogger name, address or search query.
    # If stale input state lingers (e.g. an abandoned search prompt), don't
    # capture the paste — clear the state and ignore it, so payout rows are
    # not echoed back as a blogger search.
    _low = text_raw.lower()
    is_table_paste = (
        "\t" in text_raw
        or ("http" in _low and any(c in text_raw for c in "$€₽"))
    )
    if action != "edit_note":
        is_table_paste = is_table_paste or "\n" in text_raw
    if is_table_paste:
        context.user_data.pop("bm_action", None)
        context.user_data.pop("bm_prompt_msg_id", None)
        return

    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    text = update.message.text.strip()
    menu_msg_id = context.user_data.get("bm_msg_id")

    # Delete the prompt message (the one bot sent asking for input)
    prompt_msg_id = context.user_data.get("bm_prompt_msg_id")
    if prompt_msg_id:
        try:
            await update.message.bot.delete_message(update.effective_chat.id, prompt_msg_id)
        except Exception:
            pass

    # Delete user's reply to keep chat clean
    try:
        await update.message.delete()
    except Exception:
        pass

    if action == "search_bloggers":
        context.user_data["_last_action"] = "search_bloggers"
        await _restore_screen_list_search(update, context, user, lang, menu_msg_id, text)
        return

    if action == "add_blogger_name":
        if not text:
            return
        # Check for existing
        existing = await get_blogger_by_name(text, user["id"])
        if existing:
            await _restore_menu(update, context, menu_msg_id,
                f"Блогер «{text}» уже существует." if lang == "ru" else f"Blogger '{text}' already exists.",
                lang)
            return
        result = await add_blogger(text, user["id"])
        if result:
            log_info("BLOGGER_ADDED", user_id=user["telegram_id"], username=user["username"], blogger=text)
            await db_log(user["id"], "BLOGGER_ADDED", f"blogger={text}")
            context.user_data["bm_blogger_id"] = result["id"]
            context.user_data.pop("bm_action", None)
            await _restore_screen_blogger(update, context, result["id"], lang, menu_msg_id)
        return

    blogger_id = context.user_data.get("bm_blogger_id")
    method_id  = context.user_data.get("bm_method_id")

    if action == "edit_note":
        if blogger_id:
            await update_blogger_notes(blogger_id, text)
            log_info("NOTE_UPDATED", user_id=user["telegram_id"], username=user["username"])
            context.user_data.pop("bm_action", None)
            await _restore_screen_blogger(update, context, blogger_id, lang, menu_msg_id)

    elif action == "add_address":
        context.user_data["_last_action"] = "add_address"
        mtype = context.user_data.get("bm_pending_type")
        if blogger_id and mtype:
            await add_payment_method(blogger_id, mtype, text)
            log_info("METHOD_ADDED", user_id=user["telegram_id"], username=user["username"], type=mtype)
            await db_log(user["id"], "METHOD_ADDED", f"type={mtype}")
            context.user_data.pop("bm_action", None)
            context.user_data.pop("bm_pending_type", None)
            await _restore_screen_blogger(update, context, blogger_id, lang, menu_msg_id)

    elif action == "edit_address":
        context.user_data["_last_action"] = "edit_address"
        if method_id and blogger_id:
            await update_method_address(method_id, text)
            log_info("METHOD_UPDATED", user_id=user["telegram_id"], username=user["username"])
            context.user_data.pop("bm_action", None)
            await _restore_screen_method(update, context, method_id, blogger_id, lang, menu_msg_id)



async def _restore_screen_list_search(update, context, user, lang, menu_msg_id, search: str):
    """After search input — re-render list with search filter."""
    try:
        msg = await update.message.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=menu_msg_id,
            text="🔍 ..." if lang == "ru" else "🔍 ...",
        )
        await screen_list(msg, user, lang, edit=True, page=0, search=search)
    except Exception:
        sent = await update.effective_chat.send_message("🔍 ...")
        context.user_data["bm_msg_id"] = sent.message_id
        await screen_list(sent, user, lang, edit=True, page=0, search=search)
    try:
        await update.message.delete()
    except Exception:
        pass

async def _restore_screen_blogger(update, context, blogger_id, lang, menu_msg_id, back_page: int = 0):
    """Re-render blogger card by editing the original menu message."""
    try:
        msg = await update.message.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=menu_msg_id,
            text="...",
        )
        await screen_blogger(msg, blogger_id, lang, edit=True, back_page=back_page)
    except Exception:
        sent = await update.effective_chat.send_message("...")
        context.user_data["bm_msg_id"] = sent.message_id
        await screen_blogger(sent, blogger_id, lang, edit=True, back_page=back_page)


async def _restore_screen_method(update, context, method_id, blogger_id, lang, menu_msg_id):
    try:
        msg = await update.message.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=menu_msg_id,
            text="...",
        )
        await screen_method(msg, method_id, blogger_id, lang, edit=True)
    except Exception:
        sent = await update.effective_chat.send_message("...")
        context.user_data["bm_msg_id"] = sent.message_id
        await screen_method(sent, method_id, blogger_id, lang, edit=True)


async def _restore_menu(update, context, menu_msg_id, text, lang):
    try:
        await update.message.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=menu_msg_id,
            text=text,
        )
    except Exception:
        pass


async def _send_prompt(query, context, text: str, action: str, **extra) -> None:
    """Send a temporary prompt message and record its id."""
    msg = await query.message.reply_text(text)
    context.user_data["bm_action"] = action
    context.user_data["bm_prompt_msg_id"] = msg.message_id
    for k, v in extra.items():
        context.user_data[k] = v


# --------------------------------------------------------------------------- #
# /cancel for text input
# --------------------------------------------------------------------------- #
async def cmd_cancel_bm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("bm_action"):
        return  # Not our cancel
    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data.pop("bm_action", None)
    try:
        await update.message.delete()
    except Exception:
        pass
    menu_msg_id = context.user_data.get("bm_msg_id")
    blogger_id  = context.user_data.get("bm_blogger_id")
    if menu_msg_id and blogger_id:
        _bp = context.user_data.get("bm_list_page", 0)
        await _restore_screen_blogger(update, context, blogger_id, lang, menu_msg_id, back_page=_bp)
    else:
        await update.message.reply_text("Отменено." if lang == "ru" else "Cancelled.")


# --------------------------------------------------------------------------- #
# /bloggers entry point
# --------------------------------------------------------------------------- #
async def cmd_bloggers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return
    lang = get_lang(user)
    context.user_data["bm_user_id"] = user["id"]
    sent = await screen_list(update.message, user, lang, edit=False)
    if sent:
        context.user_data["bm_msg_id"] = sent.message_id


# --------------------------------------------------------------------------- #
# Main callback router: bm:<action>:<args...>
# --------------------------------------------------------------------------- #
async def cb_bm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user = await get_user(update.effective_user.id)
    lang = get_lang(user) if user else "en"
    context.user_data["bm_user_id"] = user["id"] if user else 0
    context.user_data["bm_msg_id"]  = query.message.message_id

    parts = query.data.split(":")
    action = parts[1]

    # ---- NOOP (page indicator button) ----
    if action == "noop":
        await query.answer()
        return

    # ---- LIST with pagination and search ----
    elif action == "list":
        page   = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        search = parts[3] if len(parts) > 3 else ""
        await screen_list(query, user, lang, edit=True, page=page, search=search)
        return

    # ---- SEARCH PROMPT ----
    elif action == "search_prompt":
        context.user_data["bm_action"]  = "search_bloggers"
        context.user_data["bm_msg_id"]  = query.message.message_id
        context.user_data["bm_user_id"] = user["id"]
        await query.edit_message_text(
            "Введи часть никнейма для поиска:" if lang == "ru" else "Enter part of the username to search:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад" if lang == "ru" else "← Back",
                                     callback_data="bm:list:0:")
            ]])
        )
        return

    # ---- BLOGGER CARD ----
    elif action == "blogger":
        blogger_id = int(parts[2])
        back_page  = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        context.user_data["bm_blogger_id"] = blogger_id
        context.user_data["bm_list_page"]  = back_page
        await screen_blogger(query, blogger_id, lang, back_page=back_page)

    # ---- ADD METHOD FROM HOME (shows list) ----
    elif action == "add_method_list":
        bloggers_without = await get_bloggers_without_method(user["id"])
        all_bloggers = await get_bloggers_for_manager(user["id"])
        bloggers = bloggers_without or all_bloggers
        if not bloggers:
            await query.edit_message_text(
                "Сначала добавьте блогера: /add_blogger" if lang == "ru"
                else "Add a blogger first: /add_blogger",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("← Назад" if lang == "ru" else "← Back", callback_data="bm:list")
                ]])
            )
            return
        header = ("Блогеры без метода оплаты:" if bloggers_without else "Все блогеры:") if lang == "ru"                  else ("Bloggers without a payment method:" if bloggers_without else "All bloggers:")
        buttons = [[InlineKeyboardButton(b["name"], callback_data=f"bm:add_method_type:{b['id']}")]
                   for b in bloggers]
        if bloggers_without and len(all_bloggers) > len(bloggers_without):
            buttons.append([InlineKeyboardButton(
                "Показать всех" if lang == "ru" else "Show all",
                callback_data="bm:add_method_list_all"
            )])
        buttons.append([InlineKeyboardButton("← Назад" if lang == "ru" else "← Back", callback_data="show_start")])
        await query.edit_message_text(header, reply_markup=InlineKeyboardMarkup(buttons))

    elif action == "add_method_list_all":
        bloggers = await get_bloggers_for_manager(user["id"])
        buttons = [[InlineKeyboardButton(b["name"], callback_data=f"bm:add_method_type:{b['id']}")]
                   for b in bloggers]
        buttons.append([InlineKeyboardButton("← Назад" if lang == "ru" else "← Back", callback_data="show_start")])
        header = "Все блогеры" if lang == "ru" else "All bloggers"
        await query.edit_message_text(header, reply_markup=InlineKeyboardMarkup(buttons))

    # ---- ADD BLOGGER ----
    elif action == "add_blogger_start":
        # parts[2] = origin context: 'list' (from /bloggers) or 'home' (from main screen)
        origin = parts[2] if len(parts) > 2 else "list"
        context.user_data["bm_origin"] = origin
        _back_page = context.user_data.get("bm_list_page", 0)
        back_cb = "show_start" if origin == "home" else f"bm:list:{_back_page}:"
        await query.edit_message_text(
            "Введи никнейм блогера точно так, как он указан в таблице:" if lang == "ru"
            else "Enter the blogger username exactly as it appears in the spreadsheet:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад" if lang == "ru" else "← Back", callback_data=back_cb)
            ]])
        )
        context.user_data["bm_action"] = "add_blogger_name"

    # ---- ADD METHOD: type selection ----
    elif action == "add_method_type":
        blogger_id = int(parts[2])
        context.user_data["bm_blogger_id"] = blogger_id
        await screen_add_method_type(query, blogger_id, lang)

    # ---- ADD METHOD: address input ----
    elif action == "add_method_addr":
        mtype = parts[2]
        blogger_id = int(parts[3])
        context.user_data["bm_blogger_id"]   = blogger_id
        context.user_data["bm_pending_type"] = mtype
        hints = {
            "site":       {"ru": "Profile ID (напр. 690779e7e54ed806f3d730b4)", "en": "Profile ID (e.g. 690779e7e54ed806f3d730b4)"},
            "usdt-trc20": {"ru": "Адрес кошелька TRC20",                        "en": "TRC20 wallet address"},
            "paypal":     {"ru": "Email PayPal",                                 "en": "PayPal email"},
        }
        hint = hints.get(mtype, {}).get(lang, "")
        label = METHOD_LABELS.get(mtype, mtype)
        back_row = [InlineKeyboardButton(
            "← Назад" if lang == "ru" else "← Back",
            callback_data=f"bm:add_method_type:{blogger_id}"
        )]
        await query.edit_message_text(
            f"Метод: {label}\n{hint}\n\nВведи реквизиты:" if lang == "ru"
            else f"Method: {label}\n{hint}\n\nEnter the payment details:",
            reply_markup=InlineKeyboardMarkup([back_row])
        )
        context.user_data["bm_action"] = "add_address"

    elif action == "edit_note":
        context.user_data["_last_action"] = "edit_note"
        blogger_id = int(parts[2])
        context.user_data["bm_blogger_id"] = blogger_id
        b = await get_blogger_by_id(blogger_id)
        current = b.get("notes") or ""
        prefix = (f"Текущая заметка: {current}\n\n" if current else "")
        await query.edit_message_text(
            prefix + ("Введи текст заметки:" if lang == "ru" else "Enter note text:"),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "← Назад" if lang == "ru" else "← Back",
                    callback_data=f"bm:blogger:{blogger_id}"
                )
            ]])
        )
        context.user_data["bm_action"] = "edit_note"

    # ---- DELETE NOTE ----
    elif action == "del_note":
        blogger_id = int(parts[2])
        await update_blogger_notes(blogger_id, "")
        await screen_blogger(query, blogger_id, lang)

    # ---- METHOD CARD ----
    elif action == "method":
        method_id  = int(parts[2])
        method = await get_method_by_id(method_id)
        blogger_id = method["blogger_id"] if method else 0
        context.user_data["bm_method_id"]  = method_id
        context.user_data["bm_blogger_id"] = blogger_id
        await screen_method(query, method_id, blogger_id, lang)

    # ---- SET PRIMARY ----
    elif action == "set_primary":
        method_id  = int(parts[2])
        blogger_id = int(parts[3])
        await set_primary_method(method_id, blogger_id)
        log_info("METHOD_SET_PRIMARY", user_id=user["telegram_id"] if user else 0,
                 username=user["username"] if user else "", method_id=method_id, blogger_id=blogger_id)
        await db_log(user["id"] if user else None, "METHOD_SET_PRIMARY", f"method_id={method_id}")
        await screen_method(query, method_id, blogger_id, lang)

    # ---- TOGGLE METHOD ----
    elif action == "toggle_method":
        method_id  = int(parts[2])
        blogger_id = int(parts[3])
        m = await get_method_by_id(method_id)
        new_state = "disabled" if m["is_active"] else "enabled"
        if m["is_active"]:
            await deactivate_method(method_id)
        else:
            await reactivate_method(method_id)
        log_info("METHOD_TOGGLED", user_id=user["telegram_id"] if user else 0,
                 username=user["username"] if user else "", method_id=method_id, state=new_state)
        await db_log(user["id"] if user else None, "METHOD_TOGGLED", f"method_id={method_id}")
        await screen_method(query, method_id, blogger_id, lang)

    # ---- EDIT ADDRESS ----
    elif action == "edit_address":
        method_id  = int(parts[2])
        blogger_id = int(parts[3])
        context.user_data["bm_method_id"]  = method_id
        context.user_data["bm_blogger_id"] = blogger_id
        await query.edit_message_text(
            "Введи новые реквизиты:" if lang == "ru" else "Enter the new payment details:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "← Назад" if lang == "ru" else "← Back",
                    callback_data=f"bm:method:{method_id}"
                )
            ]])
        )
        context.user_data["bm_action"] = "edit_address"

    # ---- DELETE BLOGGER (confirm) ----
    elif action == "del_blogger_confirm":
        blogger_id = int(parts[2])
        b = await get_blogger_by_id(blogger_id)
        name = b["name"] if b else "?"
        await screen_confirm(
            query,
            (f"Удалить блогера {name}?\nИстория выплат останется в базе."
             if lang == "ru" else
             f"Delete {name}?\nPayout history will be preserved."),
            yes_cb=f"bm:del_blogger_do:{blogger_id}",
            no_cb=f"bm:blogger:{blogger_id}",
            lang=lang,
        )

    # ---- DELETE BLOGGER (do) ----
    elif action == "del_blogger_do":
        blogger_id = int(parts[2])
        b = await get_blogger_by_id(blogger_id)
        name = b["name"] if b else "?"
        await deactivate_blogger(blogger_id)
        log_info("BLOGGER_DEACTIVATED", user_id=user["telegram_id"] if user else 0,
                 username=user["username"] if user else "", blogger=name)
        await db_log(user["id"] if user else None, "BLOGGER_DEACTIVATED", f"blogger={name}")
        await screen_list(query, user, lang)

    # ---- DELETE METHOD (confirm) ----
    elif action == "del_method_confirm":
        method_id  = int(parts[2])
        blogger_id = int(parts[3])
        m = await get_method_by_id(method_id)
        label = METHOD_LABELS.get(m["type"], m["type"]) if m else "?"
        await screen_confirm(
            query,
            (f"Отключить метод {label}?\n{m['address']}"
             if lang == "ru" else
             f"Disable method {label}?\n{m['address']}"),
            yes_cb=f"bm:del_method_do:{method_id}:{blogger_id}",
            no_cb=f"bm:method:{method_id}",
            lang=lang,
        )

    # ---- DELETE METHOD (do) ----
    elif action == "del_method_do":
        method_id  = int(parts[2])
        blogger_id = int(parts[3])
        await deactivate_method(method_id)
        log_info("METHOD_DEACTIVATED", user_id=user["telegram_id"] if user else 0,
                 username=user["username"] if user else "", method_id=method_id, blogger_id=blogger_id)
        await db_log(user["id"] if user else None, "METHOD_DEACTIVATED", f"method_id={method_id}")
        await screen_blogger(query, blogger_id, lang)


# --------------------------------------------------------------------------- #
# /add_method shortcut (command entry)
# --------------------------------------------------------------------------- #
async def cmd_add_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return
    lang = get_lang(user)
    arg = " ".join(context.args).strip() if context.args else ""

    bloggers_without = await get_bloggers_without_method(user["id"])
    if arg:
        from database.queries import search_bloggers_by_prefix
        exact = await get_blogger_by_name(arg, user["id"])
        if not exact:
            candidates = await search_bloggers_by_prefix(arg, user["id"])
            if len(candidates) == 1:
                exact = candidates[0]
            elif candidates:
                buttons = [[InlineKeyboardButton(b["name"], callback_data=f"bm:add_method_type:{b['id']}")]
                           for b in candidates]
                sent = await update.message.reply_text(
                    f"Несколько совпадений для «{arg}»:" if lang == "ru" else f"Multiple matches for '{arg}':",
                    reply_markup=InlineKeyboardMarkup(buttons)
                )
                context.user_data["bm_msg_id"] = sent.message_id
                return
            else:
                await update.message.reply_text(
                    f"Блогер «{arg}» не найден." if lang == "ru" else f"Blogger '{arg}' not found."
                )
                return
        context.user_data["bm_blogger_id"] = exact["id"]
        sent = await update.message.reply_text("...")
        context.user_data["bm_msg_id"] = sent.message_id
        await screen_add_method_type(sent, exact["id"], lang, edit=True)
        return

    # Show list
    bloggers = bloggers_without or await get_bloggers_for_manager(user["id"])
    if not bloggers:
        await update.message.reply_text(
            "Сначала добавьте блогера: /add_blogger" if lang == "ru"
            else "Add a blogger first: /add_blogger"
        )
        return
    header = ("Блогеры без метода оплаты:" if bloggers_without else "Все блогеры:") if lang == "ru" \
             else ("Bloggers without a payment method:" if bloggers_without else "All bloggers:")
    buttons = [[InlineKeyboardButton(b["name"], callback_data=f"bm:add_method_type:{b['id']}")]
               for b in bloggers]
    if bloggers_without:
        all_list = await get_bloggers_for_manager(user["id"])
        if len(all_list) > len(bloggers_without):
            buttons.append([InlineKeyboardButton(
                "Показать всех" if lang == "ru" else "Show all",
                callback_data="bm:list"
            )])
    sent = await update.message.reply_text(header, reply_markup=InlineKeyboardMarkup(buttons))
    context.user_data["bm_msg_id"] = sent.message_id


# --------------------------------------------------------------------------- #
# /add_blogger shortcut
# --------------------------------------------------------------------------- #
async def cmd_add_blogger(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_or_reject(update)
    if not user:
        return
    lang = get_lang(user)
    context.user_data["bm_origin"] = "list"  # command entry always backs to list
    sent = await update.message.reply_text(
        "Введи никнейм блогера точно так, как он указан в таблице:" if lang == "ru"
        else "Enter the blogger username exactly as it appears in the spreadsheet:",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("← Назад" if lang == "ru" else "← Back", callback_data="bm:list")
        ]])
    )
    context.user_data["bm_msg_id"] = sent.message_id
    context.user_data["bm_action"] = "add_blogger_name"
    context.user_data["bm_user_id"] = user["id"]


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register_blogger_handlers(app):
    app.add_handler(CommandHandler("bloggers",    cmd_bloggers))
    app.add_handler(CommandHandler("add_blogger", cmd_add_blogger))
    app.add_handler(CommandHandler("add_method",  cmd_add_method))
    app.add_handler(CommandHandler("cancel",      cmd_cancel_bm))

    # Main inline router
    app.add_handler(CallbackQueryHandler(cb_bm, pattern=r"^bm:"))

    # Text input handler — runs in group 1 (after ConversationHandlers)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input),
        group=2,
    )