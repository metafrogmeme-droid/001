"""Resting paper limits: the paper book enters the way live enters.

A paper confirm of a limit idea used to open the position at once, at the
limit price, whether or not the market ever came back to it. Live places
that limit and waits: from 2026-08-18 to 2026-10-14, 123 of the operator's
407 live records were orders that never filled (expired, cancelled, or
pulled for drift). Paper booked every one of them, at the better price.

Now a paper limit rests on the book (`PortfolioTracker.rest_limit`) and this
sweep, run on every paper monitoring pass, decides it on live's rules:

  * **expiry**: `limit_expiry_seconds`, live's own horizon (the engine's
    ideas 4h, a hand-typed ticket 24h);
  * **fill**: when the last price reaches the limit (a buy limit at or
    below, a sell limit at or above), at the limit price;
  * **drift**: once the market has moved `LIMIT_DRIFT_CANCEL_PCT` away since
    placement (`resting_limit_drift`), the order is cancelled, or chased at
    the market where live would chase it: an engine idea (never a typed
    ticket or a scan card's shown levels, `levels_as_shown`), with
    `LIMIT_DRIFT_MARKET_FALLBACK` on and `drift_chase_decision` saying yes,
    stop and target shifted by the same percentage as live shifts them.

A price that does not read decides nothing that pass, except expiry, which
needs no price. Paper reads the last price at each pass; live rests on the
venue and fills on any touch between passes, so paper can miss a touch live
would have filled. That bias is the other way from the one this removes, and
much smaller.
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable, Optional

from bot.config import CONFIG
from bot.core.limit_entry import (
    drift_chase_decision,
    drift_chase_precheck,
    drift_max_chase_pct,
    limit_crosses_market,
    placement_price,
    resting_limit_drift,
)

#: Where the bot records the first paper confirm made under this fill model.
#: Addendum 2 of the equal-sizing forward test
#: (docs/research/prereg/equal_sizing_forward_2026-10/ADDENDUM_2.md) opens
#: its paper window there: a paper trade opened before it was filled the old
#: way, at once at the limit price.
FILL_MODEL_MARKER = "data/paper_fill_model.json"
FILL_MODEL = "rests_like_live"


def mark_fill_model_start(now: Optional[float] = None) -> None:
    """Record, once, when the first paper confirm ran under this fill model.

    Written by the paper confirm itself, before it rests or fills anything,
    so no trade of the new model predates the record. A record already there
    is left as it is, readable or not: an unreadable one is the reader's to
    report, never this writer's to replace with a later date."""
    from datetime import datetime, timezone

    from bot.utils.atomic_write import atomic_write_json
    from bot.utils.paths import state_path
    path = state_path(FILL_MODEL_MARKER)
    if path.exists():
        return
    when = datetime.fromtimestamp(time.time() if now is None else now, tz=timezone.utc)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {"model": FILL_MODEL, "since": when.isoformat()})


#: What a sweep reports for each row it decided.
FILLED, EXPIRED, DRIFT_CANCELLED, CHASED, FILL_FAILED = (
    "filled", "expired", "drift_cancelled", "chased", "fill_failed")


def paper_limit_rests(idea, market_price) -> bool:
    """Does a paper confirm of ``idea`` rest, rather than fill now?

    A limit that sits on its own side of a readable market rests, as live's
    does. A limit at or through the market fills now (live's crosses the book
    as a taker), and so does a market order. With no readable market price,
    or levels that do not read, the confirm fills at the idea's entry, as
    every paper confirm did before; the confirm path refuses before here when
    it cannot read the market, so that arm is a caller with no price to give.
    """
    if getattr(idea, "order_type", None) != "limit":
        return False
    side = getattr(getattr(idea, "direction", None), "value", None)
    if side is None:
        return False
    return limit_crosses_market(side, getattr(idea, "entry_price", None),
                                market_price) is False


def paper_entry_price(idea, market_price) -> float:
    """The price a paper confirm that fills NOW fills at.

    A market order fills at the market. A limit at or through the market fills
    at the market up to its limit (a buy never above it, a sell never below).
    With no readable market price, the idea's own entry."""
    entry = float(idea.entry_price)
    mkt = placement_price(market_price)
    if mkt is None:
        return entry
    if getattr(idea, "order_type", None) != "limit":
        return mkt
    if getattr(getattr(idea, "direction", None), "value", None) == "LONG":
        return min(mkt, entry)
    return max(mkt, entry)


def touched(side: str, limit_price: float, price: float) -> bool:
    """Has the market reached a resting limit? A buy limit rests below the
    market and fills at or below it; a sell limit rests above."""
    word = str(getattr(side, "value", side) or "").upper()
    if word in ("LONG", "BUY"):
        return price <= limit_price
    return price >= limit_price


async def sweep_paper_limits(
    portfolio,
    prices: dict,
    *,
    fetch_bars: Callable[[str], Awaitable[Optional[list]]],
    now: Optional[float] = None,
) -> list[dict]:
    """Decide every resting limit on ``portfolio`` once; the events, in order.

    ``fetch_bars(asset)`` returns closed 15m bars for the drift chase, or
    None; it is called only for a row that has drifted and may be chased.
    """
    cfg = CONFIG.limit_orders
    now = time.time() if now is None else now
    events: list[dict] = []
    for row in portfolio.pending_limits:
        tid = row["trade_id"]
        idea = row["idea"]
        asset = idea.get("asset")
        side = idea.get("direction")
        lim = placement_price(idea.get("entry_price"))
        if now >= float(row["expires_at"]):
            portfolio.drop_limit(tid, "expired")
            events.append({"kind": EXPIRED, "trade_id": tid, "asset": asset})
            continue
        px = placement_price(prices.get(asset))
        if px is None or lim is None:
            continue
        if touched(side, lim, px):
            events.append(_fill(portfolio, tid, asset, lim, FILLED))
            continue
        if idea.get("source") == "manual":
            continue  # a hand-typed ticket is never drift-read, as live
        drift = resting_limit_drift(side, lim, px, row.get("placed_market"))
        if drift is None or drift[0] < float(cfg.price_drift_cancel_pct):
            continue
        max_chase = drift_max_chase_pct()
        if (row.get("chase_allowed") and cfg.drift_market_fallback
                and drift_chase_precheck(side, lim, px, max_chase_pct=max_chase) is None):
            bars = await fetch_bars(asset)
            chase, _detail = drift_chase_decision(
                side, lim, px, bars, min_adx=float(cfg.drift_market_min_adx),
                max_chase_pct=max_chase)
            if chase:
                ratio = px / lim
                stop = placement_price(idea.get("stop_loss"))
                target = placement_price(idea.get("take_profit"))
                events.append(_fill(
                    portfolio, tid, asset, px, CHASED,
                    stop_loss=stop * ratio if stop is not None else None,
                    take_profit=target * ratio if target is not None else None))
                continue
        portfolio.drop_limit(tid, "price_drift")
        events.append({"kind": DRIFT_CANCELLED, "trade_id": tid, "asset": asset,
                       "drift_pct": round(drift[0], 3)})
    return events


def _fill(portfolio, tid: str, asset, price: float, kind: str, *,
          stop_loss: Optional[float] = None, take_profit: Optional[float] = None) -> dict:
    try:
        portfolio.fill_limit(tid, price, stop_loss=stop_loss, take_profit=take_profit)
    except Exception as exc:  # noqa: BLE001 -- a fill the book refuses is said, not raised
        return {"kind": FILL_FAILED, "trade_id": tid, "asset": asset,
                "error": type(exc).__name__}
    return {"kind": kind, "trade_id": tid, "asset": asset, "price": price}
