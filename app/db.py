"""SQLite-lagring för bibliotek, matchningar och tittarpositioner."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Iterable

from .config import DB_PATH

_local = threading.local()
_init_lock = threading.Lock()
_initialized = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL DEFAULT 'movie',      -- movie | episode
    title TEXT,
    show_title TEXT,
    show_key TEXT,
    year INTEGER,
    season INTEGER,
    episode INTEGER,
    size INTEGER,
    mtime REAL,
    duration REAL,
    width INTEGER,
    height INTEGER,
    vcodec TEXT,
    acodec TEXT,
    channels INTEGER,
    container TEXT,
    pix_fmt TEXT,
    profile TEXT,
    level INTEGER,
    fps REAL,
    probed INTEGER NOT NULL DEFAULT 0,
    tmdb_id INTEGER,
    match_json TEXT,
    match_manual INTEGER NOT NULL DEFAULT 0,
    match_state TEXT NOT NULL DEFAULT 'pending', -- pending | matched | failed | manual
    added_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0,
    missing INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind);
CREATE INDEX IF NOT EXISTS idx_items_show ON items(show_key);

CREATE TABLE IF NOT EXISTS shows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    title TEXT,
    year INTEGER,
    tmdb_id INTEGER,
    match_json TEXT,
    match_manual INTEGER NOT NULL DEFAULT 0,
    match_state TEXT NOT NULL DEFAULT 'pending',
    updated_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS progress (
    item_id INTEGER PRIMARY KEY,
    position REAL NOT NULL DEFAULT 0,
    duration REAL NOT NULL DEFAULT 0,
    watched INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS imports (
    hash TEXT PRIMARY KEY,
    name TEXT,
    item_id INTEGER,
    path TEXT,
    imported_at REAL,
    status TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


def conn() -> sqlite3.Connection:
    global _initialized
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.conn = c
    if not _initialized:
        with _init_lock:
            if not _initialized:
                c.executescript(SCHEMA)
                _migrate(c)
                c.commit()
                _initialized = True
    return c


def _migrate(c: sqlite3.Connection) -> None:
    """Lägg till nya kolumner i en äldre databas utan att tappa data."""
    wanted = {
        "pix_fmt": "TEXT", "profile": "TEXT", "level": "INTEGER", "fps": "REAL",
        "channels": "INTEGER",
    }
    have = {row[1] for row in c.execute("PRAGMA table_info(items)").fetchall()}
    for name, kind in wanted.items():
        if name not in have:
            c.execute(f"ALTER TABLE items ADD COLUMN {name} {kind}")
    if "channels" not in have:
        # Teknisk info saknas i gamla rader – proba om dem.
        c.execute("UPDATE items SET probed = 0")


def q(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return conn().execute(sql, tuple(params)).fetchall()


def q1(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return conn().execute(sql, tuple(params)).fetchone()


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    c = conn()
    cur = c.execute(sql, tuple(params))
    c.commit()
    return cur


def upsert_item(item: dict) -> int:
    now = time.time()
    existing = q1("SELECT id FROM items WHERE path = ?", (item["path"],))
    if existing:
        item_id = existing["id"]
        fields = {k: v for k, v in item.items() if k not in ("id", "path")}
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            execute(f"UPDATE items SET {sets}, updated_at = ? WHERE id = ?", (*fields.values(), now, item_id))
        return item_id
    item = dict(item)
    item.setdefault("added_at", now)
    item["updated_at"] = now
    cols = ", ".join(item.keys())
    ph = ", ".join("?" for _ in item)
    cur = execute(f"INSERT INTO items ({cols}) VALUES ({ph})", item.values())
    return int(cur.lastrowid)


def json_load(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def get_kv(key: str, default: Any = None) -> Any:
    row = q1("SELECT v FROM kv WHERE k = ?", (key,))
    return json.loads(row["v"]) if row else default


def set_kv(key: str, value: Any) -> None:
    execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, json.dumps(value)))
