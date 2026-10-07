"""Daily volatility rotation across a named universe.

Momentum is the close-to-close ratio the confluence feature already calls
``chg``: close divided by the close ``momentum_period`` bars earlier, minus
one. On that reading the threshold 0.5 is a 50% rise. The trend average is
``sma_last``. ATR is ``atr_reading``. An unreadable momentum, average, or
ATR is not an entry, and a readable ATR is not a stop distance.

The bars are closed ``bar_timeframe`` groups. When that is coarser than the
source series, ``closed_ohlcv`` drops a trailing unfinished group. A 1h
series is not treated as a 1d series.

The house trail is an ATR stage table and partial closes are R-multiples, so
a percent trail, a percent target, and a percent hard stop are recorded and
not applied. This reader does not open a position and does not invent a fill.
The engine can hold more than one symbol, and it does not divide a
utilization budget across names, so the one name a book would hold is the
qualifier with the highest momentum. An equal highest momentum is not a
choice. Leverage and utilization stay on the preset and do not size a fill.
"""

from __future__ import annotations

from typing import Optional

from bot.core.ma_trend import closed_ohlcv
from bot.core.position_telemetry import atr_reading
from bot.core.strategy_gate import _base
from bot.core.ta_utils import sma_last

LONG = "LONG"

_SIGNAL_KEYS = ("momentum_period", "trend_period", "atr_period")
UNMODELED_EXITS = ("trailing_stop_pct", "take_profit_pct", "hard_stop_loss_pct")


def preset_is_vol_rotation(cfg: dict) -> bool:
    """True when this preset's direction is the daily rotation."""
    if not isinstance(cfg, dict):
        return False
    if cfg.get("bar_timeframe") != "1d":
        return False
    for key in _SIGNAL_KEYS:
        val = cfg.get(key)
        if isinstance(val, bool) or not isinstance(val, int) or val < 1:
            return False
    return True


def publishes_scorecard(cfg: dict) -> bool:
    """False when a majors 1h house run would not be this strategy's record.

    The percent exits are not applied, and the bar size is daily. Omitting
    the file keeps a missing track record missing.
    """
    return not preset_is_vol_rotation(cfg)


def _finite(raw) -> Optional[float]:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _number(raw) -> Optional[float]:
    """A finite threshold or size, or None. ``bool`` is not a number."""
    return _finite(raw)


def momentum_ratio(closes, period: int) -> Optional[float]:
    """Close / close ``period`` bars earlier, minus 1, or None.

    This is the confluence change ratio. 0.5 on this reading is a 50% rise.
    A short window, a non-numeric close, a non-finite close, or a prior close
    of 0 is None. None is not zero and not a pass.
    """
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        return None
    if closes is None:
        return None
    try:
        seq = list(closes)
    except TypeError:
        return None
    if len(seq) < period + 1:
        return None
    last = _finite(seq[-1])
    prior = _finite(seq[-1 - period])
    if last is None or prior is None or prior == 0:
        return None
    return last / prior - 1.0


def close_above_average(closes, period: int) -> Optional[bool]:
    """True when the last close is above ``sma_last``.

    None when the average cannot be read. Equal is False. False is not a
    short.
    """
    avg = sma_last(closes, period)
    if avg is None or closes is None:
        return None
    try:
        last = _finite(list(closes)[-1])
    except TypeError:
        return None
    if last is None:
        return None
    return last > avg


def _blank(daily_bars) -> dict:
    return {
        "momentum": None,
        "above_average": None,
        "atr": None,
        "side": None,
        "qualifies": False,
        "hard_stop": None,
        "take_profit": None,
        "trail": None,
        "daily_bars": daily_bars,
    }


def _period(raw) -> Optional[int]:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return None
    return int(raw)


def symbol_read(closes, highs, lows, cfg: dict) -> dict:
    """One symbol on already-closed bars of the preset's bar size."""
    mom_n = _period(cfg.get("momentum_period"))
    trend_n = _period(cfg.get("trend_period"))
    atr_n = _period(cfg.get("atr_period"))
    mom = momentum_ratio(closes, mom_n) if mom_n is not None else None
    above = close_above_average(closes, trend_n) if trend_n is not None else None
    atr = None
    if atr_n is not None:
        try:
            atr = atr_reading(list(highs), list(lows), list(closes), atr_n)
        except TypeError:
            atr = None
    threshold = _number(cfg.get("momentum_threshold"))
    qualifies = (
        mom is not None and above is True and atr is not None
        and threshold is not None and mom >= threshold
    )
    return {
        "momentum": mom,
        "above_average": above,
        "atr": atr,
        "side": LONG if qualifies else None,
        "qualifies": qualifies,
        # Percent exits are not prices. None is not a stop distance of 0.
        "hard_stop": None,
        "take_profit": None,
        "trail": None,
        "daily_bars": None if closes is None else len(list(closes)),
    }


def _columns(rows) -> Optional[tuple]:
    highs, lows, closes = [], [], []
    for row in rows:
        try:
            high, low, close = row[2], row[3], row[4]
        except (TypeError, IndexError):
            return None
        h, lo, c = _finite(high), _finite(low), _finite(close)
        if h is None or lo is None or c is None:
            return None
        highs.append(h)
        lows.append(lo)
        closes.append(c)
    return highs, lows, closes


