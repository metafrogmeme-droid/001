"""QC-2b — order-book entry-quality checks.

Pure, side-effect-free helpers the live executor calls just before it places an
entry. The star is ``book_wall_verdict``: it looks at the L2 order book and
flags entries where a dominant opposing wall sits in the path between the entry
and the take-profit — a level so much larger than its neighbours that price is
likely to stall or reject there, giving away the setup's edge.

Design notes:
  * PURE. No I/O, no config reads, no clock. The executor fetches the book and
    passes it in; every threshold is an explicit argument. This is what makes
    it unit-testable and safe to reason about on a money path.
  * FAIL-OPEN, PER CHECK — not as a blanket rule. A missing / malformed /
    one-sided book returns "no flag" from ``book_wall_verdict``, because a wall
    is an EDGE-QUALITY heuristic and a degraded read is no evidence of one. It
    is the wrong rule for the two gates added below: a ticker whose age cannot
    be determined, or a spread that cannot be parsed, is precisely the state
    those gates exist to notice. So they report **unreadable** as its own
    state, and the executor decides what to do with it — rather than each
    returning the reassuring answer and letting the caller mistake silence for
    a clean read.
  * OBSERVE-FIRST. The executor defaults a new gate OFF, then WARN (log only),
    and only enforces (block) when explicitly switched on — the house rule for
    any new gate on the live path.
"""

from __future__ import annotations

from typing import Optional


def _norm_levels(levels) -> list[tuple[float, float]]:
    """Coerce a ccxt-style [[price, size], ...] ladder into clean float tuples,
    dropping anything non-numeric or non-positive. Never raises."""
    out: list[tuple[float, float]] = []
    if not levels:
        return out
    for lvl in levels:
        try:
            price = float(lvl[0])
            size = float(lvl[1])
        except (TypeError, ValueError, IndexError):
            continue
        if price > 0 and size > 0:
            out.append((price, size))
    return out


def book_wall_verdict(
    direction: str,
    entry: float,
    take_profit: Optional[float],
    bids,
    asks,
    *,
    band_pct: float = 1.5,
    wall_ratio: float = 4.0,
    imbalance_ratio: float = 3.0,
) -> dict:
    """Judge whether the book has an opposing wall in the entry→TP path.

    Args:
        direction: "LONG" or "SHORT" (case-insensitive).
        entry: entry/reference price.
        take_profit: TP price — bounds the path we care about. None/invalid
            just widens the band to ``band_pct`` alone.
        bids / asks: ccxt-style ladders [[price, size], ...].
        band_pct: how far above/below entry (percent) to inspect the path.
        wall_ratio: a single level counts as a "wall" when its size is at least
            this multiple of the average level size in the path band.
        imbalance_ratio: the path-side resting liquidity counts as an adverse
            "shelf" when it is at least this multiple of the entry-side
            liquidity in the same band.

    Returns:
        {"flag": bool, "reason": str, "metrics": {...}} — flag True means an
        obstruction was found. Always returns a dict; never raises.
    """
    d = str(direction or "").strip().upper()
    metrics: dict = {}
    try:
        entry = float(entry)
    except (TypeError, ValueError):
        return {"flag": False, "reason": "no-entry", "metrics": metrics}
    if entry <= 0 or d not in ("LONG", "SHORT"):
        return {"flag": False, "reason": "bad-input", "metrics": metrics}

    bids_n = _norm_levels(bids)
    asks_n = _norm_levels(asks)
    if not bids_n or not asks_n:
        return {"flag": False, "reason": "book-unavailable", "metrics": metrics}

    band = entry * (max(0.0, band_pct) / 100.0)
    if band <= 0:
        return {"flag": False, "reason": "no-band", "metrics": metrics}

    # The path side is the direction price must travel to reach TP:
    #   LONG  → up through the asks;  SHORT → down through the bids.
    if d == "LONG":
        path_levels = asks_n
        supp_levels = bids_n
        lo, hi = entry, entry + band
    else:
        path_levels = bids_n
        supp_levels = asks_n
        lo, hi = entry - band, entry

    # A valid TP inside the band tightens the path window to entry→TP.
    try:
        tp = float(take_profit) if take_profit is not None else None
    except (TypeError, ValueError):
        tp = None
    if tp is not None and tp > 0:
        if d == "LONG" and tp < hi:
            hi = max(entry, tp)
        elif d == "SHORT" and tp > lo:
            lo = min(entry, tp)

    path_band = [(p, s) for (p, s) in path_levels if lo <= p <= hi]
    supp_band = [(p, s) for (p, s) in supp_levels
                 if (entry - band) <= p <= (entry + band)]

    path_liq = sum(s for _, s in path_band)
    supp_liq = sum(s for _, s in supp_band)
    metrics["path_liq"] = round(path_liq, 6)
    metrics["supp_liq"] = round(supp_liq, 6)
    metrics["path_levels"] = len(path_band)

    if not path_band:
        return {"flag": False, "reason": "clear-path", "metrics": metrics}

    # 1) Concentrated wall: one level dwarfs the band's average level size.
    avg = path_liq / len(path_band)
    biggest_p, biggest_s = max(path_band, key=lambda x: x[1])
    wall_mult = biggest_s / avg if avg > 0 else 0.0
    metrics["wall_mult"] = round(wall_mult, 3)
    metrics["wall_price"] = biggest_p
    if len(path_band) >= 3 and wall_mult >= wall_ratio:
        return {"flag": True,
                "reason": f"wall {wall_mult:.1f}x avg at {biggest_p:g}",
                "metrics": metrics}

    # 2) Adverse shelf: the path side is far heavier than the entry side.
    imb = path_liq / supp_liq if supp_liq > 0 else float("inf")
    metrics["imbalance"] = round(imb, 3) if imb != float("inf") else None
    if imb >= imbalance_ratio:
        return {"flag": True,
                "reason": f"opposing shelf {imb:.1f}x support",
                "metrics": metrics}

    return {"flag": False, "reason": "ok", "metrics": metrics}


