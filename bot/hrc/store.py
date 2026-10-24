"""Postgres tables for the reserve tape and for quotes.

``schema.sql`` is the Postgres DDL. Tests apply the same column names on
sqlite, which is the hermetic stand-in: it does not start a server. A row
is written only for a reading that was actually taken. An unreadable hour
is left out. It is not inserted as a utilization of zero.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from bot.hrc.model import MODEL_VER

_DDL_PATH = Path(__file__).with_name("schema.sql")

_SQLITE = """\
CREATE TABLE IF NOT EXISTS reserve_samples (
    ts_utc TEXT NOT NULL,
    src TEXT NOT NULL,
    token INTEGER NOT NULL,
    total_supplied TEXT NOT NULL,
    total_borrowed TEXT NOT NULL,
    util TEXT,
    borrow_rate TEXT,
    supply_rate TEXT,
    l1_block INTEGER
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id TEXT NOT NULL,
    side TEXT NOT NULL,
    notional TEXT NOT NULL,
    premium TEXT,
    model_ver TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def postgres_ddl() -> str:
    """The committed Postgres DDL, verbatim."""
    return _DDL_PATH.read_text(encoding="utf-8")


def columns(ddl: str, table: str) -> list[str]:
    """Column names in the order a CREATE TABLE lists them."""
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\)",
        ddl,
        re.S,
    )
    if match is None:
        return []
    names = []
    for line in match.group(1).splitlines():
        part = line.strip().rstrip(",")
        if not part or part.startswith("--"):
            continue
        names.append(part.split()[0])
    return names


def ensure(conn: sqlite3.Connection) -> None:
    """Create the sqlite mirror. Column names match the Postgres DDL."""
    conn.executescript(_SQLITE)


def _text(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, Decimal):
        return None
    return format(value, "f")


def record_sample(
    conn: sqlite3.Connection,
    *,
    ts_utc: str,
    src: str,
    token: int,
    total_supplied: Decimal,
    total_borrowed: Decimal,
    util: Optional[Decimal],
    borrow_rate: Optional[Decimal],
    supply_rate: Optional[Decimal],
    l1_block: Optional[int],
) -> bool:
    """Insert one taken sample. Refuse a row whose totals were not read.

    ``util`` and the rates may be None. They are stored as NULL. Zero is
    written only when the caller measured zero.
    """
    if not isinstance(ts_utc, str) or not ts_utc:
        return False
    if not isinstance(src, str) or not src:
        return False
    if type(token) is not int or token < 0:
        return False
    if not isinstance(total_supplied, Decimal) or not isinstance(total_borrowed, Decimal):
        return False
    if total_supplied <= 0 or total_borrowed < 0:
        return False
    if l1_block is not None and (type(l1_block) is not int or l1_block < 0):
        return False
    conn.execute(
        """
        INSERT INTO reserve_samples (
            ts_utc, src, token, total_supplied, total_borrowed,
            util, borrow_rate, supply_rate, l1_block
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts_utc, src, token, _text(total_supplied), _text(total_borrowed),
            _text(util), _text(borrow_rate), _text(supply_rate), l1_block,
        ),
    )
    return True


def record_quote(
    conn: sqlite3.Connection,
    *,
    series_id: str,
    side: str,
    notional: Decimal,
    premium: Optional[Decimal],
    model_ver: str = MODEL_VER,
    created_at: Optional[str] = None,
) -> bool:
    """Insert a quote. An unreadable premium is NULL, not zero."""
    if not isinstance(series_id, str) or not series_id:
        return False
    if side not in ("buy", "sell"):
        return False
    if not isinstance(notional, Decimal) or notional <= 0:
        return False
    if not isinstance(model_ver, str) or not model_ver:
        return False
    if premium is not None and not isinstance(premium, Decimal):
        return False
    when = created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        """
        INSERT INTO quotes (series_id, side, notional, premium, model_ver, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (series_id, side, _text(notional), _text(premium), model_ver, when),
    )
    return True
