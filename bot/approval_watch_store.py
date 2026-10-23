"""Addresses a chat asked us to watch, and when the next digest is due.

The only key is a 64-hex HMAC of the chat id (``player_key``). The chat id is
not a column. A value that is not 64 hex characters is refused, so a raw
Telegram id cannot be written by passing it as the key. ``/stop`` deletes
every address and the schedule row for that key. The file is the digest's
own; it is not the duel call store.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from bot.approval_hygiene import _ISO, MAX_ADDRESSES, normalize_address

_KEY = re.compile(r"^[0-9a-f]{64}$")


class ApprovalWatchStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS approval_watches ("
                " chat_key TEXT NOT NULL,"
                " address TEXT NOT NULL,"
                " added_at TEXT NOT NULL,"
                " PRIMARY KEY (chat_key, address))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS approval_schedule ("
                " chat_key TEXT PRIMARY KEY,"
                " next_digest_at TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _key(chat_key: str) -> str:
        if not _KEY.fullmatch(chat_key or ""):
            raise ValueError("key")
        return chat_key

    @staticmethod
    def _when(text: str) -> str:
        if not _ISO.fullmatch(text or ""):
            raise ValueError("time")
        return text

    def addresses(self, chat_key: str) -> list[str]:
        key = self._key(chat_key)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT address FROM approval_watches WHERE chat_key = ? ORDER BY added_at, address",
                (key,),
            ).fetchall()
        return [str(r["address"]) for r in rows]

    def add(self, chat_key: str, address: str, added_at: str,
            next_digest_at: Optional[str] = None) -> tuple[str, int]:
        """``(status, count)``. status is ``added``, ``duplicate`` or ``full``.

        ``full`` and ``duplicate`` write nothing. When ``next_digest_at`` is
        given, the schedule row is inserted in the same transaction as the
        address, and an existing schedule is left where it is.
        """
        key = self._key(chat_key)
        addr = normalize_address(address)
        when = self._when(added_at)
        nxt = self._when(next_digest_at) if next_digest_at is not None else None
        if addr is None:
            raise ValueError("address")
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT address FROM approval_watches WHERE chat_key = ?",
                (key,),
            ).fetchall()
            held = [str(r["address"]) for r in rows]
            if addr in held:
                return "duplicate", len(held)
            if len(held) >= MAX_ADDRESSES:
                return "full", len(held)
            conn.execute(
                "INSERT INTO approval_watches (chat_key, address, added_at) VALUES (?, ?, ?)",
                (key, addr, when),
            )
            if nxt is not None:
                conn.execute(
                    "INSERT INTO approval_schedule (chat_key, next_digest_at) VALUES (?, ?)"
                    " ON CONFLICT(chat_key) DO NOTHING",
                    (key, nxt),
                )
            conn.commit()
            n = len(held) + 1
        return "added", n

    def schedule_next(self, chat_key: str, next_digest_at: str) -> None:
        key = self._key(chat_key)
        when = self._when(next_digest_at)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO approval_schedule (chat_key, next_digest_at) VALUES (?, ?)"
                " ON CONFLICT(chat_key) DO UPDATE SET next_digest_at = excluded.next_digest_at",
                (key, when),
            )
            conn.commit()

    def next_digest_at(self, chat_key: str) -> Optional[str]:
        key = self._key(chat_key)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT next_digest_at FROM approval_schedule WHERE chat_key = ?",
                (key,),
            ).fetchone()
        return str(row["next_digest_at"]) if row is not None else None

    def due(self, now_iso: str) -> list[str]:
        """Chat keys whose digest is due and that still have an address.

        The timestamp is the fixed-width UTC form ``format_iso`` writes, which
        sorts in time order. A chat with a schedule and no addresses is not due.
        """
        when = self._when(now_iso)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT s.chat_key FROM approval_schedule s"
                " WHERE s.next_digest_at <= ?"
                " AND EXISTS (SELECT 1 FROM approval_watches w WHERE w.chat_key = s.chat_key)"
                " ORDER BY s.next_digest_at",
                (when,),
            ).fetchall()
        return [str(r["chat_key"]) for r in rows]

    def forget(self, chat_key: str) -> bool:
        """Delete every address and the schedule for this chat. True if a row went."""
        key = self._key(chat_key)
        with self._lock, self._connect() as conn:
            addresses = conn.execute("DELETE FROM approval_watches WHERE chat_key = ?", (key,))
            schedule = conn.execute("DELETE FROM approval_schedule WHERE chat_key = ?", (key,))
            conn.commit()
            removed = addresses.rowcount + schedule.rowcount
        return removed > 0
