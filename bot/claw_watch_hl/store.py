"""Addresses and snapshots, keyed only by an HMAC of the chat id.

The chat id is not a column. ``forget`` deletes every row for that key.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from bot.approval_hygiene import MAX_ADDRESSES, normalize_address

_KEY_LEN = 64


class HlStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS hl_address ("
                " chat_key TEXT NOT NULL, address TEXT NOT NULL,"
                " added_at TEXT NOT NULL, PRIMARY KEY (chat_key, address))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS hl_snapshot ("
                " chat_key TEXT NOT NULL, address TEXT NOT NULL,"
                " body TEXT NOT NULL, PRIMARY KEY (chat_key, address))"
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _key(self, chat_key: str) -> str:
        if not isinstance(chat_key, str) or len(chat_key) != _KEY_LEN:
            raise ValueError("key")
        if any(ch not in "0123456789abcdef" for ch in chat_key):
            raise ValueError("key")
        return chat_key

    def listed_addresses(self, chat_key: str) -> list[str]:
        key = self._key(chat_key)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT address FROM hl_address WHERE chat_key = ? ORDER BY added_at, address",
                (key,),
            ).fetchall()
        return [str(r["address"]) for r in rows]

    def add_address(self, chat_key: str, address: str, added_at: str) -> Optional[str]:
        """Store one address. Returns an error word, or None when it was stored."""
        key = self._key(chat_key)
        addr = normalize_address(address)
        if addr is None:
            return "address"
        with self._lock, self._connect() as conn:
            have = conn.execute(
                "SELECT address FROM hl_address WHERE chat_key = ?", (key,),
            ).fetchall()
            owned = {str(r["address"]) for r in have}
            if addr in owned:
                return None
            if len(owned) >= MAX_ADDRESSES:
                return "cap"
            conn.execute(
                "INSERT INTO hl_address (chat_key, address, added_at) VALUES (?, ?, ?)",
                (key, addr, added_at),
            )
            conn.commit()
        return None

    def forget(self, chat_key: str) -> bool:
        key = self._key(chat_key)
        with self._lock, self._connect() as conn:
            a = conn.execute("DELETE FROM hl_address WHERE chat_key = ?", (key,))
            b = conn.execute("DELETE FROM hl_snapshot WHERE chat_key = ?", (key,))
            conn.commit()
            return (a.rowcount + b.rowcount) > 0

    def snapshot(self, chat_key: str, address: str) -> Optional[dict]:
        key = self._key(chat_key)
        addr = normalize_address(address)
        if addr is None:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT body FROM hl_snapshot WHERE chat_key = ? AND address = ?",
                (key, addr),
            ).fetchone()
        if row is None:
            return None
        try:
            body = json.loads(row["body"])
        except json.JSONDecodeError:
            return None
        return body if isinstance(body, dict) else None

    def snapshot_addresses(self, chat_key: str) -> list[str]:
        key = self._key(chat_key)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT address FROM hl_snapshot WHERE chat_key = ?", (key,),
            ).fetchall()
        return [str(r["address"]) for r in rows]

    def save_snapshot(self, chat_key: str, address: str, body: dict) -> None:
        key = self._key(chat_key)
        addr = normalize_address(address)
        if addr is None:
            raise ValueError("address")
        payload = json.dumps(body, sort_keys=True, default=str)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO hl_snapshot (chat_key, address, body) VALUES (?, ?, ?)"
                " ON CONFLICT(chat_key, address) DO UPDATE SET body = excluded.body",
                (key, addr, payload),
            )
            conn.commit()

    def drop_snapshot(self, chat_key: str, address: str) -> None:
        key = self._key(chat_key)
        addr = normalize_address(address)
        if addr is None:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM hl_snapshot WHERE chat_key = ? AND address = ?",
                (key, addr),
            )
            conn.commit()
