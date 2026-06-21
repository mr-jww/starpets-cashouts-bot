"""All database queries grouped by entity."""

from __future__ import annotations
import aiosqlite
from database.db import get_db


# =========================================================================== #
# USERS
# =========================================================================== #

async def upsert_user(telegram_id: int, username: str | None, role: str = "manager") -> dict:
    async with get_db() as db:
        # Check if a placeholder account exists with matching username (manager name)
        # Placeholder accounts have negative telegram_id
        if username:
            async with db.execute(
                """SELECT id FROM users
                   WHERE manager_filter = ? COLLATE NOCASE
                   AND telegram_id < 0""",
                (username,)
            ) as cur:
                placeholder = await cur.fetchone()
            if placeholder:
                # Upgrade placeholder to real account
                await db.execute(
                    "UPDATE users SET telegram_id = ?, username = ? WHERE id = ?",
                    (telegram_id, username, placeholder["id"])
                )
                await db.commit()
                async with db.execute(
                    "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
                ) as cur:
                    return dict(await cur.fetchone())

        await db.execute(
            """
            INSERT INTO users (telegram_id, username, role)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username
            """,
            (telegram_id, username, role),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            return dict(await cur.fetchone())


async def get_user(telegram_id: int) -> dict | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def set_user_lang(telegram_id: int, lang: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET lang = ? WHERE telegram_id = ?", (lang, telegram_id)
        )
        await db.commit()


async def get_all_users() -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM users ORDER BY created_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# =========================================================================== #
# BLOGGERS
# =========================================================================== #

async def add_blogger(name: str, manager_id: int, notes: str | None = None) -> dict | None:
    """
    Returns the blogger dict, or None if a non-deleted duplicate exists.
    If a soft-deleted blogger with the same name exists, reactivates it.
    """
    async with get_db() as db:
        try:
            await db.execute(
                "INSERT INTO bloggers (name, manager_id, notes) VALUES (?, ?, ?)",
                (name, manager_id, notes),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            # Check if it's soft-deleted — reactivate if so
            async with db.execute(
                "SELECT * FROM bloggers WHERE name = ? AND manager_id = ?",
                (name, manager_id),
            ) as cur:
                row = await cur.fetchone()
            if row and dict(row).get("is_active") == 0:
                await db.execute(
                    "UPDATE bloggers SET is_active = 1, notes = COALESCE(?, notes) WHERE name = ? AND manager_id = ?",
                    (notes, name, manager_id),
                )
                await db.commit()
            else:
                return None  # Active duplicate — genuine conflict
        async with db.execute(
            "SELECT * FROM bloggers WHERE name = ? AND manager_id = ? AND is_active = 1",
            (name, manager_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_blogger_by_name(name: str, manager_id: int) -> dict | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM bloggers WHERE name = ? AND manager_id = ? AND is_active = 1",
            (name, manager_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None



async def get_bloggers_without_method(manager_id: int) -> list[dict]:
    """Returns bloggers that have no active payment methods."""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT b.* FROM bloggers b
            WHERE b.manager_id = ?
            AND NOT EXISTS (
                SELECT 1 FROM payment_methods pm
                WHERE pm.blogger_id = b.id AND pm.is_active = 1
            )
            ORDER BY b.name
            """,
            (manager_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_bloggers_for_manager(manager_id: int) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM bloggers WHERE manager_id = ? AND is_active = 1 ORDER BY name",
            (manager_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]



async def deactivate_blogger(blogger_id: int) -> None:
    """Soft delete: hide blogger from all lists."""
    async with get_db() as db:
        await db.execute(
            "UPDATE bloggers SET is_active = 0 WHERE id = ?", (blogger_id,)
        )
        await db.commit()


async def search_bloggers_by_prefix(prefix: str, manager_id: int) -> list[dict]:
    """Find active bloggers whose name starts with prefix (case-insensitive)."""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT * FROM bloggers
            WHERE manager_id = ? AND is_active = 1
            AND lower(name) LIKE lower(?)
            ORDER BY name
            """,
            (manager_id, f"{prefix}%"),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def update_blogger_notes(blogger_id: int, notes: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE bloggers SET notes = ? WHERE id = ?", (notes, blogger_id)
        )
        await db.commit()


async def get_blogger_by_id(blogger_id: int) -> dict | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM bloggers WHERE id = ?", (blogger_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_all_bloggers() -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT b.*, u.username AS manager_username
            FROM bloggers b JOIN users u ON b.manager_id = u.id
            WHERE b.is_active = 1
            ORDER BY b.name
            """
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def search_bloggers_global(query: str) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT b.*, u.username AS manager_username
            FROM bloggers b JOIN users u ON b.manager_id = u.id
            WHERE b.name LIKE ?
            ORDER BY b.name
            """,
            (f"%{query}%",),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]



METHOD_TYPES = ["site", "usdt-trc20", "paypal"]

METHOD_LABELS = {
    "site":       "Site",
    "usdt-trc20": "USDT-TRC20",
    "paypal":     "PayPal",
}


async def add_payment_method(
    blogger_id: int,
    method_type: str,
    address: str,
    label: str | None = None,
) -> dict:
    """
    Upsert: if a method of this type already exists for the blogger,
    update its address and reactivate it. Otherwise insert new.
    """
    async with get_db() as db:
        # Check for existing method of same type (active or inactive)
        async with db.execute(
            "SELECT * FROM payment_methods WHERE blogger_id = ? AND type = ? ORDER BY id LIMIT 1",
            (blogger_id, method_type),
        ) as cur:
            existing = await cur.fetchone()

        if existing:
            existing = dict(existing)
            if existing["address"] != address or not existing["is_active"]:
                # Save old address to history before overwriting
                if existing["address"] and existing["address"] != address:
                    await db.execute(
                        "INSERT INTO payment_method_history (blogger_id, type, address) VALUES (?, ?, ?)",
                        (blogger_id, method_type, existing["address"]),
                    )
                await db.execute(
                    "UPDATE payment_methods SET address = ?, is_active = 1 WHERE id = ?",
                    (address, existing["id"]),
                )
                await db.commit()
            # Return current state
            async with db.execute(
                "SELECT * FROM payment_methods WHERE id = ?",
                (existing["id"],),
            ) as cur:
                return dict(await cur.fetchone())
        else:
            await db.execute(
                "INSERT INTO payment_methods (blogger_id, type, address, label) VALUES (?, ?, ?, ?)",
                (blogger_id, method_type, address, label),
            )
            await db.commit()
            async with db.execute(
                "SELECT * FROM payment_methods WHERE blogger_id = ? AND type = ? ORDER BY id DESC LIMIT 1",
                (blogger_id, method_type),
            ) as cur:
                return dict(await cur.fetchone())


async def get_active_methods(blogger_id: int) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM payment_methods WHERE blogger_id = ? AND is_active = 1 ORDER BY added_at",
            (blogger_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]



async def get_primary_method(blogger_id: int) -> dict | None:
    """Returns the primary payment method, or first active if none marked primary."""
    async with get_db() as db:
        # Try primary first
        async with db.execute(
            "SELECT * FROM payment_methods WHERE blogger_id = ? AND is_active = 1 AND is_primary = 1 ORDER BY added_at LIMIT 1",
            (blogger_id,),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
        # Fall back to first active
        async with db.execute(
            "SELECT * FROM payment_methods WHERE blogger_id = ? AND is_active = 1 ORDER BY added_at LIMIT 1",
            (blogger_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_active_methods_by_type(blogger_id: int, method_type: str) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM payment_methods WHERE blogger_id = ? AND is_active = 1 AND type = ? ORDER BY added_at",
            (blogger_id, method_type),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def set_primary_method(method_id: int, blogger_id: int) -> None:
    """Mark one method as primary, unmark others for same blogger."""
    async with get_db() as db:
        await db.execute(
            "UPDATE payment_methods SET is_primary = 0 WHERE blogger_id = ?", (blogger_id,)
        )
        await db.execute(
            "UPDATE payment_methods SET is_primary = 1 WHERE id = ?", (method_id,)
        )
        await db.commit()






# --------------------------------------------------------------------------- #
# MANAGER PASSWORDS & LOCKOUT
# --------------------------------------------------------------------------- #

import hashlib as _hashlib
from datetime import datetime as _dt, timedelta as _td


def _hash_pw(password: str) -> str:
    return _hashlib.sha256(password.encode()).hexdigest()


async def set_manager_password(telegram_id: int, password: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET mgr_password = ? WHERE telegram_id = ?",
            (_hash_pw(password), telegram_id),
        )
        await db.commit()


async def check_manager_password(telegram_id: int, password: str) -> str:
    """
    Returns: "ok" | "wrong" | "locked" | "no_password"
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT mgr_password, failed_attempts, locked_until FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return "wrong"
    pw_hash, attempts, locked_until = row

    # Check lockout
    if locked_until:
        lock_dt = _dt.fromisoformat(locked_until)
        if _dt.utcnow() < lock_dt:
            return "locked"
        # Lockout expired — reset
        async with get_db() as db:
            await db.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE telegram_id = ?",
                (telegram_id,),
            )
            await db.commit()

    if not pw_hash:
        return "no_password"

    if pw_hash == _hash_pw(password):
        # Reset on success
        async with get_db() as db:
            await db.execute(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE telegram_id = ?",
                (telegram_id,),
            )
            await db.commit()
        return "ok"

    # Wrong password
    new_attempts = (attempts or 0) + 1
    locked_until_val = None
    if new_attempts >= 5:
        locked_until_val = (_dt.utcnow() + _td(minutes=10)).isoformat()
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE telegram_id = ?",
            (new_attempts, locked_until_val, telegram_id),
        )
        await db.commit()
    return "locked" if locked_until_val else "wrong"


async def reset_lockout(telegram_id: int) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE telegram_id = ?",
            (telegram_id,),
        )
        await db.commit()


async def get_locked_users() -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            """SELECT telegram_id, username, locked_until, failed_attempts
               FROM users WHERE locked_until IS NOT NULL""",
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# --------------------------------------------------------------------------- #
# PAYMENT METHOD HISTORY
# --------------------------------------------------------------------------- #

async def add_method_history(blogger_id: int, method_type: str, old_address: str) -> None:
    """Save old address before it gets replaced."""
    async with get_db() as db:
        await db.execute(
            "INSERT INTO payment_method_history (blogger_id, type, address) VALUES (?, ?, ?)",
            (blogger_id, method_type, old_address),
        )
        await db.commit()


async def get_method_history(blogger_id: int) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            """SELECT type, address, replaced_at
               FROM payment_method_history
               WHERE blogger_id = ?
               ORDER BY replaced_at""",
            (blogger_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_method_history() -> dict[int, list[dict]]:
    """Returns {blogger_id: [history_rows]} for export."""
    async with get_db() as db:
        async with db.execute(
            "SELECT blogger_id, type, address FROM payment_method_history ORDER BY replaced_at"
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    result: dict[int, list] = {}
    for r in rows:
        result.setdefault(r["blogger_id"], []).append(r)
    return result


async def set_filter_setting(telegram_id: int, field: str, value: int) -> None:
    """field: include_paid | warn_paid | include_pending | warn_pending"""
    if field not in ("include_paid", "warn_paid", "include_pending", "warn_pending"):
        return
    async with get_db() as db:
        await db.execute(
            f"UPDATE users SET {field} = ? WHERE telegram_id = ?",
            (value, telegram_id),
        )
        await db.commit()



async def set_show_all_bloggers(telegram_id: int, value: bool) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET show_all_bloggers = ? WHERE telegram_id = ?",
            (1 if value else 0, telegram_id),
        )
        await db.commit()


async def set_include_no_method(telegram_id: int, value: bool) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET include_no_method = ? WHERE telegram_id = ?",
            (1 if value else 0, telegram_id),
        )
        await db.commit()


async def set_method_from_table(telegram_id: int, value: bool) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET method_from_table = ? WHERE telegram_id = ?",
            (1 if value else 0, telegram_id),
        )
        await db.commit()


async def get_show_all_bloggers(telegram_id: int) -> bool:
    async with get_db() as db:
        async with db.execute(
            "SELECT show_all_bloggers FROM users WHERE telegram_id = ?",
            (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else False


async def set_default_fmt(telegram_id: int, fmt: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET default_fmt = ? WHERE telegram_id = ?",
            (fmt, telegram_id),
        )
        await db.commit()


async def set_output_mode(telegram_id: int, mode: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET output_mode = ? WHERE telegram_id = ?",
            (mode, telegram_id),
        )
        await db.commit()


async def set_manager_filter(telegram_id: int, manager_name: str | None) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET manager_filter = ? WHERE telegram_id = ?",
            (manager_name, telegram_id),
        )
        await db.commit()


async def get_all_methods(blogger_id: int) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM payment_methods WHERE blogger_id = ? ORDER BY added_at",
            (blogger_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_method_by_id(method_id: int) -> dict | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT * FROM payment_methods WHERE id = ?", (method_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def deactivate_method(method_id: int) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE payment_methods SET is_active = 0 WHERE id = ?", (method_id,)
        )
        await db.commit()


async def reactivate_method(method_id: int) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE payment_methods SET is_active = 1 WHERE id = ?", (method_id,)
        )
        await db.commit()


async def update_method_address(
    method_id: int, address: str, label: str | None = None
) -> None:
    async with get_db() as db:
        # Save old address to history before overwriting
        async with db.execute(
            "SELECT blogger_id, type, address FROM payment_methods WHERE id = ?",
            (method_id,)
        ) as cur:
            old = await cur.fetchone()
        if old and old[2] and old[2] != address:
            await db.execute(
                "INSERT INTO payment_method_history (blogger_id, type, address) VALUES (?, ?, ?)",
                (old[0], old[1], old[2])
            )
        await db.execute(
            "UPDATE payment_methods SET address = ?, label = ? WHERE id = ?",
            (address, label, method_id),
        )
        await db.commit()


# =========================================================================== #
# PAYOUTS
# =========================================================================== #

async def save_payout(
    blogger_id: int,
    manager_id: int,
    amount_raw: str,
    method_id: int | None,
    videos_count: int,
    game: str | None,
    mode: str,
    raw_input: str,
    formatted_text: str,
) -> dict:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO payouts
                (blogger_id, manager_id, amount_raw, method_id,
                 videos_count, game, mode, raw_input, formatted_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blogger_id, manager_id, amount_raw, method_id,
                videos_count, game, mode, raw_input, formatted_text,
            ),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM payouts WHERE blogger_id = ? ORDER BY id DESC LIMIT 1",
            (blogger_id,),
        ) as cur:
            return dict(await cur.fetchone())



async def get_payouts_for_blogger(blogger_id: int, limit: int = 0) -> list[dict]:
    """limit=0 means all."""
    async with get_db() as db:
        sql = """
            SELECT p.*, u.username AS manager_username
            FROM payouts p
            JOIN users u ON p.manager_id = u.id
            WHERE p.blogger_id = ?
            ORDER BY p.created_at DESC
        """
        params = [blogger_id]
        if limit > 0:
            sql += " LIMIT ?"
            params.append(limit)
        async with db.execute(sql, params) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_recent_blogger_ids(manager_id: int, limit: int = 8) -> list[int]:
    """Return IDs of bloggers paid most recently (unique, ordered by last payout)."""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT DISTINCT blogger_id
            FROM payouts
            WHERE manager_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (manager_id, limit * 3),  # fetch more to dedupe
        ) as cur:
            rows = await cur.fetchall()
    seen = []
    for r in rows:
        if r[0] not in seen:
            seen.append(r[0])
        if len(seen) >= limit:
            break
    return seen


async def get_recent_payouts(manager_id: int, limit: int = 20) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT p.*, b.name AS blogger_name
            FROM payouts p JOIN bloggers b ON p.blogger_id = b.id
            WHERE p.manager_id = ?
            ORDER BY p.created_at DESC LIMIT ?
            """,
            (manager_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_all_recent_payouts(limit: int = 50) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT p.*, b.name AS blogger_name, u.username AS manager_username
            FROM payouts p
            JOIN bloggers b ON p.blogger_id = b.id
            JOIN users u ON p.manager_id = u.id
            ORDER BY p.created_at DESC LIMIT ?
            """,
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# =========================================================================== #
# LOGS
# =========================================================================== #

async def db_log(
    user_id: int | None,
    action: str,
    details: str | None,
    level: str = "INFO",
) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO logs (user_id, action, details, level) VALUES (?, ?, ?, ?)",
            (user_id, action, details, level),
        )
        await db.commit()


async def get_recent_logs(limit: int = 50) -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            """
            SELECT l.*, u.username AS username
            FROM logs l LEFT JOIN users u ON l.user_id = u.id
            ORDER BY l.created_at DESC LIMIT ?
            """,
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]