"""One row of the orphan-position list, as a pure function.

An "orphan" is a live position the bot did not open — opened by hand, or lost
from local state on a restart — discovered by reading the exchange directly.
`/open_positions` falls back to that read when it has nothing tracked locally,
and it is the list an operator opens *because they do not know what is out
there*, which is what makes every claim on it expensive.

WHY IT MOVED. It was built inline inside an async handler wrapped around live
exchange calls, so nothing could plant a venue response and assert what the row
said. CLAUDE.md names that shape directly, and the first thing the extraction
found was that a mutation reverting the mark price to `0` passed every test —
the renderer was well covered and the row builder was covered only by grep.

WHAT THE ROW REFUSES TO SAY. Each of these was a real value being manufactured
from a missing one, and every one of them leaned the same way — toward a
position looking safer, calmer or better understood than it was:

  the mark price      `0` renders as "$0.00". The branch had already stopped
                      falling back to the ENTRY price, on the grounds that
                      echoing the entry asserts the market is sitting exactly
                      there — and then fell back to zero, which is the same
                      assertion with a worse number.
  the stop / target   `0` renders as "None", i.e. THIS POSITION IS UNPROTECTED.
                      That is a finding, and it was also what a failed
                      `fetch_open_orders` produced for every symbol at once.
  the age             `0.0` renders as "0m", i.e. just opened.
  R:R                 an orphan has no thesis, so there is no reward target to
                      measure against. `0` is a ratio; this is the absence.
  entry / unrealized  `or 0` on a field the venue omits.

`orders_read` is the parameter that carries the distinction the caller has and
the row cannot infer: whether the conditional-order book was actually read. An
empty map means "no stops found" only if somebody looked.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


def _f(v: Any) -> Optional[float]:
    """A finite float, or None. Never a default."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def orphan_position_row(
    pos: dict,
    *,
    mark: Optional[float],
    sl_price: Optional[float],
    tp_price: Optional[float],
    commission_pct: float,
    now: Optional[datetime] = None,
) -> dict:
    """Build one `/open_positions` row from a raw exchange position.

    `mark` is the last price if it was read, else None. `sl_price` / `tp_price`
    are the trigger prices found in the conditional-order book — `0.0` when the
    book was read and held none, and **None when the book could not be read**.
    Those are different answers and the row keeps them apart.
    """
    sym = pos.get("symbol", "") or ""
    side = (pos.get("side") or "long").upper()
    contracts = _f(pos.get("contracts")) or 0.0
    entry_price = _f(pos.get("entryPrice"))
    if entry_price is None:
        entry_price = _f((pos.get("info") or {}).get("openPriceAvg"))
    notional = _f(pos.get("notional"))
    margin = _f(pos.get("initialMargin"))
    if margin is None:
        margin = _f(pos.get("collateral"))
    lev = _f(pos.get("leverage"))

    # The venue omits unrealizedPnl more often than it reports a real 0.00, and
    # for an orphan "break-even" is the single worst thing to assert. A genuine
    # 0 from the venue still reads as 0; only a missing field is unknown.
    unrealized = _f(pos.get("unrealizedPnl")) if pos.get("unrealizedPnl") is not None else None

    mark_read = mark is not None and mark > 0
    last_price = mark if mark_read else None

    pnl_pct = ((unrealized / margin * 100)
               if (unrealized is not None and margin is not None and margin > 0)
               else None)

    ts = _f(pos.get("timestamp"))
    if ts:
        opened = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        hold_h = ((now or datetime.now(timezone.utc)) - opened).total_seconds() / 3600
    else:
        hold_h = None

    def _dist(a: Optional[float], b: Optional[float]) -> Optional[float]:
        if a is None or b is None or not b:
            return None
        return abs(a - b) / b * 100

    sl_dist = _dist(sl_price, last_price) if sl_price else None
    tp_dist = _dist(tp_price, last_price) if tp_price else None

    def _order_state(price: Optional[float]) -> str:
        if price is None:
            return "unknown"
        return "exchange" if price > 0 else "none"

    return {
        "pair": sym.replace("/", "").replace(":USDT", ""),
        "direction": side,
        "entry": round(entry_price, 6) if entry_price else None,
        "current": round(last_price, 6) if last_price is not None else None,
        "price_unavailable": not mark_read,
        "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
        "pnl_usd": round(unrealized, 4) if unrealized is not None else None,
        "sl": round(sl_price, 6) if sl_price is not None else None,
        "tp": round(tp_price, 6) if tp_price is not None else None,
        "sl_dist_pct": round(sl_dist, 2) if sl_dist is not None else None,
        "tp_dist_pct": round(tp_dist, 2) if tp_dist is not None else None,
        "size_usd": round(margin, 2) if margin is not None else None,
        "notional_usd": round(notional, 2) if notional is not None else None,
        "leverage": round(lev, 2) if lev is not None else None,
        # An orphan carries no thesis, so there is no reward target to measure
        # against. 0 is a ratio; this is the absence of one.
        "rr_live": None,
        "quantity": contracts,
        "comm_pct": commission_pct,
        "hold_hours": round(hold_h, 1) if hold_h is not None else None,
        "sl_order": _order_state(sl_price),
        "tp_order": _order_state(tp_price),
        "trade_id": sym,
        "untracked": True,
        "status": "open",
    }
