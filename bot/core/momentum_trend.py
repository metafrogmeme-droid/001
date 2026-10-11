"""1h Multi-Symbol Momentum Trend, on closed bars.

Bitget GetAgent's "1h Multi-Symbol Momentum Trend Perpetuals" as its page
states it: on closed 1h candles, a fast and a slow simple average agree on a
side and the close has moved that way over the momentum window. One position
a symbol; an opposite confirmed signal closes it, beside a fixed percent stop
and target. Its page showed the slow period (21), the momentum period (3), the
stop (1.2%), the target (2.4%) and the leverage (20x), and hid the fast
period. This reading takes 9, the conventional partner of 21, fixed before any
result of it was measured; the card says it is a reading.

The rule is one reading, ``momentum_signal``. Its numbers live under the
preset's own ``momentum_trend`` key, not the moving-average book's
``fast_period``/``slow_period``: those keys already mean "the MA book" to the
catalogue, the confirm gate and the scorecard generator. None is unread,
never a side.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

from bot.core.ma_trend import LONG, SHORT, ma_side
from bot.core.ta_utils import sma_last

#: The preset key that holds this rule's numbers.
RULE_KEY = "momentum_trend"


@dataclass(frozen=True)
class TrendParams:
    fast_period: int = 9
    slow_period: int = 21
    momentum_period: int = 3
    stop_loss_pct: float = 1.2
    take_profit_pct: float = 2.4

    @property
    def bars_needed(self) -> int:
        """Closed bars one reading needs: the slow average, and the close
        ``momentum_period`` bars before the last."""
        return max(self.slow_period, self.momentum_period + 1)


_INT_KEYS = ("fast_period", "slow_period", "momentum_period")
_PCT_KEYS = ("stop_loss_pct", "take_profit_pct")


def preset_is_momentum_trend(cfg: Any) -> bool:
    """True when this preset carries the momentum-trend rule."""
    return isinstance(cfg, dict) and isinstance(cfg.get(RULE_KEY), dict)


def params_from_preset(cfg: Any) -> Optional[TrendParams]:
    """The rule's numbers, or None when any of them does not read.

    A period is a positive int, the slow average is longer than the fast,
    and the stop and target are positive percents. A key the rule does not
    read is refused rather than carried.
    """
    if not preset_is_momentum_trend(cfg):
        return None
    raw = cfg[RULE_KEY]
    if set(raw) - set(_INT_KEYS) - set(_PCT_KEYS):
        return None
    kw: dict[str, Any] = {}
    for key in _INT_KEYS:
        if key in raw:
            val = raw[key]
            if isinstance(val, bool) or not isinstance(val, int) or val < 1:
                return None
            kw[key] = val
    for key in _PCT_KEYS:
        if key in raw:
            val = raw[key]
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                return None
            val = float(val)
            if not (0 < val < float("inf")):
                return None
            kw[key] = val
    params = TrendParams(**kw)
    if params.slow_period <= params.fast_period:
        return None
    return params


@dataclass(frozen=True)
class Reading:
    """One symbol at one closed bar. None is unread."""

    symbol: str
    fast: Optional[float]
    slow: Optional[float]
    momentum_pct: Optional[float]
    trend: Optional[str]
    signal: Optional[str]
    reason: str


def _close(raw: Any) -> Optional[float]:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value != value or value in (float("inf"), float("-inf")) or value <= 0:
        return None
    return value


def momentum_signal(symbol: str, closes: Sequence[Any], params: TrendParams) -> Reading:
    """LONG when the fast average is above the slow and the close is above
    the close ``momentum_period`` bars before it; SHORT for both below; no
    signal when they disagree, when the averages are equal or when the
    momentum is flat. A window too short or a close that does not read is
    unread, and says which."""
    seq = list(closes or [])
    if len(seq) < params.bars_needed:
        return Reading(symbol, None, None, None, None, None,
                       f"unread: {len(seq)} closed bars, {params.bars_needed} needed")
    fast = sma_last(seq, params.fast_period)
    slow = sma_last(seq, params.slow_period)
    last = _close(seq[-1])
    prior = _close(seq[-1 - params.momentum_period])
    if fast is None or slow is None or last is None or prior is None:
        return Reading(symbol, fast, slow, None, None, None,
                       "unread: a close in the window does not read")
    momentum = (last / prior - 1.0) * 100.0
    trend = ma_side(fast, slow)
    if trend is None:
        return Reading(symbol, fast, slow, momentum, None, None,
                       "the averages are equal: no side")
    agrees = momentum > 0 if trend == LONG else momentum < 0
    if not agrees:
        return Reading(symbol, fast, slow, momentum, trend, None,
                       f"the averages say {trend.lower()}, the momentum does not")
    return Reading(symbol, fast, slow, momentum, trend, trend,
                   f"the averages and the momentum agree: {trend.lower()}")


def how_line(cfg: dict) -> str:
    """What the rule does, read off the preset's own numbers."""
    p = params_from_preset(cfg)
    if p is None:
        return "Its numbers could not be read, so no scan runs it."
    syms = cfg.get("symbols")
    n = len(syms) if isinstance(syms, (list, tuple)) else 0
    lev = cfg.get("leverage")
    lev_text = (f" Its {lev:g}× leverage is recorded and not applied."
                if isinstance(lev, (int, float)) and not isinstance(lev, bool) else "")
    return (
        f"The rule covers {n} USDT perpetuals on closed 1h bars: long when the "
        f"{p.fast_period}-bar simple average is above the {p.slow_period}-bar and the "
        f"close is above the close {p.momentum_period} bars before it; short when both "
        f"are below; no signal when they disagree. One position a symbol, a "
        f"{p.stop_loss_pct:g}% stop and a {p.take_profit_pct:g}% target from the "
        f"entry, and an opposite signal closes it. Its source page hid the fast "
        f"period; {p.fast_period} is a reading (the usual partner of "
        f"{p.slow_period}), fixed before any result of it was measured.{lev_text}"
    )


__all__ = ["LONG", "SHORT", "RULE_KEY", "TrendParams", "Reading", "preset_is_momentum_trend",
           "params_from_preset", "momentum_signal", "how_line"]