# ── QC-2 SAFEGUARDS 0a/0b, extracted from LiveExecutor.execute() ───────────
#
# Both were inline in a 1,717-line method, which is why nobody could plant a
# ticker and read back what the gate decided — and why both carried the same
# defect in different clothes:
#
#   0a  `_tk_ts = ticker.get("timestamp")` then `if _max_age > 0 and _tk_ts:`
#       — a ticker with NO timestamp skipped the staleness gate entirely, and
#       an unparseable one hit `except (TypeError, ValueError): _age = 0.0`,
#       which is not "we could not tell" but "the tick is brand new". 0.0 is
#       the freshest possible reading, invented for the one case where the
#       guard has the least information.
#
#   0b  `except (TypeError, ValueError): _bid = _ask = 0.0` then
#       `if _ask > _bid > 0:` — an unparseable quote collapsed into the same
#       branch as a venue that simply does not quote bid/ask, and both walked
#       past the spread check.
#
# Neither is a hypothetical: these run before every live entry, and the whole
# point of 0a is that sizing and gating against a price that may no longer
# exist is how a trade gets placed into a market that has moved.


def ticker_age_verdict(ticker, max_age_sec: float, now: float) -> dict:
    """How old is this tick — and say so when that cannot be answered.

    Args:
        ticker: a ccxt-style ticker dict (anything else reads as unreadable).
        max_age_sec: staleness ceiling; <= 0 disables the gate entirely.
        now: epoch seconds. Passed in, never read from the clock here, so a
            test can pin it — the module's no-clock rule.

    Returns:
        {"state": ..., "age_sec": float|None, "reason": str}

        state is one of:
          "disabled"    the ceiling is <= 0; nothing was checked
          "fresh"       measured, and within the ceiling
          "stale"       measured, and older than the ceiling
          "unreadable"  no timestamp, or one that will not parse

    `age_sec` is None for every state except fresh/stale. It is never 0.0 for
    a tick whose age is unknown.
    """
    if not isinstance(max_age_sec, (int, float)) or max_age_sec <= 0:
        return {"state": "disabled", "age_sec": None,
                "reason": "staleness ceiling disabled"}
    if not isinstance(ticker, dict):
        return {"state": "unreadable", "age_sec": None,
                "reason": "no ticker to read a timestamp from"}
    raw = ticker.get("timestamp")
    if raw is None:
        return {"state": "unreadable", "age_sec": None,
                "reason": "ticker carries no timestamp"}
    try:
        age = float(now) - float(raw) / 1000.0
    except (TypeError, ValueError):
        return {"state": "unreadable", "age_sec": None,
                "reason": f"ticker timestamp will not parse: {raw!r}"}
    # A timestamp in the FUTURE is not a fresh tick, it is a clock that
    # disagrees with ours — and negative age would sail through any ceiling.
    if age < 0:
        return {"state": "unreadable", "age_sec": None,
                "reason": f"ticker timestamp is {abs(age):.0f}s in the future"}
    if age > float(max_age_sec):
        return {"state": "stale", "age_sec": age,
                "reason": f"last tick is {age:.0f}s old (max {max_age_sec:.0f}s)"}
    return {"state": "fresh", "age_sec": age,
            "reason": f"last tick is {age:.0f}s old"}


