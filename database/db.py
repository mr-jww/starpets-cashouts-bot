"""
Database initialisation and schema.
All queries live in queries.py.
"""

import aiosqlite
import os
from config import DB_PATH
from services.logger import log_system

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    username    TEXT,
    role        TEXT NOT NULL DEFAULT 'manager',
    lang        TEXT NOT NULL DEFAULT 'ru',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS bloggers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    manager_id  INTEGER NOT NULL REFERENCES users(id),
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(name, manager_id)
);

CREATE TABLE IF NOT EXISTS payment_methods (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    blogger_id  INTEGER NOT NULL REFERENCES bloggers(id),
    type        TEXT NOT NULL,
    address     TEXT NOT NULL,
    label       TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    added_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS payouts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    blogger_id      INTEGER NOT NULL REFERENCES bloggers(id),
    manager_id      INTEGER NOT NULL REFERENCES users(id),
    amount_raw      TEXT NOT NULL,
    method_id       INTEGER REFERENCES payment_methods(id),
    videos_count    INTEGER NOT NULL DEFAULT 0,
    game            TEXT,
    mode            TEXT NOT NULL DEFAULT 'splite',
    raw_input       TEXT,
    formatted_text  TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id),
    action      TEXT NOT NULL,
    details     TEXT,
    level       TEXT NOT NULL DEFAULT 'INFO',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()
    log_system("DB_INIT", path=DB_PATH)


async def get_db() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn