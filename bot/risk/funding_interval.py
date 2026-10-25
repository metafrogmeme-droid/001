"""Per-symbol funding settlement interval.

Bitget quotes ``fundingRateInterval`` in hours. The values it enumerates are
1, 2, 4 and 8. The funding clock and the backtest charge both ask here, so
a 4h contract is not billed on the 8h grid.

An unreadable interval is not 8. Eight hours is the fallback the product
used when it had one grid, and ``measured`` is false when that fallback is
what came back. A reading is remembered on disk so the next process does
not have to see the payload again. ``FUNDING_INTERVAL_HOURS`` forces one
grid for every symbol (the reversible old behaviour is ``8``).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

from bot.risk.funding_clock import SETTLEMENT_INTERVAL_SEC
from bot.utils.json_store import StoreUnreadable, load_json_store, update_json_store

#: The one fallback, read from the clock's own constant.
FALLBACK_HOURS = int(SETTLEMENT_INTERVAL_SEC // 3600)
ALLOWED_HOURS = frozenset({1, 2, 4, 8})

_BOOK: Optional["IntervalBook"] = None


def canonical_symbol(symbol: str) -> str:
    """``BTC/USDT``, ``BTC/USDT:USDT`` and ``BTCUSDT`` are one contract."""
    text = str(symbol or "").strip().upper()
    if not text:
        return ""
    text = text.split(":", 1)[0]
    return text.replace("/", "").replace("-", "").replace("_", "")


def parse_interval_hours(raw) -> Optional[int]:
    """Hours, or None when the field is absent, blank, or not 1/2/4/8.

    ``"8h"`` (ccxt) and ``"8"`` (Bitget) are the same reading. ``0``,
    ``""`` and ``None`` are not 8.
    """
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    if text.endswith("h"):
        text = text[:-1].strip()
    try:
        hours = float(text)
    except (TypeError, ValueError):
        return None
    if hours != int(hours):
        return None
    hours = int(hours)
    if hours not in ALLOWED_HOURS:
        return None
    return hours


def read_interval_hours(payload) -> Optional[int]:
    """A ccxt funding payload or a Bitget row. None when it names no interval."""
    if not isinstance(payload, dict):
        return None
    info = payload.get("info")
    if isinstance(info, dict):
        parsed = parse_interval_hours(info.get("fundingRateInterval"))
        if parsed is not None:
            return parsed
    for key in ("fundingRateInterval", "interval"):
        if key in payload:
            parsed = parse_interval_hours(payload.get(key))
            if parsed is not None:
                return parsed
    return None


def override_hours() -> Optional[int]:
    """``FUNDING_INTERVAL_HOURS`` when it names a real grid, else None.

    Unset is per-symbol. A value this parser refuses is unset too: a typo
    must not become the 8h fallback and look like a choice.
    """
    raw = os.getenv("FUNDING_INTERVAL_HOURS", "")
    if raw is None or str(raw).strip() == "":
        return None
    return parse_interval_hours(raw)


@dataclass(frozen=True)
class IntervalRead:
    """``measured`` is false only for the fallback. ``hours`` is always usable."""

    symbol: str
    hours: int
    measured: bool
    source: str  # "exchange" | "cache" | "fallback" | "override"


class IntervalBook:
    """Remembered intervals. ``path`` None is memory only."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path
        self._rows: dict[str, dict] = {}
        # A file that is there and will not read is not an empty book. Nothing
        # is written over it.
        self._unreadable = False
        self._load()

    def _load(self) -> None:
        if not self.path:
            return
        try:
            raw = load_json_store(self.path)
        except StoreUnreadable:
            self._unreadable = True
            return
        if not isinstance(raw, dict):
            self._unreadable = True
            return
        for key, row in raw.items():
            if not isinstance(row, dict):
                continue
            hours = parse_interval_hours(row.get("hours"))
            if hours is None:
                continue
            self._rows[canonical_symbol(str(key))] = {
                "hours": hours,
                "read_at": row.get("read_at"),
            }

    def _persist(self, key: str) -> None:
        """Write one symbol. An unreadable file is left untouched."""
        if not self.path or self._unreadable or key not in self._rows:
            return
        row = dict(self._rows[key])

        def change(data):
            data[key] = row
            return True

        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            update_json_store(self.path, change)
        except StoreUnreadable:
            self._unreadable = True
        except OSError:
            return

    def remember(self, symbol: str, payload) -> Optional[IntervalRead]:
        """Store an exchange reading. An unreadable payload stores nothing."""
        if isinstance(payload, (int, float)) and not isinstance(payload, bool):
            hours = parse_interval_hours(payload)
        else:
            hours = read_interval_hours(payload)
        if hours is None:
            return None
        key = canonical_symbol(symbol)
        if not key:
            return None
        self._rows[key] = {"hours": hours, "read_at": time.time()}
        self._persist(key)
        return IntervalRead(key, hours, True, "exchange")

    def get(self, symbol: str) -> IntervalRead:
        key = canonical_symbol(symbol)
        forced = override_hours()
        if forced is not None:
            return IntervalRead(key, forced, True, "override")
        row = self._rows.get(key)
        if row is not None:
            hours = parse_interval_hours(row.get("hours"))
            if hours is not None:
                return IntervalRead(key, hours, True, "cache")
        return IntervalRead(key, FALLBACK_HOURS, False, "fallback")


def default_path() -> str:
    return os.path.join("data", "funding_intervals.json")


def install_book(book: Optional[IntervalBook] = None) -> IntervalBook:
    """Swap the process book. Tests pass their own; ``None`` is memory-only."""
    global _BOOK
    _BOOK = book if book is not None else IntervalBook(path=None)
    return _BOOK


def book() -> IntervalBook:
    """The process book. The first read builds the one on disk."""
    global _BOOK
    if _BOOK is None:
        return install_book(IntervalBook(path=default_path()))
    return _BOOK


def interval_seconds(symbol: str) -> float:
    """Seconds in this symbol's grid. Never raises; unreadable is the fallback."""
    try:
        return float(book().get(symbol).hours) * 3600.0
    except Exception:
        return float(SETTLEMENT_INTERVAL_SEC)


def remember_funding_payload(symbol: str, payload) -> None:
    """Best-effort. A bad payload or a disk fault leaves the book as it was."""
    try:
        book().remember(symbol, payload)
    except Exception:
        return
