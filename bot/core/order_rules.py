"""
RUNECLAW Order Rules — asset-specific execution guards.

Implements GetClaw-style order intelligence for UTA accounts:
  - ISOLATED margin (mandatory for all assets)
  - Market hours awareness for metals/stocks
  - Weekend SL widening for gap-risk assets
  - Weekend size reduction for metals
  - TP/SL deferral for gap-risk limit orders

Uses the existing _classify_symbol() from market_scanner.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from bot.compat import UTC

logger = logging.getLogger(__name__)

# ── Market Hours (UTC) ─────────────────────────────────────────────
# Bitget trading windows — all times in UTC.
# Crypto: 24/7
# Metals (XAUUSDT etc.): Mon 00:01 – Fri 23:59 (closed Sat/Sun)
# Stock perps (TSLA, NVDA etc.): 24/7 — see below
# Commodities (CL etc.): Similar to metals
#
# STOCK PERPS ARE NOT A SESSION INSTRUMENT, AND THIS FILE SAID THEY WERE.
#
# The window was `02:30 – 09:00 UTC`, derived from "21:30 – 04:00 CST". Those
# source hours are CHINA Standard Time (UTC+8) — 21:30–04:00 there IS the US
# session — so they convert to 13:30–20:00 UTC. Read as US Central instead,
# the arithmetic is self-consistent, which is why it survived review: the
# numbers check out, against the wrong zone. The comment on the code itself
# then claimed the SAME window was "9:30 AM - 4:00 PM ET", which is
# 13:30–20:00 UTC. Two comments, mutually exclusive, and the code matched
# neither market.
#
# Both were moot. Bitget moved Stock Futures to continuous 24/7 trading,
# weekends and holidays included, on 2026-02-07. There is no order window to
# gate on, so gating on one for 19 hours a day cost real money twice over:
# every stock-perp entry outside it had its stop WIDENED 25-50% as
# "weekend gap risk" on an instrument that never gaps for a closed venue, and
# every market order was downgraded to a limit.
#
# WHAT REMAINS TRUE is that the UNDERLYING's cash session is not 24/7, and
# spreads on these perps are thin when Wall Street is shut. That is a
# different question from "can I place an order", and one function answered
# both. `is_market_open()` answers the venue; `is_reference_session_open()`
# answers the underlying. See there.

_ALWAYS_OPEN = {"Crypto", "Stock"}  # Stock: Bitget stock perps, 24/7 since 2026-02-07
# Note: Pre-IPO tokens may have exchange-specific trading windows.
# Treated as 24/7 for now; monitor for maintenance-window rejections.
_PRE_IPO = {"Pre-IPO"}
_WEEKDAY_ONLY = {"Metal", "Commodity", "ETF"}  # closed weekends
_SESSION_HOURS: set[str] = set()  # no venue-gated classes remain

#: Classes whose PRICE references a market with its own session, even though
#: the perp trades around the clock. Used only for "should spreads be tight
#: right now", never to gate an order.
_REFERENCE_SESSION = {"Stock", "ETF"}
_US_EQUITY_TZ = "America/New_York"
_US_SESSION_OPEN = (9, 30)    # 09:30 ET
_US_SESSION_CLOSE = (16, 0)   # 16:00 ET


def is_market_open(asset_class: str, now: datetime | None = None) -> tuple[bool, str]:
    """Check if the market is open for this asset class.

    Returns (is_open, reason_if_closed).
    """
    if now is None:
        now = datetime.now(UTC)

    weekday = now.weekday()  # 0=Mon, 6=Sun

    if asset_class in _ALWAYS_OPEN or asset_class in _PRE_IPO:
        return True, ""

    if asset_class in _WEEKDAY_ONLY:
        if weekday >= 5:  # Saturday or Sunday
            return False, f"{asset_class} markets are closed on weekends (opens Monday)"
        return True, ""

    # Unknown class — assume open
    return True, ""


def reference_session_state(asset_class: str, now: datetime | None = None) -> str:
    """Is the market this asset's PRICE references currently trading? Four answers.

    ``"open"`` and ``"closed"`` are measurements. ``"none"`` is a class with no
    reference session (crypto: nothing to be shut). ``"unknown"`` is the
    timezone database being unreadable, and it is its own word because the two
    callers need OPPOSITE defaults from it: the off-hours spread filter wants
    to attenuate (treat as not-tight-book; never page over a missing tzdata
    package), while the sweep wants to run unfiltered (a class that cannot be
    clocked must still be scanned, or a container without tzdata never looks
    at a stock perp again and logs "session closed" as the reason). Folding
    "unknown" into "closed" served the first caller and would have lied to the
    second -- unreadable is not a measurement, here as everywhere else.

    NOT an order gate. `is_market_open()` answers "may I place this order";
    this answers "is the reference market moving right now". Bitget stock
    perps trade 24/7, but the equities they track do not: a 47x spread at
    05:00 UTC on a Sunday is thin-book noise, and an overnight candle fetch
    for NFLX stalls because nothing is printing.

    DST-AWARE, because the US session is defined in local time and a fixed UTC
    window is wrong for roughly four months a year. 09:30-16:00 America/New_York
    is 13:30-20:00 UTC under EDT and 14:30-21:00 under EST.
    """
    if asset_class not in _REFERENCE_SESSION:
        return "none"
    if now is None:
        now = datetime.now(UTC)
    try:
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo(_US_EQUITY_TZ))
    except Exception:  # noqa: BLE001 — missing tzdata must not raise into a caller
        logger.warning("US equity timezone unavailable; reference session state is "
                       "unknown (spread filter attenuates, sweep runs unfiltered)")
        return "unknown"
    if local.weekday() >= 5:
        return "closed"
    minutes = local.hour * 60 + local.minute
    in_session = (_US_SESSION_OPEN[0] * 60 + _US_SESSION_OPEN[1]) <= minutes < \
                 (_US_SESSION_CLOSE[0] * 60 + _US_SESSION_CLOSE[1])
    return "open" if in_session else "closed"


def is_reference_session_open(asset_class: str, now: datetime | None = None) -> bool:
    """`reference_session_state(...) == "open"`, for callers that want a bool.

    Returns False when the class has no reference session and -- deliberately --
    when the timezone database cannot be read. False here means "do not treat
    this as a tight-book period", which attenuates a noisy alert; the
    alternative default would page an operator on thin-book spreads because a
    tzdata package was missing. A caller that must tell "closed" from "could
    not read the clock" uses `reference_session_state` directly.
    """
    return reference_session_state(asset_class, now) == "open"


def is_weekend_queued(asset_class: str, now: datetime | None = None) -> bool:
    """Return True if a limit order placed now would sit queued over weekend/off-hours."""
    if now is None:
        now = datetime.now(UTC)

    if asset_class in _ALWAYS_OPEN or asset_class in _PRE_IPO:
        return False

    is_open, _ = is_market_open(asset_class, now)
    return not is_open


def adjust_sl_for_gap_risk(
    stop_loss: float,
    entry_price: float,
    direction: str,
    asset_class: str,
    is_weekend: bool,
) -> float:
    """Widen SL for gap-risk assets when placed during off-hours.

    GetClaw rule: widen SL by ~25-50% for weekend-queued metals/stocks.
    - Normal SL: 2% → Weekend SL: 2.5-3%

    IT REFUSES RATHER THAN INVENTS, and that is the whole of the change here.

    The guard used to be ``if entry_price > 0 else 0``, which set the stop
    DISTANCE to zero for an unreadable entry and then computed
    ``new_sl = entry_price * (1 - 0)`` — ``0.0``. The caller assigns on
    ``new_sl != old_sl``, so a long position's stop was set to zero (no stop at
    all) and audited as ``SL widened: $98.0000 → $0.0000, result="WIDENED"``:
    the removal of a stop, recorded as an improvement to it. An unreadable
    stop was worse still — ``abs(entry - 0)/entry`` is ``1.0``, widened to
    ``1.4``, giving a NEGATIVE stop.

    Every refusal returns ``stop_loss`` unchanged, which the caller sees as
    "no adjustment" and leaves the submitted stop in place for the risk engine
    to judge. That is the only safe direction: this function may make a stop
    wider, and must never be able to make one disappear.
    """
    if not is_weekend:
        return stop_loss
    if asset_class in _ALWAYS_OPEN or asset_class in _PRE_IPO:
        return stop_loss

    def _decline(why: str) -> float:
        # Loud, and at WARNING: silently returning the input would make a
        # refusal indistinguishable from "nothing needed widening".
        logger.warning(
            "Gap-risk SL widening DECLINED for %s (%s): entry=%r stop=%r dir=%r — "
            "the submitted stop is left exactly as it was",
            asset_class, why, entry_price, stop_loss, direction,
        )
        return stop_loss

    try:
        entry = float(entry_price)
        sl = float(stop_loss)
    except (TypeError, ValueError):
        return _decline("not numeric")
    if not (math.isfinite(entry) and math.isfinite(sl)):
        return _decline("not finite")
    if entry <= 0 or sl <= 0:
        return _decline("a price is not positive")

    # EXPLICIT, not "anything that is not LONG is a short". `direction` arrives
    # here as `Direction(str, Enum).value`, so today it is LONG or SHORT and
    # nothing else — but the fallthrough meant any other spelling ("BUY",
    # "long", "") moved a long's stop UP, through entry, to the wrong side.
    side = str(direction or "").strip().upper()
    if side not in ("LONG", "SHORT"):
        return _decline("direction is neither LONG nor SHORT")

    # A stop on the wrong side of entry is not a stop, and widening one would
    # silently move it ACROSS entry and call that a repair — inventing a
    # thesis the caller did not submit. Let the risk engine reject it instead.
    if (side == "LONG" and sl >= entry) or (side == "SHORT" and sl <= entry):
        return _decline("the stop is on the wrong side of entry")

    sl_dist_pct = abs(entry - sl) / entry

    # Widen by 40% (midpoint of GetClaw's 25-50% range)
    widen_factor = 1.40
    new_sl_dist = sl_dist_pct * widen_factor

    if side == "LONG":
        # SL is below entry — move it further down
        new_sl = entry * (1 - new_sl_dist)
    else:
        # SHORT: SL is above entry — move it further up
        new_sl = entry * (1 + new_sl_dist)

    # The widened stop must still BE a stop. A distance over 100% takes a long
    # through zero and out the other side, which is not a wider stop, it is
    # the absence of one wearing its name.
    if not math.isfinite(new_sl) or new_sl <= 0:
        return _decline("widening would put the stop at or below zero")

    logger.info(
        "Gap-risk SL widened for %s: %.4f → %.4f (%.1f%% → %.1f%%)",
        asset_class, sl, new_sl, sl_dist_pct * 100, new_sl_dist * 100,
    )
    return round(new_sl, 8)


def adjust_size_for_weekend(
    size_usd: float,
    asset_class: str,
    is_weekend: bool,
) -> float:
    """Reduce position size for weekend-queued metals (30-40% reduction).

    GetClaw rule: size down 30-40% for weekend gold limits.

    Same refusal rule as ``adjust_sl_for_gap_risk``: an unreadable size cannot
    be reduced by 35%, and returning ``nan`` or a negative would be handed
    straight to an order. This one is less dangerous than its sibling — the
    caller only shrinks — but a NaN size propagates into the order payload,
    so it is guarded on the same terms rather than on the same day it bites.
    """
    if not is_weekend:
        return size_usd
    if asset_class not in _WEEKDAY_ONLY:
        return size_usd

    try:
        size = float(size_usd)
    except (TypeError, ValueError):
        size = float("nan")
    if not math.isfinite(size) or size <= 0:
        logger.warning(
            "Weekend size reduction DECLINED for %s: size=%r is not a readable "
            "positive amount — left exactly as submitted", asset_class, size_usd)
        return size_usd

    # 35% reduction (midpoint)
    reduction = 0.35
    new_size = size * (1 - reduction)
    logger.info(
        "Weekend %s size reduced: $%.2f → $%.2f (%.0f%% reduction)",
        asset_class, size_usd, new_size, reduction * 100,
    )
    return round(new_size, 2)


def should_defer_tp_sl(asset_class: str, is_weekend: bool, order_type: str) -> bool:
    """Return True if TP/SL should be placed AFTER fill, not at placement.

    GetClaw rule: for weekend-queued limits on metals/stocks, set TP/SL
    after fill confirmation to avoid instant TP trigger on gap fill.
    """
    if order_type != "limit":
        return False
    if not is_weekend:
        return False
    if asset_class in _ALWAYS_OPEN or asset_class in _PRE_IPO:
        return False
    return True


# ── Quick reference: asset limits ──────────────────────────────────
ASSET_RULES = {
    "Metal":     {"min_sl_pct": 1.5, "weekend_sl_pct": 2.5, "max_leverage": 20},
    "Stock":     {"min_sl_pct": 2.0, "weekend_sl_pct": 3.0, "max_leverage": 10},
    "Commodity": {"min_sl_pct": 2.0, "weekend_sl_pct": 3.0, "max_leverage": 10},
    "ETF":       {"min_sl_pct": 2.0, "weekend_sl_pct": 2.5, "max_leverage": 10},
    "Crypto":    {"min_sl_pct": 1.0, "weekend_sl_pct": 1.0, "max_leverage": 125},
}
