"""All database queries grouped by entity."""

from __future__ import annotations
import aiosqlite
from database.db import get_db


# =========================================================================== #
# USERS
# =========================================================================== #

async def upsert_user(telegram_id: int, username: str | None, role: str = "manager") -> dict:
    async with get_db() as db:
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
    """Returns None if duplicate."""
    async with get_db() as db:
        try:
            await db.execute(
                "INSERT INTO bloggers (name, manager_id, notes) VALUES (?, ?, ?)",
                (name, manager_id, notes),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            return None
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


async def update_blogger_notes(blogger_id: int, notes: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE bloggers SET notes = ? WHERE id = ?", (notes, blogger_id)
        )
        await db.commit()


# =========================================================================== #
# PAYMENT METHODS
# =========================================================================== #

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
    async with get_db() as db:
        await db.execute(
            "INSERT INTO payment_methods (blogger_id, type, address, label) VALUES (?, ?, ?, ?)",
            (blogger_id, method_type, address, label),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM payment_methods WHERE blogger_id = ? ORDER BY id DESC LIMIT 1",
            (blogger_id,),
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