def spread_verdict(ticker, max_spread_pct: float) -> dict:
    """Bid/ask spread as a percentage of mid, and whether it is too wide.

    Args:
        ticker: a ccxt-style ticker dict.
        max_spread_pct: ceiling in percent; <= 0 disables the gate.

    Returns:
        {"state": ..., "spread_pct": float|None, "bid": ..., "ask": ...,
         "reason": str}

        state is one of:
          "disabled"    ceiling <= 0
          "not_quoted"  the venue reports no bid/ask — a real and common fact
                        about some venues, and NOT the same as a bad read
          "unreadable"  bid/ask are present and will not parse, or are
                        nonsensical (non-positive, or ask below bid)
          "ok"          measured and within the ceiling
          "too_wide"    measured and over it
    """
    if not isinstance(max_spread_pct, (int, float)) or max_spread_pct <= 0:
        return {"state": "disabled", "spread_pct": None, "bid": None,
                "ask": None, "reason": "spread ceiling disabled"}
    if not isinstance(ticker, dict):
        return {"state": "unreadable", "spread_pct": None, "bid": None,
                "ask": None, "reason": "no ticker to read a quote from"}
    raw_bid, raw_ask = ticker.get("bid"), ticker.get("ask")
    if raw_bid is None and raw_ask is None:
        return {"state": "not_quoted", "spread_pct": None, "bid": None,
                "ask": None, "reason": "venue reports no bid/ask"}
    if raw_bid is None or raw_ask is None:
        # HALF a quote is not a quote, and it is not the same as no quote —
        # one side present says the venue does publish this, and the other
        # side is missing. Spelled out rather than left to fall into the
        # TypeError below, which is where mypy found it: relying on an
        # exception to classify a case makes the case invisible.
        return {"state": "unreadable", "spread_pct": None, "bid": raw_bid,
                "ask": raw_ask,
                "reason": f"half a quote: bid={raw_bid!r} ask={raw_ask!r}"}
    try:
        bid, ask = float(raw_bid), float(raw_ask)
    except (TypeError, ValueError):
        return {"state": "unreadable", "spread_pct": None, "bid": raw_bid,
                "ask": raw_ask,
                "reason": f"quote will not parse: bid={raw_bid!r} ask={raw_ask!r}"}
    if bid <= 0 or ask <= 0 or ask < bid:
        return {"state": "unreadable", "spread_pct": None, "bid": bid,
                "ask": ask,
                "reason": f"nonsensical quote: bid={bid} ask={ask}"}
    spread_pct = (ask - bid) / ((ask + bid) / 2.0) * 100.0
    state = "too_wide" if spread_pct > float(max_spread_pct) else "ok"
    return {"state": state, "spread_pct": spread_pct, "bid": bid, "ask": ask,
            "reason": (f"spread {spread_pct:.2f}% "
                       f"(max {max_spread_pct:.2f}%)")}
