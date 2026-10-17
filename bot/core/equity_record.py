"""One live equity row per UTC day, so live can have a Sharpe ratio.

The 17 Oct edge audit could give live a profit factor and a win rate but no
Sharpe: nothing kept a daily equity series. The risk engine's
`record_equity_snapshot` is in memory and per evaluation, and the website's
`record_equity` is per process. This appends one row per day to a JSONL file
under the state directory, from a reading the venue answered that day.

Three facts, kept apart: a day already recorded is left as it is, a reading
that cannot be read records nothing (never a zero), and a recorded row says
when it was read and from what.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from bot.utils.paths import env_state_path

RECORDED = "recorded"
ALREADY = "already recorded today"
UNREADABLE = "unreadable"


def equity_log_path() -> Path:
    """Where the daily rows live; `EQUITY_LOG_PATH` overrides it."""
    return env_state_path("EQUITY_LOG_PATH", "data/learning/equity_daily.jsonl")


def _money(v: object) -> Optional[float]:
    """A finite number, or None. Zero is a measured balance, not an absence."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def equity_of(reading: object) -> Optional[float]:
    """The equity a balance reading states, or None when it states none.

    A reading that carries an error and no positive total is the failed read
    `get_live_equity` lets through for its own cache; it is not a figure.
    """
    if not isinstance(reading, dict):
        return None
    total = _money(reading.get("total"))
    if total is None:
        return None
    if "error" in reading and total <= 0:
        return None
    return total


def last_recorded_day(path: Path) -> Optional[str]:
    """The ``day`` of the last row, or None for no readable row."""
    try:
        with open(path, "rb") as fh:
            tail = b""
            for line in fh:
                if line.strip():
                    tail = line
        if not tail:
            return None
        row = json.loads(tail)
        day = row.get("day") if isinstance(row, dict) else None
        return day if isinstance(day, str) else None
    except (OSError, ValueError):
        return None


def record_daily_equity(reading: object, *, mode: str, source: str,
                        when: Optional[datetime] = None,
                        path: Optional[Path] = None) -> str:
    """Append today's row from ``reading`` unless today already has one.

    Returns one of RECORDED, ALREADY, UNREADABLE. ``mode`` is the book the
    figure belongs to (``live``); ``source`` names what answered (the venue
    name). An unreadable reading writes nothing and says so.
    """
    now = when or datetime.now(UTC)
    day = now.date().isoformat()
    p = path or equity_log_path()
    equity = equity_of(reading)
    if equity is None or not isinstance(reading, dict):
        return UNREADABLE
    if last_recorded_day(p) == day:
        return ALREADY
    free = _money(reading.get("free"))
    row = {
        "day": day,
        "read_at": now.isoformat(timespec="seconds"),
        "mode": mode,
        "source": source,
        "equity_usd": round(equity, 2),
        "free_usd": None if free is None else round(free, 2),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return RECORDED


def read_daily_equity(path: Optional[Path] = None) -> list[dict]:
    """Every readable row, oldest first. A missing file is an empty series,
    which a reader must say rather than render as a flat line."""
    p = path or equity_log_path()
    out: list[dict] = []
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("day"), str) \
                        and _money(row.get("equity_usd")) is not None:
                    out.append(row)
    except OSError:
        return []
    return out
