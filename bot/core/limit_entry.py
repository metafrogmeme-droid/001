"""
RUNECLAW Limit Entry Calculator — GetClaw-style confluence-based entries.

Replaces simple ATR offset with multi-layer price level analysis:
  Layer 1: VWAP, EMA9/EMA20, session high/low, round numbers, ATR bands
  Layer 2: Confluence scoring → entry quality tier (A/B/C/D)
  Layer 3: Natural SL placement at structural levels

Used by live_executor.execute() when recalculating limit prices.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EntryLevel:
    """A potential entry price level with its source and weight."""
    price: float
    source: str        # "vwap", "ema9", "ema20", "session_low", "round_number", "atr_band"
    weight: float      # confluence weight (higher = stronger)
    description: str = ""


@dataclass
class EntryResult:
    """Result of limit entry calculation."""
    limit_price: float
    tier: str              # "A", "B", "C", "D"
    confluence_count: int  # how many levels stacked
    size_multiplier: float # 1.0 for A, 0.7 for C, 0.0 for D (skip)
    natural_sl: Optional[float] = None
    levels_used: list = field(default_factory=list)
    explanation: str = ""


# ── Weight constants ──────────────────────────────────────────────
_WEIGHTS = {
    "vwap":        1.0,   # Strongest — institutional anchor
    "ema20":       0.9,   # Trend structure
    "ema9":        0.8,   # Short-term momentum
    "session_low": 0.7,   # Local support/resistance
    "session_high": 0.7,
    "round_number": 0.5,  # Psychological level
    "atr_band":    0.4,   # Statistical boundary
    "prev_close":  0.3,   # Overnight reference
}


def _round_number_near(price: float, tolerance_pct: float = 0.3) -> Optional[float]:
    """Find the nearest round number within tolerance_pct of price."""
    if price <= 0:
        return None

    # Determine the round number granularity based on price magnitude
    step: float
    if price > 100000:
        # QC-2: with a $100 step, every price above $100k failed the
        # step/price >= 0.1% meaningfulness guard below — BTC at six
        # figures had NO round-number level at all. $1000 is the step
        # traders actually watch up here ($105,000, $106,000).
        step = 1000
    elif price > 10000:
        step = 100      # $10,500, $10,600
    elif price > 1000:
        step = 50       # $4,150, $4,200
    elif price > 100:
        step = 5        # $245, $250
    elif price > 10:
        step = 1        # $77, $78
    elif price > 1:
        step = 0.25     # $1.25, $1.50
    else:
        step = 0.01     # $0.15, $0.16

    nearest = round(price / step) * step
    dist_pct = abs(price - nearest) / price * 100

    # Guard: on high-priced assets, ensure the round number is actually meaningful
    # (step must be at least 0.1% of price for the round number to matter)
    if step / price < 0.001:
        return None  # step too small relative to price — round numbers are noise

    if dist_pct <= tolerance_pct:
        return nearest
    return None


def _calculate_vwap(ohlcv: list) -> Optional[float]:
    """Calculate VWAP from OHLCV candle data.
    
    VWAP = sum(typical_price * volume) / sum(volume)
    Each candle: [timestamp, open, high, low, close, volume]
    """
    if not ohlcv or len(ohlcv) < 5:
        return None

    total_pv = 0.0
    total_vol = 0.0
    for candle in ohlcv:
        if len(candle) < 6:
            continue
        high = float(candle[2] or 0)
        low = float(candle[3] or 0)
        close = float(candle[4] or 0)
        volume = float(candle[5] or 0)
        if volume <= 0:
            continue
        typical = (high + low + close) / 3.0
        total_pv += typical * volume
        total_vol += volume

    if total_vol <= 0:
        return None
    return total_pv / total_vol


def _calculate_ema(closes: list[float], period: int) -> Optional[float]:
    """Calculate EMA from a list of close prices (oldest first)."""
    if len(closes) < period:
        return None

    multiplier = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period  # SMA seed

    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema

    return ema


def calculate_entry(
    current_price: float,
    direction: str,           # "LONG" or "SHORT"
    atr_value: float,
    ohlcv: Optional[list] = None,  # recent 1H candles for VWAP/EMA
    session_high: Optional[float] = None,
    session_low: Optional[float] = None,
    prev_close: Optional[float] = None,
    book_bid_wall: Optional[float] = None,
    book_ask_wall: Optional[float] = None,
) -> EntryResult:
    """Calculate optimal limit entry price using confluence analysis.

    Returns EntryResult with limit_price, quality tier, and size multiplier.
    Falls back to 0.5 ATR offset if no confluence data available.
    """
    is_long = direction.upper() == "LONG"
    levels: list[EntryLevel] = []

    # ── Compute technical levels from OHLCV data ──────────────
    vwap = None
    ema9 = None
    ema20 = None

    if ohlcv and len(ohlcv) >= 20:
        vwap = _calculate_vwap(ohlcv)
        closes = [float(c[4]) for c in ohlcv if len(c) >= 5 and c[4]]
        if len(closes) >= 20:
            ema9 = _calculate_ema(closes, 9)
            ema20 = _calculate_ema(closes, 20)

        # Session high/low from candle data if not provided
        if session_high is None:
            session_high = max(float(c[2]) for c in ohlcv[-24:] if len(c) >= 3 and c[2])
        if session_low is None:
            session_low = min(float(c[3]) for c in ohlcv[-24:] if len(c) >= 4 and c[3])

    # ── Build level list ──────────────────────────────────────
    # For LONG: we want levels BELOW current price (pullback entries)
    # For SHORT: we want levels ABOVE current price (rally entries)

    if vwap and vwap > 0:
        if is_long and vwap < current_price:
            levels.append(EntryLevel(vwap, "vwap", _WEIGHTS["vwap"],
                                     f"VWAP @ ${vwap:,.4f}"))
        elif not is_long and vwap > current_price:
            levels.append(EntryLevel(vwap, "vwap", _WEIGHTS["vwap"],
                                     f"VWAP @ ${vwap:,.4f}"))

    if ema9 and ema9 > 0:
        if is_long and ema9 < current_price:
            levels.append(EntryLevel(ema9, "ema9", _WEIGHTS["ema9"],
                                     f"EMA9 @ ${ema9:,.4f}"))
        elif not is_long and ema9 > current_price:
            levels.append(EntryLevel(ema9, "ema9", _WEIGHTS["ema9"],
                                     f"EMA9 @ ${ema9:,.4f}"))

    if ema20 and ema20 > 0:
        if is_long and ema20 < current_price:
            levels.append(EntryLevel(ema20, "ema20", _WEIGHTS["ema20"],
                                     f"EMA20 @ ${ema20:,.4f}"))
        elif not is_long and ema20 > current_price:
            levels.append(EntryLevel(ema20, "ema20", _WEIGHTS["ema20"],
                                     f"EMA20 @ ${ema20:,.4f}"))

    if session_low and is_long and session_low < current_price:
        levels.append(EntryLevel(session_low, "session_low", _WEIGHTS["session_low"],
                                 f"Session low @ ${session_low:,.4f}"))

    if session_high and not is_long and session_high > current_price:
        levels.append(EntryLevel(session_high, "session_high", _WEIGHTS["session_high"],
                                 f"Session high @ ${session_high:,.4f}"))

    if prev_close and prev_close > 0:
        if is_long and prev_close < current_price:
            levels.append(EntryLevel(prev_close, "prev_close", _WEIGHTS["prev_close"],
                                     f"Prev close @ ${prev_close:,.4f}"))
        elif not is_long and prev_close > current_price:
            levels.append(EntryLevel(prev_close, "prev_close", _WEIGHTS["prev_close"],
                                     f"Prev close @ ${prev_close:,.4f}"))

    # Round number check
    round_num = _round_number_near(current_price)
    if round_num:
        if is_long and round_num < current_price:
            levels.append(EntryLevel(round_num, "round_number", _WEIGHTS["round_number"],
                                     f"Round # @ ${round_num:,.4f}"))
        elif not is_long and round_num > current_price:
            levels.append(EntryLevel(round_num, "round_number", _WEIGHTS["round_number"],
                                     f"Round # @ ${round_num:,.4f}"))

    # ATR band entry
    if atr_value > 0:
        if is_long:
            atr_entry = current_price - (0.75 * atr_value)
        else:
            atr_entry = current_price + (0.75 * atr_value)
        levels.append(EntryLevel(atr_entry, "atr_band", _WEIGHTS["atr_band"],
                                 f"ATR band @ ${atr_entry:,.4f}"))

    # ── No levels found → fallback to simple ATR offset ──────
    if not levels:
        # L-03: guard against atr_value=0 producing a market-price limit
        if atr_value <= 0:
            return EntryResult(
                limit_price=round(current_price, 8),
                tier="D",
                confluence_count=0,
                size_multiplier=0.0,
                explanation="No confluence data and ATR=0 — cannot calculate meaningful limit offset",
            )
        fallback = current_price - (0.5 * atr_value) if is_long else current_price + (0.5 * atr_value)
        return EntryResult(
            limit_price=round(fallback, 8),
            tier="C",
            confluence_count=0,
            size_multiplier=0.7,
            explanation="No confluence data — using 0.5 ATR offset fallback",
        )

    # ── Find confluence zone ─────────────────────────────────
    # Group levels that are within 0.5% of each other
    tolerance = current_price * 0.005  # 0.5% clustering tolerance

    # Sort levels by price (ascending for longs, descending for shorts)
    levels.sort(key=lambda l: l.price, reverse=is_long)

    best_cluster: list[EntryLevel] = []
    best_weight = 0.0
    best_center = 0.0

    for i, anchor in enumerate(levels):
        cluster = [anchor]
        for j, other in enumerate(levels):
            if i == j:
                continue
            if abs(anchor.price - other.price) <= tolerance:
                cluster.append(other)

        total_weight = sum(l.weight for l in cluster)
        if total_weight > best_weight:
            best_weight = total_weight
            best_cluster = cluster
            # Weighted average of cluster prices
            best_center = sum(l.price * l.weight for l in cluster) / total_weight

    # ── Determine entry quality tier ──────────────────────────
    n_sources = len(set(l.source for l in best_cluster))

    if n_sources >= 3 and best_weight >= 2.5:
        tier = "A"
        size_mult = 1.0
    elif n_sources >= 2 and best_weight >= 1.5:
        tier = "B"
        size_mult = 1.0
    elif n_sources >= 1:
        tier = "C"
        size_mult = 0.7  # reduce size 30%
    else:
        tier = "D"
        size_mult = 0.0  # skip

    # ── Distance validation ──────────────────────────────────
    # GetClaw: too close (<0.3 ATR) = no edge, too far (>2 ATR) = knife catch
    if atr_value > 0:
        dist = abs(current_price - best_center)
        if dist < 0.3 * atr_value:
            # Too close — might as well market order; but still use it
            logger.info("Limit entry very close to market (%.1f%% ATR) — low edge",
                        dist / atr_value * 100)
        elif dist > 2.0 * atr_value:
            # Too far — downgrade tier
            if tier in ("A", "B"):
                tier = "C"
                size_mult = 0.7
                logger.info("Limit entry >2 ATR from market — downgraded to Tier C")

    # ── Natural SL calculation ────────────────────────────────
    natural_sl = None
    if is_long:
        # SL below the lowest structural level - buffer
        sl_candidates = []
        if session_low:
            sl_candidates.append(session_low)
        if ema20 and ema20 < best_center:
            sl_candidates.append(ema20)
        if sl_candidates:
            natural_sl = min(sl_candidates) * 0.998  # 0.2% buffer below

    else:
        # SHORT: SL above the highest structural level + buffer
        sl_candidates = []
        if session_high:
            sl_candidates.append(session_high)
        if ema20 and ema20 > best_center:
            sl_candidates.append(ema20)
        if sl_candidates:
            natural_sl = max(sl_candidates) * 1.002  # 0.2% buffer above

    levels_desc = [l.description for l in best_cluster]
    explanation = (
        f"Tier {tier}: {n_sources} confluent levels ({', '.join(l.source for l in best_cluster)}) "
        f"cluster @ ${best_center:,.4f} | weight={best_weight:.1f}"
    )

    return EntryResult(
        limit_price=round(best_center, 8),
        tier=tier,
        confluence_count=n_sources,
        size_multiplier=size_mult,
        natural_sl=round(natural_sl, 8) if natural_sl else None,
        levels_used=levels_desc,
        explanation=explanation,
    )


def validate_entry_distance(
    current_price: float,
    limit_price: float,
    direction: str,
) -> tuple[bool, str]:
    """Validate limit price distance from current market.

    GetClaw rule: warn if >5% from current price.
    Returns (is_valid, warning_message).
    Note: return value is informational — executor handles recalculation independently.
    """
    if current_price <= 0:
        return True, ""

    dist_pct = abs(current_price - limit_price) / current_price * 100

    if dist_pct > 5.0:
        return False, (
            f"Limit price ${limit_price:,.4f} is {dist_pct:.1f}% from market "
            f"(${current_price:,.4f}) — exceeds 5% threshold"
        )

    is_long = direction.upper() == "LONG"
    if is_long and limit_price >= current_price:
        return True, (
            f"LONG limit ${limit_price:,.4f} is above market ${current_price:,.4f} — "
            f"will fill immediately as taker"
        )
    if not is_long and limit_price <= current_price:
        return True, (
            f"SHORT limit ${limit_price:,.4f} is below market ${current_price:,.4f} — "
            f"will fill immediately as taker"
        )

    return True, ""


def limit_crosses_market(side: str, limit_price, current_price) -> Optional[bool]:
    """Does a LIMIT at ``limit_price`` sit at or through the market, so the venue
    fills it at once (as a taker) rather than resting it on the book?

    ``side`` is the order side (``buy``/``sell``) or the direction it opens
    (``LONG``/``SHORT``). A buy at or above the market crosses; a sell at or
    below it does. Equality counts: a limit AT the market fills at once too.
    ``None`` when either price is not a readable positive figure, because
    "does not cross" is a claim about two prices and nothing can be said
    about one that was not read -- and ``None`` for a side this reading does
    not know, never a guess at what the other side means.

    ONE reading. The engine's confirm-time block and the executor's
    placement each spelled it inline, and each decides something different
    from it: the engine's own idea is re-priced to rest as a maker, a
    hand-typed ticket is placed as typed and sent GTC so the venue fills it
    at the market up to the price the person named.
    """
    try:
        lp = float(limit_price)
        cp = float(current_price)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(lp) and math.isfinite(cp)) or lp <= 0 or cp <= 0:
        return None
    s = str(side or "").strip().lower()
    if s in ("buy", "long"):
        return lp >= cp
    if s in ("sell", "short"):
        return lp <= cp
    return None


def order_fill_price(side: str, limit_price, current_price: float) -> float:
    """The price an order's notional is measured at: the price it FILLS at.

    A limit that RESTS on the book fills at its own price, so that is the price
    it is sized and checked at. A limit that CROSSES fills at the market, the
    way a market order does, and a market order (``limit_price`` None) fills at
    the market. The executor sized every order at the market price and a
    resting limit filled at its own, so a SHORT resting 10% above the market
    placed 10% more margin than the ticket approved, past the caps and the
    Authority Envelope, and a LONG resting 10% below placed 10% less.

    A limit this reading cannot place (``limit_crosses_market`` answers None:
    a price that is not a readable positive figure, or a side it does not
    know) is measured at the market, which is what every order was measured at
    before this reading existed.
    """
    if limit_crosses_market(side, limit_price, current_price) is False:
        return float(limit_price)
    return float(current_price)


#: Producers whose limit LEVELS a person saw and confirmed as they stand. A
#: hand-typed ticket's levels are the person's own, and a scan card's ✅ says,
#: in the card's own words, that it "places the entry shown, as a limit order,
#: with its stop and target".
LEVELS_AS_SHOWN_SOURCES = frozenset({"manual", "scan_skill"})


def levels_as_shown(obj: object) -> bool:
    """Are this idea's (or this resting row's) entry, stop and target the ones
    a person confirmed AS SHOWN, so nothing may move them?

    True for a hand-typed ticket and a scan card's idea (their ``source``, or
    ``idea_source`` on the position the executor built from one), and for an
    idea whose ENTRY the person typed through the Limit button
    (``entry_typed``), whatever produced the rest of it.

    Three sites move a limit's levels, and each exempted only ``manual``:
    the engine's confirm-time re-price, the executor's confluence re-price
    (which can also turn the limit into a market order), and the drift
    fallback that chases a resting limit at the market with its stop and
    target shifted by the same percentage. Driven, a scan card showing
    99.4 / 95 / 106 with the market at 99.3 was handed to the executor as
    98.3 / 93.9 / 104.9, under a card that promised the entry shown.

    What this reading does NOT decide, stated: the clock a resting order
    rests on, and whether drift may CANCEL it. A hand-typed ticket rests on
    its own clock and is never drift-read (``limit_expiry_seconds``); a scan
    card's idea is the engine's analysis, rests on the engine's clock, and a
    level the market ran away from may still be cancelled. Cancelling places
    nothing; moving the levels places an order nobody confirmed.

    A value that is not a string, or ``entry_typed`` that is not literally
    True, reads as not shown: a stand-in answering every attribute truthily
    has confirmed nothing.
    """
    if getattr(obj, "entry_typed", None) is True:
        return True
    for name in ("idea_source", "source"):
        src = getattr(obj, name, None)
        if isinstance(src, str) and src:
            return src in LEVELS_AS_SHOWN_SOURCES
    return False


def placement_price(value) -> Optional[float]:
    """A price read off a resting row: a finite positive number, never a
    bool (JSON ``true`` is 1.0 to ``float``), or None."""
    if isinstance(value, bool):
        return None
    from bot.core.position_telemetry import price_on_record
    return price_on_record(value)


def resting_limit_drift(side: str, limit_price, current_price,
                        placed_market=None) -> Optional[tuple[float, str]]:
    """How far the market has moved AWAY from a resting limit, in percent,
    and what it was measured from; None when the prices cannot be read.

    The drift rule cancels (or, for the engine's own idea, chases) a resting
    limit once "the market drifts X% away", and it used to measure that as
    the limit's distance from the market. An engine pullback limit is placed
    up to one ATR from the market ON PURPOSE, so on an asset whose ATR is
    over the band the first monitor pass after placement read a drift that
    had not happened. Driven: a SOL LONG limit at 97.2 with the market at 100
    and not moving was cancelled on the next pass, or, with a trend up,
    marketed at 100 -- the pullback entry turned into the price the analysis
    said to wait below.

    With the market at placement on record the drift is the MARKET's move
    since then, signed so that away from the limit is positive: up for a buy
    limit, which rests below the market, and down for a sell limit. A move
    toward the limit is not drift; it is the fill coming. Basis
    ``"placement"``.

    A row placed before the market was recorded (or adopted from the venue)
    has none, and is measured as it always was, from the limit (basis
    ``"limit"``). Such a row has already been read under that rule on every
    pass since it was placed, so the ones still resting sit inside the band.
    """
    cur = placement_price(current_price)
    lim = placement_price(limit_price)
    if cur is None or lim is None:
        return None
    placed = placement_price(placed_market)
    word = str(getattr(side, "value", side) or "").upper()
    if placed is not None and word in ("LONG", "BUY", "SHORT", "SELL"):
        away = (cur - placed) if word in ("LONG", "BUY") else (placed - cur)
        return away / placed * 100.0, "placement"
    return abs(cur - lim) / lim * 100.0, "limit"
