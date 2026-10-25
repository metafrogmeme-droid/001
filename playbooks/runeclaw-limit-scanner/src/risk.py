"""Limit price, stop, take-profit ladder, and dollar-risk sizing for RUNECLAW.

v0.1.0 is side-aware. For longs the limit rests below VWAP and the stop sits
below the 24h low; for shorts the limit rests above VWAP and the stop sits above
the 24h high. A limit that would sit at or through the last trade is not
moved onto the market: that is a taker, and this function refuses it.

Sizing is solved backward from a loss that is strictly under the circuit
pause and that, times the concurrent cap, does not exceed the circuit stop.
The margin budget can only shrink that further.
"""
import math
from dataclasses import dataclass
from typing import Optional

from .features import SymbolFeatures

_BTC_ETH = {"BTCUSDT", "ETHUSDT"}
_SOL_BNB = {"SOLUSDT", "BNBUSDT"}


@dataclass
class TradePlan:
    symbol: str
    side: str
    entry: float
    atr: float
    sl_price: float
    sl_pct: float
    tp1: float
    tp2: float
    trail_atr: float
    breakeven_price: float
    notional_usdt: float
    margin_usdt: float
    leverage: int
    size_factor: float
    sizing_ok: bool
    risk_usdt: float = 0.0
    note: str = ""


def _sl_min_fraction(symbol: str, cfg: dict) -> float:
    if symbol in _BTC_ETH:
        return float(cfg.get("sl_min_btc_eth_pct", "1.5")) / 100.0
    if symbol in _SOL_BNB:
        return float(cfg.get("sl_min_sol_bnb_pct", "1.2")) / 100.0
    return float(cfg.get("sl_min_alt_pct", "2.5")) / 100.0


def _positive(raw, default: str) -> Optional[float]:
    try:
        value = float(default if raw is None else raw)
    except (TypeError, ValueError):
        return None
    if value != value or value <= 0 or value == float("inf"):
        return None
    return value


def book_risk_cap(cfg: dict) -> Optional[float]:
    """The most the open book may lose at its stops, in USDT.

    The circuit stop, and twice the configured per-trade max loss, whichever
    is smaller. Unreadable breaker numbers are not a large cap.
    """
    hard = _positive(cfg.get("circuit_stop_usdt"), "40")
    asked = _positive(cfg.get("max_loss_usdt"), "15")
    if hard is None or asked is None:
        return None
    return min(hard, 2.0 * asked)


def loss_cap(cfg: dict) -> Optional[float]:
    """The most one trade may lose at its stop, in USDT.

    Strictly under the pause, and no more than the book cap divided by the
    concurrent cap. Five stops at that number cannot exceed the circuit stop.
    """
    pause = _positive(cfg.get("circuit_pause_usdt"), "30")
    book = book_risk_cap(cfg)
    try:
        slots = int(cfg.get("max_concurrent", 3))
    except (TypeError, ValueError):
        return None
    if pause is None or book is None or slots < 1:
        return None
    under_pause = math.nextafter(pause, 0.0)
    return min(under_pause, book / slots)


