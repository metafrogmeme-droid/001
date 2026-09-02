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


def is_reference_session_open(asset_class: str, now: datetime | None = None) -> bool:
    """Is the market this asset's PRICE references currently trading?

    NOT an order gate. `is_market_open()` answers "may I place this order";
    this answers "should the book be tight right now", which is what an
    off-hours spread-anomaly filter actually wants. Bitget stock perps trade
    24/7, but the equities they track do not, and a 47x spread at 05:00 UTC on
    a Sunday is thin-book noise rather than an event worth paging anyone about.

    Those were one function, and both callers wanted different answers.

    DST-AWARE, because the US session is defined in local time and a fixed UTC
    window is wrong for roughly four months a year. 09:30-16:00 America/New_York
    is 13:30-20:00 UTC under EDT and 14:30-21:00 under EST.

    Returns False when the class has no reference session (crypto: nothing to
    be shut) and — deliberately — when the timezone database cannot be read.
    False here means "do not treat this as a tight-book period", which
    attenuates a noisy alert. The alternative default would page an operator
    on thin-book spreads because a tzdata package was missing.
    """
    if asset_class not in _REFERENCE_SESSION:
        return False
    if now is None:
        now = datetime.now(UTC)
    try:
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo(_US_EQUITY_TZ))
    except Exception:  # noqa: BLE001 — missing tzdata must not raise into a caller
        logger.warning("US equity timezone unavailable; treating the reference "
                       "session as closed (attenuates, never pages)")
        return False
    if local.weekday() >= 5:
        return False
    minutes = local.hour * 60 + local.minute
    return (_US_SESSION_OPEN[0] * 60 + _US_SESSION_OPEN[1]) <= minutes < \
           (_US_SESSION_CLOSE[0] * 60 + _US_SESSION_CLOSE[1])


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
    """
    if not is_weekend:
        return stop_loss
    if asset_class in _ALWAYS_OPEN or asset_class in _PRE_IPO:
        return stop_loss

    sl_dist_pct = abs(entry_price - stop_loss) / entry_price if entry_price > 0 else 0

    # Widen by 40% (midpoint of GetClaw's 25-50% range)
    widen_factor = 1.40

    if direction.upper() == "LONG":
        # SL is below entry — move it further down
        new_sl_dist = sl_dist_pct * widen_factor
        new_sl = entry_price * (1 - new_sl_dist)
    else:
        # SHORT: SL is above entry — move it further up
        new_sl_dist = sl_dist_pct * widen_factor
        new_sl = entry_price * (1 + new_sl_dist)

    logger.info(
        "Gap-risk SL widened for %s: %.4f → %.4f (%.1f%% → %.1f%%)",
        asset_class, stop_loss, new_sl, sl_dist_pct * 100, new_sl_dist * 100,
    )
    return round(new_sl, 8)


def adjust_size_for_weekend(
    size_usd: float,
    asset_class: str,
    is_weekend: bool,
) -> float:
    """Reduce position size for weekend-queued metals (30-40% reduction).

    GetClaw rule: size down 30-40% for weekend gold limits.
    """
    if not is_weekend:
        return size_usd
    if asset_class not in _WEEKDAY_ONLY:
        return size_usd

    # 35% reduction (midpoint)
    reduction = 0.35
    new_size = size_usd * (1 - reduction)
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
