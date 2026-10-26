"""SQLite book for one chat's watched addresses. Not the approval book.

A failed read is None. An empty table is an empty list or a spent count
of 0, which is a real reading. The chat id is stored only as an HMAC.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from bot.claw_watch_heuristics.flag import CALLS_PER_WALLET_WEEK, WEEK_SECONDS

_LOCK = threading.Lock()
_CONN: dict[str, sqlite3.Connection] = {}


def _connect(path: str) -> sqlite3.Connection:
    existing = _CONN.get(path)
    if existing is not None:
        return existing
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS watches (
            chat_key TEXT NOT NULL,
            address TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            added_at INTEGER NOT NULL,
            PRIMARY KEY (chat_key, address, chain_id)
        );
        CREATE TABLE IF NOT EXISTS budget (
            address TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            ts INTEGER NOT NULL,
            calls INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS seen (
            address TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            tx_hash TEXT NOT NULL,
            PRIMARY KEY (address, chain_id, tx_hash)
        );
        CREATE TABLE IF NOT EXISTS memory (
            address TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            blob TEXT NOT NULL,
            PRIMARY KEY (address, chain_id)
        );
        CREATE TABLE IF NOT EXISTS cursor (
            address TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            block INTEGER NOT NULL,
            PRIMARY KEY (address, chain_id)
        );
        CREATE TABLE IF NOT EXISTS last_poll (
            address TEXT NOT NULL,
            chain_id INTEGER NOT NULL,
            ts INTEGER NOT NULL,
            PRIMARY KEY (address, chain_id)
        );
        """
    )
    _CONN[path] = conn
    return conn


def _blank_memory() -> dict:
    return {"trusted": [], "tainted": {}, "keys": {}}


def watch_add(path: str, chat_key: str, address: str, chain_id: int, now: int, limit: int) -> str:
    """``added``, ``duplicate``, ``full``, or ``unreadable``."""
    try:
        with _LOCK:
            conn = _connect(path)
            rows = conn.execute(
                "SELECT address FROM watches WHERE chat_key = ?",
                (chat_key,),
            ).fetchall()
            have = {row[0] for row in rows}
            if address in have:
                return "duplicate"
            if len(have) >= limit:
                return "full"
            conn.execute(
                "INSERT INTO watches (chat_key, address, chain_id, added_at) VALUES (?, ?, ?, ?)",
                (chat_key, address, chain_id, now),
            )
            conn.commit()
        return "added"
    except Exception:
        return "unreadable"


def watch_remove(path: str, chat_key: str, address: str) -> Optional[bool]:
    """True when a row was removed, False when none matched, None when unreadable."""
    try:
        with _LOCK:
            conn = _connect(path)
            cur = conn.execute(
                "DELETE FROM watches WHERE chat_key = ? AND address = ?",
                (chat_key, address),
            )
            conn.commit()
            return cur.rowcount > 0
    except Exception:
        return None


def watch_list(path: str, chat_key: str) -> Optional[list[tuple[str, int]]]:
    """Address and chain id, or None when the book could not be read."""
    try:
        with _LOCK:
            conn = _connect(path)
            rows = conn.execute(
                "SELECT address, chain_id FROM watches WHERE chat_key = ? ORDER BY added_at",
                (chat_key,),
            ).fetchall()
        return [(str(row[0]), int(row[1])) for row in rows]
    except Exception:
        return None


def watch_all(path: str) -> Optional[list[tuple[str, str, int]]]:
    """Every row: chat key, address, chain id. None when unreadable."""
    try:
        with _LOCK:
            conn = _connect(path)
            rows = conn.execute(
                "SELECT chat_key, address, chain_id FROM watches ORDER BY added_at",
            ).fetchall()
        return [(str(a), str(b), int(c)) for a, b, c in rows]
    except Exception:
        return None


def budget_spent(path: str, address: str, chain_id: int, now: int) -> Optional[int]:
    """Calls in the rolling week. 0 is a real empty ledger. None is unreadable."""
    try:
        with _LOCK:
            conn = _connect(path)
            row = conn.execute(
                "SELECT COALESCE(SUM(calls), 0) FROM budget WHERE address = ? AND chain_id = ? AND ts >= ?",
                (address, chain_id, now - WEEK_SECONDS),
            ).fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0])
    except Exception:
        return None


