"""One sealed call per (anonymous id, duel day).

The id is an HMAC of the Telegram user id. The user id is not a column, and
``record`` refuses a receipt that still carries it. The unique key is the
refusal of a second call: the insert loses, and the first receipt is what
comes back.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Mapping, Optional

_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SALT_MIN = 16


class PayloadRefused(Exception):
    """The receipt still carries the Telegram id. Nothing was written."""

    pass  # a docstring-only body does not parse once code_only blanks it


def player_key(telegram_id: str, salt: str) -> str:
    """Hex HMAC-SHA256. The salt is the key, so a leaked table is not a list
    of Telegram ids. An empty salt is refused: hashing without one is just
    the id, encoded."""
    if not isinstance(salt, str) or len(salt) < _SALT_MIN:
        raise ValueError("salt")
    if not str(telegram_id):
        raise ValueError("id")
    return hmac.new(salt.encode("utf-8"), str(telegram_id).encode("utf-8"), hashlib.sha256).hexdigest()


def payload_carries_id(payload: str, telegram_id: str) -> bool:
    """True when `telegram_id` stands in the receipt as itself.

    A short id that happens to be digits inside a longer number is not it.
    A quoted id, or the id as its own JSON number, is.
    """
    if not telegram_id:
        return False
    if f'"{telegram_id}"' in payload:
        return True
    if len(telegram_id) < 6 or not telegram_id.isdigit():
        return False
    return re.search(rf"(?<![0-9]){re.escape(telegram_id)}(?![0-9])", payload) is not None


class CallStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS calls ("
                " player_key TEXT NOT NULL,"
                " day TEXT NOT NULL,"
                " pick TEXT NOT NULL,"
                " symbol TEXT NOT NULL,"
                " seal TEXT NOT NULL,"
                " seal_payload TEXT NOT NULL,"
                " picked_at TEXT NOT NULL,"
                " resolves_at TEXT NOT NULL,"
                " PRIMARY KEY (player_key, day))"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {k: row[k] for k in row.keys()}

    def for_day(self, player_key: str, day: str) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            found = conn.execute(
                "SELECT player_key, day, pick, symbol, seal, seal_payload, picked_at, resolves_at"
                " FROM calls WHERE player_key = ? AND day = ?",
                (player_key, day),
            ).fetchone()
        return self._row(found) if found is not None else None

    def latest(self, player_key: str) -> Optional[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            found = conn.execute(
                "SELECT player_key, day, pick, symbol, seal, seal_payload, picked_at, resolves_at"
                " FROM calls WHERE player_key = ? ORDER BY day DESC LIMIT 1",
                (player_key,),
            ).fetchone()
        return self._row(found) if found is not None else None

    def picked_at(self, player_key: str) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT picked_at FROM calls WHERE player_key = ? ORDER BY day",
                (player_key,),
            ).fetchall()
        return [str(r["picked_at"]) for r in rows]

    def payloads(self, player_key: str) -> list[str]:
        """The sealed receipts, oldest day first. The rating lives inside them."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT seal_payload FROM calls WHERE player_key = ? ORDER BY day",
                (player_key,),
            ).fetchall()
        return [str(r["seal_payload"]) for r in rows]

    def record(self, row: Mapping[str, Any], *, telegram_id: Optional[str] = None) -> tuple[dict[str, Any], bool]:
        """Insert. ``(stored, created)``. A second call for the same day
        returns the first receipt and ``created=False``; it does not overwrite.

        ``telegram_id``, when given, is a refusal check only. It is not bound
        into the statement.
        """
        key = str(row.get("player_key") or "")
        day = str(row.get("day") or "")
        pick = str(row.get("pick") or "")
        payload = str(row.get("seal_payload") or "")
        if not _KEY_RE.fullmatch(key) or not _DAY_RE.fullmatch(day) or pick not in ("long", "short"):
            raise ValueError("row")
        if not payload or not row.get("seal") or not row.get("symbol"):
            raise ValueError("row")
        if telegram_id and payload_carries_id(payload, str(telegram_id)):
            raise PayloadRefused()
        values = (
            key, day, pick, str(row["symbol"]), str(row["seal"]), payload,
            str(row["picked_at"]), str(row["resolves_at"]),
        )
        with self._lock, self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO calls (player_key, day, pick, symbol, seal, seal_payload,"
                    " picked_at, resolves_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
                conn.commit()
                created = True
            except sqlite3.IntegrityError:
                created = False
            found = conn.execute(
                "SELECT player_key, day, pick, symbol, seal, seal_payload, picked_at, resolves_at"
                " FROM calls WHERE player_key = ? AND day = ?",
                (key, day),
            ).fetchone()
        if found is None:
            raise ValueError("row")
        return self._row(found), created
