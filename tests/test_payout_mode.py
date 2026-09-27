"""Regression tests for persistent payout mode."""

import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "fake")
os.environ.setdefault("ADMIN_ID", "123")

import database.db as db_module
from database.queries import get_payout_mode, get_user, set_payout_mode
from handlers import payout, start


def _context(**user_data):
    return SimpleNamespace(user_data=dict(user_data), args=[])


def _message_update(text="rows", user_id=123):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, first_name="Test"),
        message=message,
        effective_message=message,
        callback_query=None,
    )


def _callback_update(user_id=123):
    message = SimpleNamespace(reply_text=AsyncMock(), reply_document=AsyncMock())
    query = SimpleNamespace(
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        message=message,
    )
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_message=message,
        callback_query=query,
        message=None,
    )


class PayoutModeDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db_path = db_module.DB_PATH
        self.temp_dir = tempfile.TemporaryDirectory()
        db_module.DB_PATH = os.path.join(self.temp_dir.name, "legacy.db")

        # Production-like schema immediately before payout_mode was added.
        with sqlite3.connect(db_module.DB_PATH) as db:
            db.execute(
                """
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE NOT NULL,
                    username TEXT,
                    role TEXT NOT NULL DEFAULT 'manager',
                    lang TEXT NOT NULL DEFAULT 'ru',
                    manager_filter TEXT,
                    output_mode TEXT NOT NULL DEFAULT 'text',
                    default_fmt TEXT NOT NULL DEFAULT 'oneline',
                    include_paid INTEGER NOT NULL DEFAULT 0,
                    warn_paid INTEGER NOT NULL DEFAULT 1,
                    include_pending INTEGER NOT NULL DEFAULT 0,
                    warn_pending INTEGER NOT NULL DEFAULT 1,
                    include_no_method INTEGER NOT NULL DEFAULT 0,
                    method_from_table INTEGER NOT NULL DEFAULT 0,
                    mgr_password TEXT,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            db.execute(
                "INSERT INTO users (telegram_id, username) VALUES (?, ?)",
                (123, "tester"),
            )

        await db_module.init_db()

    async def asyncTearDown(self):
        db_module.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    async def test_migration_adds_default_disabled_mode(self):
        with sqlite3.connect(db_module.DB_PATH) as db:
            columns = {row[1]: row for row in db.execute("PRAGMA table_info(users)")}
            stored = db.execute(
                "SELECT payout_mode FROM users WHERE telegram_id = 123"
            ).fetchone()[0]

        self.assertIn("payout_mode", columns)
        self.assertEqual(columns["payout_mode"][2].upper(), "INTEGER")
        self.assertEqual(columns["payout_mode"][3], 1)
        self.assertEqual(str(columns["payout_mode"][4]), "0")
        self.assertEqual(stored, 0)
        self.assertFalse(await get_payout_mode(123))

    async def test_query_functions_enable_and_disable_mode(self):
        await set_payout_mode(123, True)
        self.assertTrue(await get_payout_mode(123))
        self.assertEqual((await get_user(123))["payout_mode"], 1)

        await set_payout_mode(123, False)
        self.assertFalse(await get_payout_mode(123))


class PayoutModeHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_rows_nav_button_reaches_fallback(self):
        update = _message_update("🏠 Home")
        user = {
            "id": 1, "telegram_id": 123, "lang": "en", "role": "manager",
            "payout_mode": 1,
        }
        context = _context(user=user, effective_filter=None)

        with patch.object(payout, "parse_rows") as parse_rows_mock:
            state = await payout.payout_got_rows(update, context)

        self.assertEqual(state, payout.ConversationHandler.END)
        self.assertNotIn("_payout_just_handled", context.user_data)
        parse_rows_mock.assert_not_called()

        with patch.object(start, "get_user", AsyncMock(return_value=user)), \
                patch.object(payout, "handle_payout_mode_message", AsyncMock()) as route:
            await start.fallback_message(update, context)

        update.message.reply_text.assert_awaited_once()
        route.assert_not_awaited()

    async def test_enable_mode_keeps_current_payout_ready(self):
        update = _callback_update()
        context = _context(effective_filter="John")
        user = {"id": 1, "telegram_id": 123, "lang": "ru", "manager_filter": "John"}

        with patch.object(payout, "get_user_or_reject", AsyncMock(return_value=user)), \
                patch.object(payout, "set_payout_mode", AsyncMock()) as set_mode:
            state = await payout.cb_enable_payout_mode(update, context)

        self.assertEqual(state, payout.WAIT_ROWS)
        set_mode.assert_awaited_once_with(123, True)
        self.assertEqual(context.user_data["user"]["payout_mode"], 1)
        markup = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[0][0].text, "✕ Выйти из режима выплат")

    async def test_exit_mode_disables_and_only_clears_payout_data(self):
        update = _callback_update()
        context = _context(
            payout_raw="raw", pd_test={}, all_payout_texts=["one"],
            bm_action="edit_note",
        )
        user = {"telegram_id": 123, "lang": "en", "payout_mode": 1}

        with patch.object(payout, "get_user", AsyncMock(return_value=user)), \
                patch("handlers.common.set_payout_mode", AsyncMock()) as set_mode:
            state = await payout.cb_exit_payout_mode(update, context)

        self.assertEqual(state, payout.ConversationHandler.END)
        set_mode.assert_awaited_once_with(123, False)
        self.assertNotIn("payout_raw", context.user_data)
        self.assertNotIn("pd_test", context.user_data)
        self.assertNotIn("all_payout_texts", context.user_data)
        self.assertEqual(context.user_data["bm_action"], "edit_note")

    async def test_cancel_command_disables_mode(self):
        update = _message_update("/cancel")
        context = _context(payout_raw="raw")
        user = {"telegram_id": 123, "lang": "ru", "payout_mode": 1}

        with patch.object(payout, "get_user", AsyncMock(return_value=user)), \
                patch.object(payout, "set_payout_mode", AsyncMock()) as set_mode:
            await payout.cmd_cancel(update, context)

        set_mode.assert_awaited_once_with(123, False)
        self.assertEqual(context.user_data, {})

    async def test_next_plain_text_is_processed_without_new_button_press(self):
        update = _message_update("next payout rows")
        context = _context(previous="value")
        user = {
            "id": 1, "telegram_id": 123, "lang": "en",
            "manager_filter": "John", "payout_mode": 1,
        }

        with patch.object(payout, "get_user", AsyncMock(return_value=user)), \
                patch.object(payout, "payout_got_rows", AsyncMock(return_value=-1)) as got_rows:
            handled = await payout.handle_payout_mode_message(update, context)

        self.assertTrue(handled)
        got_rows.assert_awaited_once_with(update, context)
        self.assertEqual(context.user_data["effective_filter"], "John")

    async def test_mode_survives_empty_context_like_new_session(self):
        update = _message_update("rows after restart")
        context = _context()
        user = {
            "id": 1, "telegram_id": 123, "lang": "ru",
            "manager_filter": None, "payout_mode": 1,
        }

        with patch.object(payout, "get_user", AsyncMock(return_value=user)), \
                patch.object(payout, "payout_got_rows", AsyncMock(return_value=-1)) as got_rows:
            handled = await payout.handle_payout_mode_message(update, context)

        self.assertTrue(handled)
        got_rows.assert_awaited_once()
        self.assertIs(context.user_data["user"], user)

    async def test_telegram_command_is_not_treated_as_payout_rows(self):
        update = _message_update("/settings")
        context = _context()

        with patch.object(payout, "get_user", AsyncMock()) as get_user_mock, \
                patch.object(payout, "payout_got_rows", AsyncMock()) as got_rows:
            handled = await payout.handle_payout_mode_message(update, context)

        self.assertFalse(handled)
        get_user_mock.assert_not_awaited()
        got_rows.assert_not_awaited()

    async def test_active_special_flow_is_not_intercepted(self):
        update = _message_update("blogger note")
        context = _context(bm_action="edit_note")
        user = {"id": 1, "telegram_id": 123, "lang": "en", "role": "manager", "payout_mode": 1}

        with patch.object(start, "get_user", AsyncMock(return_value=user)), \
                patch.object(payout, "handle_payout_mode_message", AsyncMock()) as route:
            await start.fallback_message(update, context)

        route.assert_not_awaited()

    async def test_parse_error_does_not_disable_mode(self):
        update = _message_update("invalid payout input")
        user = {"id": 1, "telegram_id": 123, "username": "test", "lang": "en", "payout_mode": 1}
        context = _context(user=user, effective_filter=None)
        parsed = SimpleNamespace(bloggers=[], critical_errors=["bad row"])

        with patch.object(payout, "parse_rows", return_value=parsed), \
                patch.object(payout, "looks_like_lost_tabs", return_value=False), \
                patch.object(payout, "db_log", AsyncMock()), \
                patch.object(payout, "set_payout_mode", AsyncMock()) as set_mode:
            await payout.payout_got_rows(update, context)

        set_mode.assert_not_awaited()
        self.assertEqual(context.user_data["user"]["payout_mode"], 1)
        self.assertIn("Could not parse", update.message.reply_text.await_args.args[0])

    async def test_disabled_mode_uses_old_fallback(self):
        update = _message_update("hello")
        context = _context()
        user = {"id": 1, "telegram_id": 123, "lang": "en", "role": "manager", "payout_mode": 0}

        with patch.object(start, "get_user", AsyncMock(return_value=user)), \
                patch.object(payout, "get_user", AsyncMock(return_value=user)), \
                patch.object(payout, "payout_got_rows", AsyncMock()) as got_rows:
            await start.fallback_message(update, context)

        got_rows.assert_not_awaited()
        self.assertIn("Command not recognized", update.message.reply_text.await_args.args[0])

    def test_ru_and_en_entry_buttons_are_separate_rows(self):
        expected = {
            "ru": ("🔁 Включить режим выплат", "✕ Отмена"),
            "en": ("🔁 Enable payout mode", "✕ Cancel"),
        }
        for lang, labels in expected.items():
            with self.subTest(lang=lang):
                rows = payout._payout_entry_keyboard(lang).inline_keyboard
                self.assertEqual(len(rows), 2)
                self.assertEqual([len(row) for row in rows], [1, 1])
                self.assertEqual((rows[0][0].text, rows[1][0].text), labels)

    def test_mode_result_keyboard_has_full_width_exit_in_both_languages(self):
        for lang, label in (("ru", "✕ Выйти из режима выплат"), ("en", "✕ Exit payout mode")):
            with self.subTest(lang=lang):
                rows = payout._nav_keyboard(lang, payout_mode=True).inline_keyboard
                self.assertEqual(len(rows[-1]), 1)
                self.assertEqual(rows[-1][0].text, label)
                self.assertNotIn("New payout", " ".join(button.text for row in rows for button in row))
                self.assertNotIn("Новая выплата", " ".join(button.text for row in rows for button in row))

    async def test_export_keeps_mode_exit_button_in_both_languages(self):
        expected = {
            "ru": "✕ Выйти из режима выплат",
            "en": "✕ Exit payout mode",
        }
        for lang, exit_label in expected.items():
            with self.subTest(lang=lang):
                update = _callback_update()
                context = _context(all_payout_texts=["payout block"])
                user = {"telegram_id": 123, "lang": lang, "payout_mode": 1}

                with patch.object(payout, "get_user", AsyncMock(return_value=user)):
                    await payout.cb_nav_copy_all(update, context)

                markup = update.callback_query.message.reply_text.await_args.kwargs["reply_markup"]
                self.assertEqual(markup.inline_keyboard[-1][0].text, exit_label)

    async def test_export_without_mode_keeps_old_navigation(self):
        update = _callback_update()
        context = _context(all_payout_texts=["payout block"])
        user = {"telegram_id": 123, "lang": "en", "payout_mode": 0}

        with patch.object(payout, "get_user", AsyncMock(return_value=user)):
            await payout.cb_nav_copy_all(update, context)

        markup = update.callback_query.message.reply_text.await_args.kwargs["reply_markup"]
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertEqual(labels, ["🏠 Home"])


if __name__ == "__main__":
    unittest.main()
