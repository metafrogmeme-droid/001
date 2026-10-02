"""Closed-bar moving-average trend.

One reading of the average: ``sma_last``. Fast above slow is long. Fast below
slow is short. An unreadable average, or the two equal, is not a side. A held
side stays until that relationship changes; an unreadable bar does not flip it
and does not invent the held side as a new signal.

The bars are the closes of ``target_tf``. When that is coarser than the source
series, they are ``resample_ohlcv`` groups, which drop a trailing unfinished
group. A 1h series is not treated as a 4h series.
"""

from __future__ import annotations

from typing import Optional

from bot.core.strategy_gate import _base
from bot.core.ta_utils import sma_last

LONG = "LONG"
SHORT = "SHORT"


def preset_is_ma_trend(cfg: dict) -> bool:
    """True when this preset's direction is a fast/slow average."""
    fast = cfg.get("fast_period") if isinstance(cfg, dict) else None
    slow = cfg.get("slow_period") if isinstance(cfg, dict) else None
    if isinstance(fast, bool) or not isinstance(fast, int):
        return False
    if isinstance(slow, bool) or not isinstance(slow, int):
        return False
    return slow > fast > 0


def symbol_allowed(symbol: str, universe: str) -> bool:
    """True when ``symbol`` is in a comma-separated universe.

    An empty universe allows nothing. Matching is the base ticker, so
    ETHUSDT and ETH/USDT:USDT are the same market.
    """
    text = str(universe or "").strip()
    if not text:
        return False
    allowed = {_base(part) for part in text.split(",") if str(part).strip()}
    allowed.discard("")
    if not allowed:
        return False
    return _base(symbol) in allowed


def ma_side(fast: Optional[float], slow: Optional[float]) -> Optional[str]:
    """LONG, SHORT, or None. Equal and unreadable are None."""
    if fast is None or slow is None:
        return None
    if isinstance(fast, bool) or isinstance(slow, bool):
        return None
    if not isinstance(fast, (int, float)) or not isinstance(slow, (int, float)):
        return None
    if fast != fast or slow != slow:
        return None
    if fast > slow:
        return LONG
    if fast < slow:
        return SHORT
    return None


def ma_trend_step(closes, fast_period: int, slow_period: int,
                  held: Optional[str]) -> dict:
    """One closed bar.

    ``action`` is ``enter`` (flat, and the relationship names a side),
    ``reverse`` (held the other side), ``hold`` (same side, or a held side
    whose new bar cannot be read), or ``stand_aside`` (flat, and no side).
    ``side`` is None whenever the relationship cannot be read, including a
    hold caused by an unreadable bar.
    """
    fast = sma_last(closes, fast_period)
    slow = sma_last(closes, slow_period)
    side = ma_side(fast, slow)
    current = held if held in (LONG, SHORT) else None
    if side is None:
        action = "hold" if current else "stand_aside"
    elif current is None:
        action = "enter"
    elif current == side:
        action = "hold"
    else:
        action = "reverse"
    return {"fast": fast, "slow": slow, "side": side, "action": action}


def closed_ohlcv(rows, source_tf: str, target_tf: str):
    """OHLCV rows the average is read on, or None when the timeframe cannot.

    An empty list means no closed target bar yet. That is not a fallback to
    the source bars.
    """
    if rows is None:
        return None
    try:
        seq = list(rows)
    except TypeError:
        return None
    source = str(source_tf or "").strip()
    target = str(target_tf or "").strip() or source
    if not source or not target:
        return None
    from bot.utils.candles import resample_ohlcv, timeframe_to_ms
    src_ms = timeframe_to_ms(source)
    tgt_ms = timeframe_to_ms(target)
    if src_ms <= 0 or tgt_ms <= 0:
        return None
    if tgt_ms == src_ms:
        return seq
    if tgt_ms < src_ms or tgt_ms % src_ms != 0:
        return None
    grouped = resample_ohlcv(seq, source, target)
    return list(grouped)


def last_closed_open_ms(rows) -> Optional[int]:
    """Open time of the last row, or None when there is no row to stamp."""
    if not rows:
        return None
    try:
        return int(rows[-1][0])
    except (TypeError, ValueError, IndexError):
        return None


def ma_margin(balance, weight, utilization, max_gross, leverage) -> Optional[float]:
    """Margin for one position, or None when a sizing input cannot be read.

    ``leverage`` is the fill leverage. ``max_gross`` caps notional at
    ``max_gross × balance``. They are different knobs: a leverage of 10 with
    a gross cap of 1 sizes the margin at a tenth of the balance, it does not
    rewrite either number into the other. Weight and utilization scale the
    margin before that cap. An unreadable input is not a full-balance order.
    """
    if isinstance(balance, bool) or not isinstance(balance, (int, float)):
        return None
    if balance != balance or balance in (float("inf"), float("-inf")) or balance <= 0:
        return None
    for raw in (weight, utilization, max_gross):
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        if raw != raw or raw in (float("inf"), float("-inf")) or raw <= 0:
            return None
    if isinstance(leverage, bool) or not isinstance(leverage, (int, float)):
        return None
    if leverage != leverage or leverage < 1:
        return None
    margin = float(balance) * float(weight) * float(utilization)
    notional = margin * float(leverage)
    cap = float(balance) * float(max_gross)
    if notional > cap:
        margin = cap / float(leverage)
    if margin > float(balance):
        margin = float(balance)
    if margin <= 0 or margin != margin:
        return None
    return margin
