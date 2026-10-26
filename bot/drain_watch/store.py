"""Addresses and watch state, keyed only by an HMAC.

The chat id is not a column. ``forget`` deletes every row for that key:
addresses, cursors, delegates, remembered approvals, counterparties, hidden
dust labels and seen events. The file is this watch's own. It is not the
weekly digest store.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from bot.approval_hygiene import MAX_ADDRESSES, normalize_address
from bot.approval_watch_store import ApprovalWatchStore

_KEY = re.compile(r"^[0-9a-f]{64}$")
_ADDR = re.compile(r"^0x[0-9a-f]{40}$")
_ID = re.compile(r"^[a-z0-9:_-]{1,80}$")


class DrainWatchStore(ApprovalWatchStore):
    """The digest store's address table, plus the rows this watch adds."""

    def __init__(self, path: Path):
        super().__init__(path)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS drain_cursor ("
                " chat_key TEXT NOT NULL, chain_id INTEGER NOT NULL,"
                " block INTEGER NOT NULL, PRIMARY KEY (chat_key, chain_id))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS drain_delegate ("
                " chat_key TEXT NOT NULL, chain_id INTEGER NOT NULL,"
                " status TEXT NOT NULL, delegate TEXT NOT NULL,"
                " PRIMARY KEY (chat_key, chain_id))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS drain_approval ("
                " chat_key TEXT NOT NULL, chain_id INTEGER NOT NULL,"
                " token TEXT NOT NULL, spender TEXT NOT NULL,"
                " updated INTEGER, unlimited TEXT NOT NULL,"
                " PRIMARY KEY (chat_key, chain_id, token, spender))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS drain_counterparty ("
                " chat_key TEXT NOT NULL, address TEXT NOT NULL,"
                " PRIMARY KEY (chat_key, address))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS drain_seen ("
                " chat_key TEXT NOT NULL, event_id TEXT NOT NULL,"
                " PRIMARY KEY (chat_key, event_id))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS drain_hidden ("
                " chat_key TEXT NOT NULL, event_id TEXT NOT NULL,"
                " label TEXT NOT NULL, PRIMARY KEY (chat_key, event_id))"
            )
            conn.commit()

    def forget(self, chat_key: str) -> bool:
        """Delete every row for this key. True if a row went."""
        key = self._key(chat_key)
        statements = (
            "DELETE FROM approval_watches WHERE chat_key = ?",
            "DELETE FROM approval_schedule WHERE chat_key = ?",
            "DELETE FROM drain_cursor WHERE chat_key = ?",
            "DELETE FROM drain_delegate WHERE chat_key = ?",
            "DELETE FROM drain_approval WHERE chat_key = ?",
            "DELETE FROM drain_counterparty WHERE chat_key = ?",
            "DELETE FROM drain_seen WHERE chat_key = ?",
            "DELETE FROM drain_hidden WHERE chat_key = ?",
        )
        removed = 0
        with self._lock, self._connect() as conn:
            for sql in statements:
                cur = conn.execute(sql, (key,))
                removed += cur.rowcount
            conn.commit()
        return removed > 0

    def cursor(self, chat_key: str, chain_id: int) -> Optional[int]:
        key = self._key(chat_key)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT block FROM drain_cursor WHERE chat_key = ? AND chain_id = ?",
                (key, chain_id),
            ).fetchone()
        return int(row["block"]) if row is not None else None

    def set_cursor(self, chat_key: str, chain_id: int, block: int) -> None:
        key = self._key(chat_key)
        if block < 0:
            raise ValueError("block")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO drain_cursor (chat_key, chain_id, block) VALUES (?, ?, ?)"
                " ON CONFLICT(chat_key, chain_id) DO UPDATE SET block = excluded.block",
                (key, chain_id, block),
            )
            conn.commit()

    def delegate(self, chat_key: str, chain_id: int) -> Optional[tuple[str, str]]:
        key = self._key(chat_key)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT status, delegate FROM drain_delegate WHERE chat_key = ? AND chain_id = ?",
                (key, chain_id),
            ).fetchone()
        if row is None:
            return None
        return str(row["status"]), str(row["delegate"])

    def set_delegate(self, chat_key: str, chain_id: int, status: str, delegate: str) -> None:
        key = self._key(chat_key)
        if status not in ("none", "delegated"):
            raise ValueError("status")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO drain_delegate (chat_key, chain_id, status, delegate)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(chat_key, chain_id) DO UPDATE SET"
                " status = excluded.status, delegate = excluded.delegate",
                (key, chain_id, status, delegate.lower()),
            )
            conn.commit()

    def approval(self, chat_key: str, chain_id: int, token: str, spender: str) -> Optional[dict]:
        key = self._key(chat_key)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT updated, unlimited FROM drain_approval"
                " WHERE chat_key = ? AND chain_id = ? AND token = ? AND spender = ?",
                (key, chain_id, token.lower(), spender.lower()),
            ).fetchone()
        if row is None:
            return None
        updated = row["updated"]
        return {
            "updated": None if updated is None else int(updated),
            "unlimited": str(row["unlimited"]),
        }

    def save_approval(
        self, chat_key: str, chain_id: int, token: str, spender: str,
        updated: Optional[int], unlimited: str,
    ) -> None:
        key = self._key(chat_key)
        if unlimited not in ("yes", "no", "unread"):
            raise ValueError("unlimited")
        if not _ADDR.fullmatch(token) or not _ADDR.fullmatch(spender):
            raise ValueError("address")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO drain_approval"
                " (chat_key, chain_id, token, spender, updated, unlimited)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(chat_key, chain_id, token, spender) DO UPDATE SET"
                " updated = excluded.updated, unlimited = excluded.unlimited",
                (key, chain_id, token.lower(), spender.lower(), updated, unlimited),
            )
            conn.commit()

    def counterparties(self, chat_key: str) -> list[str]:
        key = self._key(chat_key)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT address FROM drain_counterparty WHERE chat_key = ? ORDER BY address",
                (key,),
            ).fetchall()
        return [str(r["address"]) for r in rows]

    def add_counterparty(self, chat_key: str, address: str) -> None:
        key = self._key(chat_key)
        addr = normalize_address(address)
        if addr is None:
            raise ValueError("address")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO drain_counterparty (chat_key, address) VALUES (?, ?)"
                " ON CONFLICT(chat_key, address) DO NOTHING",
                (key, addr),
            )
            conn.commit()

    def has_seen(self, chat_key: str, event_id: str) -> bool:
        key = self._key(chat_key)
        if not _ID.fullmatch(event_id):
            return False
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM drain_seen WHERE chat_key = ? AND event_id = ?",
                (key, event_id),
            ).fetchone()
        return row is not None

    def mark_seen(self, chat_key: str, event_id: str) -> None:
        key = self._key(chat_key)
        if not _ID.fullmatch(event_id):
            raise ValueError("event")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO drain_seen (chat_key, event_id) VALUES (?, ?)"
                " ON CONFLICT(chat_key, event_id) DO NOTHING",
                (key, event_id),
            )
            conn.commit()

    def hide(self, chat_key: str, event_id: str, label: str) -> None:
        key = self._key(chat_key)
        if not _ID.fullmatch(event_id):
            raise ValueError("event")
        if label not in ("dust", "lookalike_dust"):
            raise ValueError("label")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO drain_hidden (chat_key, event_id, label) VALUES (?, ?, ?)"
                " ON CONFLICT(chat_key, event_id) DO NOTHING",
                (key, event_id, label),
            )
            conn.commit()

    def hidden(self, chat_key: str) -> list[tuple[str, str]]:
        key = self._key(chat_key)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT event_id, label FROM drain_hidden WHERE chat_key = ? ORDER BY event_id",
                (key,),
            ).fetchall()
        return [(str(r["event_id"]), str(r["label"])) for r in rows]


def address_cap() -> int:
    return MAX_ADDRESSES