def choose_name(reads: dict) -> Optional[str]:
    """The qualifier with the strictly highest momentum, or None.

    None when nobody qualifies, and None when the highest momentum is shared.
    No tie-break is defined, and this is not a weight.
    """
    ranked: list[tuple[float, str]] = []
    for sym, read in reads.items():
        if not isinstance(read, dict) or not read.get("qualifies"):
            continue
        mom = _number(read.get("momentum"))
        if mom is None:
            continue
        ranked.append((mom, str(sym)))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    if len(ranked) >= 2 and ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][1]


def rotate(bars_by_symbol: dict, cfg: dict) -> dict:
    """Read every universe name. Does not open, size, or exit."""
    source = str(cfg.get("bar_source_timeframe") or "")
    target = str(cfg.get("bar_timeframe") or "")
    universe = cfg.get("symbols") if isinstance(cfg, dict) else None
    allowed = set()
    if isinstance(universe, (list, tuple)):
        allowed = {_base(s) for s in universe}
        allowed.discard("")
    reads: dict = {}
    if isinstance(bars_by_symbol, dict):
        for sym, rows in bars_by_symbol.items():
            if allowed and _base(sym) not in allowed:
                continue
            daily = closed_ohlcv(rows, source, target)
            if daily is None:
                reads[sym] = _blank(None)
                continue
            if not daily:
                reads[sym] = _blank(0)
                continue
            cols = _columns(daily)
            if cols is None:
                reads[sym] = _blank(len(daily))
                continue
            highs, lows, closes = cols
            read = symbol_read(closes, highs, lows, cfg)
            read["daily_bars"] = len(daily)
            reads[sym] = read
    return {
        "reads": reads,
        "qualifiers": [sym for sym, read in reads.items() if read.get("qualifies")],
        "chosen": choose_name(reads),
        "selection": "highest_momentum",
        "opens": False,
        "exits_applied": False,
        "sizing_applied": False,
    }


def _pct(raw) -> str:
    value = _number(raw)
    if value is None:
        return "unreadable"
    return f"{value * 100:g}%"


def _plain(raw) -> str:
    value = _number(raw)
    if value is None:
        return "unreadable"
    return f"{value:g}"


def bars_per_target(source: str, target: str) -> Optional[int]:
    """How many source bars close one target bar, or None when that is unreadable."""
    from bot.utils.candles import timeframe_to_ms
    src = timeframe_to_ms(str(source or ""))
    tgt = timeframe_to_ms(str(target or ""))
    if src <= 0 or tgt <= src or tgt % src != 0:
        return None
    return tgt // src


def how_line(cfg: dict) -> str:
    """The public how-line. Every number is read off the preset."""
    syms = cfg.get("symbols")
    if isinstance(syms, (list, tuple)) and syms:
        universe = ", ".join(str(s) for s in syms)
    else:
        universe = "no market"
    mom_n = cfg.get("momentum_period")
    trend_n = cfg.get("trend_period")
    atr_n = cfg.get("atr_period")
    thr = _plain(cfg.get("momentum_threshold"))
    source = str(cfg.get("bar_source_timeframe") or "")
    target = str(cfg.get("bar_timeframe") or "")
    per = bars_per_target(source, target)
    if per is not None:
        bar_sentence = (
            f" {target} bars are closed bars resampled from {source} bars."
            f" A {target} bar is {per} closed {source} bars, and a trailing"
            f" unfinished {target} group is dropped. {source} bars stay"
            f" {source} bars."
        )
    else:
        bar_sentence = ""
    return (
        f"The rule covers only {universe}: long only when the close is above the"
        f" {trend_n}-bar average of closes and the momentum ratio over"
        f" {mom_n} closed {target} bars is at least {thr}. That ratio is the"
        f" close divided by the close {mom_n} {target} bars earlier, minus"
        f" one, so {thr} is a {_pct(cfg.get('momentum_threshold'))} rise."
        f" A name stays flat when the momentum, the average, or the"
        f" {atr_n}-bar ATR cannot be read. Among the names that qualify, the"
        f" one with the highest momentum is the single name a book would hold,"
        f" because the engine does not divide a utilization budget across"
        f" names. An equal highest momentum is not a choice. The"
        f" {_pct(cfg.get('trailing_stop_pct'))} trailing stop, the"
        f" {_pct(cfg.get('take_profit_pct'))} take-profit, and the"
        f" {_pct(cfg.get('hard_stop_loss_pct'))} hard stop are recorded and"
        f" not applied. Leverage {_plain(cfg.get('leverage'))}\u00d7 and"
        f" utilization {_plain(cfg.get('utilization'))} are recorded and do"
        f" not size a fill.{bar_sentence}"
    )


def omitted_reason(cfg: dict) -> str:
    """Why the public card has no track record. No dollar amount."""
    return (
        "No track record published. The"
        f" {_pct(cfg.get('trailing_stop_pct'))} trailing stop, the"
        f" {_pct(cfg.get('take_profit_pct'))} take-profit, and the"
        f" {_pct(cfg.get('hard_stop_loss_pct'))} hard stop are recorded and"
        " not applied."
    )


def omitted_scorecard(cfg: dict) -> Optional[dict]:
    """The catalogue slot for a preset whose track record stays missing."""
    if publishes_scorecard(cfg):
        return None
    return {"omitted": omitted_reason(cfg)}
