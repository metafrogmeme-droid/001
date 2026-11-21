"""Synthetic candles stamped on the test's clock.

A series stamped ``1_700_000_000_000 + i * 3_600_000`` (November 2023) or
``float(i)`` (1970) is, by definition, a FROZEN FEED when it is analysed live:
its newest bar closed years before the analysis. Since the analyzer refuses a
series whose newest closed bar is more than ``BAR_FRESHNESS_MAX_PERIODS`` old
(``bot/utils/candles.py::bar_freshness_verdict``), a fixture that means "a live
market" has to say WHEN that market printed. These helpers move a series onto
the clock without changing its spacing or prices, so the property a test was
written about is untouched; only the date changes.

A test about staleness itself stamps its own rows; it does not use these.
"""
from __future__ import annotations

import time
from typing import Optional

from bot.utils.candles import timeframe_to_ms


def _period(timeframe: str) -> int:
    p = timeframe_to_ms(timeframe)
    if p <= 0:
        raise ValueError(f"timeframe {timeframe!r} has no fixed period")
    return p


def _boundary(timeframe: str, now_ms: Optional[float]) -> int:
    """The most recent period boundary at or before ``now_ms`` (wall clock if None)."""
    period = _period(timeframe)
    now = time.time() * 1000.0 if now_ms is None else float(now_ms)
    return int(now // period) * period


def fresh_start_ms(n_bars: int, timeframe: str = "1h", now_ms: Optional[float] = None) -> int:
    """The open of bar 0 of an ``n_bars`` series whose newest bar closed at the
    latest period boundary: a series that is as fresh as a live fetch."""
    return _boundary(timeframe, now_ms) - n_bars * _period(timeframe)


def anchored(rows: list, timeframe: str = "1h", now_ms: Optional[float] = None) -> list:
    """``rows`` shifted, as a whole, so the newest bar closed at the latest
    period boundary. Spacing, prices and volumes are unchanged."""
    if not rows:
        return rows
    target_open = _boundary(timeframe, now_ms) - _period(timeframe)
    delta = target_open - int(rows[-1][0])
    return [[int(r[0]) + delta, *r[1:]] for r in rows]
