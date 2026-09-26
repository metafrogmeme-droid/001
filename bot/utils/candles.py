"""Shared OHLCV candle hygiene helpers.

The repaint policy (DROP_UNCLOSED_CANDLE_ENABLED, default ON) must apply to
EVERY consumer of fetch_ohlcv — the engine's analysis path and the live
executor's limit-entry / trend checks alike — so the logic lives here rather
than as an engine method.
"""
from __future__ import annotations

import time
from typing import Optional


# Canonical set of scan/analysis timeframes, ascending by duration. The single
# source of truth for "which timeframes does the bot understand" — command arg
# validation and the multi-timeframe on-demand sweep both read this instead of
# inlining their own lists.
SUPPORTED_TIMEFRAMES: list[str] = ["5m", "15m", "1h", "4h", "1d"]


def is_supported_timeframe(timeframe: str) -> bool:
    """True if ``timeframe`` is one the bot scans/analyzes."""
    return timeframe in SUPPORTED_TIMEFRAMES


def resolve_timeframes(timeframe: str) -> list[str]:
    """Expand a timeframe argument to the concrete list to scan.

    ``"all"`` -> every SUPPORTED_TIMEFRAMES; a single supported tf -> just that
    one; anything else -> empty list (caller reports an invalid argument).
    """
    if timeframe == "all":
        return list(SUPPORTED_TIMEFRAMES)
    return [timeframe] if timeframe in SUPPORTED_TIMEFRAMES else []


def timeframe_to_ms(timeframe: str) -> int:
    """Parse a ccxt timeframe ('5m','1h','4h','1d','1w') to milliseconds; 0 if
    unparseable."""
    try:
        unit = timeframe[-1].lower()
        n = int(timeframe[:-1])
        mult = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}.get(unit)
        return n * mult if mult else 0
    except Exception:
        return 0


def resample_ohlcv(candles, source_tf: str, target_tf: str):
    """Aggregate finer-timeframe OHLCV rows into CLOSED target-timeframe
    candles (ccxt row format [ts, o, h, l, c, v], ascending).

    Only complete target periods are returned — a trailing group whose period
    has not fully elapsed by the last source bar's close is dropped. This
    makes backtest replay see exactly the higher-TF history live would have
    had at that bar close: no lookahead into the unfinished 4h/1d candle.

    Returns [] when the timeframes are unparseable, equal, or the target is
    not an integer multiple of the source.
    """
    src_ms = timeframe_to_ms(source_tf)
    tgt_ms = timeframe_to_ms(target_tf)
    if src_ms <= 0 or tgt_ms <= src_ms or tgt_ms % src_ms != 0 or not candles:
        return []
    groups: dict[int, list] = {}
    for row in candles:
        key = int(row[0] // tgt_ms)
        g = groups.get(key)
        if g is None:
            groups[key] = [key * tgt_ms, row[1], row[2], row[3], row[4], row[5]]
        else:
            g[2] = max(g[2], row[2])
            g[3] = min(g[3], row[3])
            g[4] = row[4]
            g[5] += row[5]
    last_close_ms = candles[-1][0] + src_ms
    return [groups[k] for k in sorted(groups) if (k + 1) * tgt_ms <= last_close_ms]


def drop_forming_candle(ohlcv, timeframe: str):
    """Drop the in-progress (still-forming) last candle so indicators/patterns
    compute on CLOSED bars only — eliminating repaint. Gated by
    DROP_UNCLOSED_CANDLE_ENABLED (default ON; when disabled returns ohlcv
    unchanged and every closes[-1] consumer repaints intrabar). The last
    candle is dropped only when its period has not yet elapsed (its open time
    + timeframe is still in the future), so a feed that already excludes the
    forming bar is left intact. Fail-open: any error returns ohlcv as-is.

    CALL IT AT THE FETCH, because it answers about the moment it is CALLED.
    The rows carry no age and the decision is a wall-clock comparison, so on
    rows that have been stored the verdict inverts: a bar that was forming when
    it was read and whose period has since elapsed is KEPT — the partial values
    captured at read time, presented as the newest closed bar, its close being
    the price at read time and its volume a part-period's. The same rows five
    minutes apart across the bar boundary answer differently. `engine.py`'s
    shared `_cached_ohlcv` applies this before it stores for exactly that
    reason, and `_mtf_ttl`'s whole derivation reasons from the result.

    `resample_ohlcv` above needs no such warning and the difference is worth
    knowing: it derives its own boundary from the DATA (`candles[-1][0] +
    src_ms`), so its answer is the same however old the rows are. That is not
    available here — a forming bar's row is byte-identical to a closed one's,
    which is why this reading needs a clock at all.
    """
    from bot.config import CONFIG
    if not getattr(CONFIG.analyzer, "drop_unclosed_candle_enabled", False):
        return ohlcv
    try:
        if not ohlcv or len(ohlcv) < 3:
            return ohlcv
        tf_ms = timeframe_to_ms(timeframe)
        if tf_ms <= 0:
            return ohlcv
        last_open = float(ohlcv[-1][0])
        now_ms = time.time() * 1000.0
        if now_ms < last_open + tf_ms:   # last candle's period not yet closed
            return ohlcv[:-1]
        return ohlcv
    except Exception:
        return ohlcv


def ohlc_on_record(rows) -> bool:
    """Whether every row's open, high, low and close is a finite, positive price.

    A NULL CLOSE BECOMES NaN SILENTLY: ccxt's ``safe_number`` answers None for a
    null, missing or empty field, and ``np.array(..., dtype=float)`` turns that
    None into NaN with no error. Every comparison against NaN is False, so a
    heuristic reads the missing bar as whichever side its ``else`` names (the
    scanner's ``price > sma50`` is False, so a clean uptrend read SHORT) and an
    EMA stays NaN from that bar on (so a daily downtrend read "neutral").
    ``Analyzer.analyze`` refuses such a series outright; this is the same
    check, for the readers that compute off the rows themselves. A series that
    fails it is MISSING, not neutral: the caller skips it.
    """
    try:
        for row in rows:
            if len(row) < 5:
                return False
            for v in row[1:5]:
                if isinstance(v, bool):
                    return False
                f = float(v)
                if f != f or f in (float("inf"), float("-inf")) or f <= 0:
                    return False
    except (TypeError, ValueError, IndexError):
        return False
    return True


def volume_on_record(v) -> Optional[float]:
    """A volume the row states -- finite and not negative -- or None.

    ``0.0`` is kept: a bar that traded nothing is a reading. A null is not.
    """
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")) or f < 0:
        return None
    return f