def build_plan(feats: SymbolFeatures, cfg: dict, size_factor: float, side: str = "long",
               open_risk: float = 0.0) -> Optional[TradePlan]:
    if not feats.ok or feats.high is None or feats.low is None or feats.vwap is None:
        return None

    high, low, vwap = feats.high, feats.low, feats.vwap
    atr = max((high - low) / 2.5, 0.0)
    atr_mult = float(cfg.get("atr_limit_mult", "0.5"))
    tp1_pct = float(cfg.get("tp1_pct", "3.5")) / 100.0
    tp2_pct = float(cfg.get("tp2_pct", "7.0")) / 100.0
    trail_atr = float(cfg.get("trail_atr_mult", "1.0")) * atr
    be_pct = float(cfg.get("breakeven_pct", "2.0")) / 100.0
    sl_min = _sl_min_fraction(feats.symbol, cfg)

    last = feats.last
    if side == "short":
        entry = vwap + atr_mult * atr
        # Do not slide the limit down to the last trade. At the market it takes.
        if last is not None and entry <= last:
            return None
        if entry <= 0:
            return None
        raw_sl_pct = (high - entry) / entry if high > entry else 0.0
        sl_pct = max(raw_sl_pct, sl_min)
        sl_price = entry * (1.0 + sl_pct)
        tp1 = entry * (1.0 - tp1_pct)
        tp2 = entry * (1.0 - tp2_pct)
        breakeven_price = entry * (1.0 - be_pct)
    else:
        entry = vwap - atr_mult * atr
        if last is not None and entry >= last:
            return None
        if entry <= 0:
            return None
        raw_sl_pct = (entry - low) / entry if entry > low else 0.0
        sl_pct = max(raw_sl_pct, sl_min)
        sl_price = entry * (1.0 - sl_pct)
        tp1 = entry * (1.0 + tp1_pct)
        tp2 = entry * (1.0 + tp2_pct)
        breakeven_price = entry * (1.0 + be_pct)

    if sl_pct <= 0 or tp1 <= 0:
        return None

    asked = _positive(cfg.get("max_loss_usdt"), "15")
    cap = loss_cap(cfg)
    leverage = max(int(cfg.get("leverage", 10)), 1)
    if (asked is None or cap is None or isinstance(open_risk, bool)
            or not isinstance(open_risk, (int, float)) or open_risk < 0):
        return None
    book = book_risk_cap(cfg)
    pause = _positive(cfg.get("circuit_pause_usdt"), "30")
    if book is None or pause is None:
        return None
    room = book - float(open_risk)
    allowed = min(asked, cap, room)
    if not (allowed > 0 and allowed < pause):
        return None
    factor = min(max(float(size_factor), 0.0), 1.0)
    notional = (allowed / sl_pct) * factor
    margin = notional / leverage

    budget = _positive(cfg.get("margin_budget"), "100")
    note = ""
    if budget is not None and margin > budget:
        margin = budget
        notional = margin * leverage
        note = "capped_by_margin_budget"
    risk = notional * sl_pct
    try:
        slots = int(cfg.get("max_concurrent", 3))
    except (TypeError, ValueError):
        return None
    if not (risk < pause and risk + float(open_risk) <= book and slots >= 1):
        return None

    return TradePlan(
        symbol=feats.symbol,
        side=side,
        entry=entry,
        atr=atr,
        sl_price=sl_price,
        sl_pct=sl_pct,
        tp1=tp1,
        tp2=tp2,
        trail_atr=trail_atr,
        breakeven_price=breakeven_price,
        notional_usdt=notional,
        margin_usdt=margin,
        leverage=leverage,
        size_factor=size_factor,
        sizing_ok=(notional > 0 and margin > 0 and risk > 0),
        risk_usdt=risk,
        note=note,
    )


def with_entry(plan: TradePlan, entry: float) -> Optional[TradePlan]:
    """The same plan at a maker price the chase moved, stops kept as fractions.

    A non-positive price is not an entry. The dollar risk does not change:
    the stop fraction and the notional are the ones already capped.
    """
    if not entry > 0 or not plan.entry > 0:
        return None
    if plan.side == "short":
        tp1_pct = (plan.entry - plan.tp1) / plan.entry
        tp2_pct = (plan.entry - plan.tp2) / plan.entry
        be_pct = (plan.entry - plan.breakeven_price) / plan.entry
        sl_price = entry * (1.0 + plan.sl_pct)
        tp1 = entry * (1.0 - tp1_pct)
        tp2 = entry * (1.0 - tp2_pct)
        breakeven_price = entry * (1.0 - be_pct)
    else:
        tp1_pct = (plan.tp1 - plan.entry) / plan.entry
        tp2_pct = (plan.tp2 - plan.entry) / plan.entry
        be_pct = (plan.breakeven_price - plan.entry) / plan.entry
        sl_price = entry * (1.0 - plan.sl_pct)
        tp1 = entry * (1.0 + tp1_pct)
        tp2 = entry * (1.0 + tp2_pct)
        breakeven_price = entry * (1.0 + be_pct)
    if not (sl_price > 0 and tp1 > 0 and tp2 > 0 and breakeven_price > 0):
        return None
    return TradePlan(
        symbol=plan.symbol, side=plan.side, entry=entry, atr=plan.atr,
        sl_price=sl_price, sl_pct=plan.sl_pct, tp1=tp1, tp2=tp2,
        trail_atr=plan.trail_atr, breakeven_price=breakeven_price,
        notional_usdt=plan.notional_usdt, margin_usdt=plan.margin_usdt,
        leverage=plan.leverage, size_factor=plan.size_factor,
        sizing_ok=plan.sizing_ok, risk_usdt=plan.risk_usdt, note=plan.note,
    )