def budget_charge(path: str, address: str, chain_id: int, now: int, calls: int) -> bool:
    if calls < 0:
        return False
    try:
        with _LOCK:
            conn = _connect(path)
            conn.execute(
                "INSERT INTO budget (address, chain_id, ts, calls) VALUES (?, ?, ?, ?)",
                (address, chain_id, now, calls),
            )
            conn.commit()
        return True
    except Exception:
        return False


def poll_block(path: str, address: str, chain_id: int, now: int,
               min_interval: int, planned: int) -> Optional[str]:
    """None when a poll may start. Otherwise ``budget``, ``interval``, or ``unreadable``.

    A spent count that could not be read does not become zero remaining.
    """
    spent = budget_spent(path, address, chain_id, now)
    if spent is None:
        return "unreadable"
    remaining = CALLS_PER_WALLET_WEEK - spent
    if remaining < planned:
        return "budget"
    try:
        with _LOCK:
            conn = _connect(path)
            row = conn.execute(
                "SELECT ts FROM last_poll WHERE address = ? AND chain_id = ?",
                (address, chain_id),
            ).fetchone()
    except Exception:
        return "unreadable"
    if row is None:
        return None
    try:
        last = int(row[0])
    except (TypeError, ValueError):
        return "unreadable"
    if now - last < min_interval:
        return "interval"
    return None


def mark_polled(path: str, address: str, chain_id: int, now: int) -> bool:
    try:
        with _LOCK:
            conn = _connect(path)
            conn.execute(
                "INSERT INTO last_poll (address, chain_id, ts) VALUES (?, ?, ?) "
                "ON CONFLICT(address, chain_id) DO UPDATE SET ts = excluded.ts",
                (address, chain_id, now),
            )
            conn.commit()
        return True
    except Exception:
        return False


def cursor_of(path: str, address: str, chain_id: int) -> tuple[str, Optional[int]]:
    """``(missing|read|unreadable, block)``. Missing is the first poll, not an error."""
    try:
        with _LOCK:
            conn = _connect(path)
            row = conn.execute(
                "SELECT block FROM cursor WHERE address = ? AND chain_id = ?",
                (address, chain_id),
            ).fetchone()
    except Exception:
        return ("unreadable", None)
    if row is None:
        return ("missing", None)
    try:
        return ("read", int(row[0]))
    except (TypeError, ValueError):
        return ("unreadable", None)


def set_cursor(path: str, address: str, chain_id: int, block: int) -> bool:
    try:
        with _LOCK:
            conn = _connect(path)
            conn.execute(
                "INSERT INTO cursor (address, chain_id, block) VALUES (?, ?, ?) "
                "ON CONFLICT(address, chain_id) DO UPDATE SET block = excluded.block",
                (address, chain_id, block),
            )
            conn.commit()
        return True
    except Exception:
        return False


def has_seen(path: str, address: str, chain_id: int, tx_hash: str) -> Optional[bool]:
    try:
        with _LOCK:
            conn = _connect(path)
            row = conn.execute(
                "SELECT 1 FROM seen WHERE address = ? AND chain_id = ? AND tx_hash = ?",
                (address, chain_id, tx_hash),
            ).fetchone()
        return row is not None
    except Exception:
        return None


def mark_seen(path: str, address: str, chain_id: int, tx_hash: str) -> bool:
    try:
        with _LOCK:
            conn = _connect(path)
            conn.execute(
                "INSERT OR IGNORE INTO seen (address, chain_id, tx_hash) VALUES (?, ?, ?)",
                (address, chain_id, tx_hash),
            )
            conn.commit()
        return True
    except Exception:
        return False


def load_memory(path: str, address: str, chain_id: int) -> Optional[dict]:
    """The stored memory, ``{}``-shaped when no row exists, None when unreadable."""
    try:
        with _LOCK:
            conn = _connect(path)
            row = conn.execute(
                "SELECT blob FROM memory WHERE address = ? AND chain_id = ?",
                (address, chain_id),
            ).fetchone()
    except Exception:
        return None
    if row is None:
        return _blank_memory()
    try:
        parsed = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def save_memory(path: str, address: str, chain_id: int, memory: dict) -> bool:
    try:
        blob = json.dumps(memory, sort_keys=True)
    except (TypeError, ValueError):
        return False
    try:
        with _LOCK:
            conn = _connect(path)
            conn.execute(
                "INSERT INTO memory (address, chain_id, blob) VALUES (?, ?, ?) "
                "ON CONFLICT(address, chain_id) DO UPDATE SET blob = excluded.blob",
                (address, chain_id, blob),
            )
            conn.commit()
        return True
    except Exception:
        return False
