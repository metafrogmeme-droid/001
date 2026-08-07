"""A closed trade with no recorded P&L is not a loss.

`LivePosition.pnl_usd` is `Optional[float] = None`, and five surfaces counted
wins like this:

    wins = sum(1 for t in live_closed if (t.pnl_usd or 0) > 0)
    win_rate = wins / len(live_closed)

`None or 0` is 0, and `0 > 0` is false — so a close the record cannot price
was filed as a DEFEAT, while `len(live_closed)` kept it in the denominator.
Every unpriced close therefore pushed the displayed win rate DOWN, on the
number the operator reads first.

The same defect shipped on the web side in the Arena discipline read (#1019)
and in the edge metrics (#1017). This module exists so the bot's five sites
share one answer instead of five copies of the mistake — the lesson from
those two being that fixing the LINE leaves the surface half-cured, and the
fix has to follow the VALUE.

Three rules, all of them the same rule:

  * a trade with no readable P&L is scored NEITHER way and leaves both the
    numerator and the denominator;
  * a recorded 0.0 IS a measurement — falsy, and real — so it stays scored,
    counting as a non-win because it is not a gain;
  * how many were unscorable travels WITH the rate, because "60% of 20" and
    "60% of the 12 we could price" are different readings and only that
    count tells them apart.

The RATE was cured and the TOTAL printed beside it was not. `pnl_stats`
below is the same three rules applied to the sum, added after the /portfolio
card was found rendering

    Realized PnL (all-time): 🟢 $+0.00

off `sum(p.pnl_usd or 0 for p in closed)` — which is two claims, not one. On
an empty set it is `$0.00`, asserting a measured break-even where the truth
is "no closed trades recorded"; on a partial set it books every unpriced
close as break-even and prints the result as a whole total. The green accent
says "not down" as loudly as the digits do, so both come from the same
tri-state: a total nothing could price is None, and None renders as neither
a number nor a colour.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional


def _pnl(trade: Any) -> Optional[float]:
    """The recorded P&L, or None when there isn't one. Never raises.

    Accepts an object with ``.pnl_usd`` or a mapping with ``pnl_usd``/``pnl``,
    because the callers span live positions, journal dicts and website rows.
    """
    for key in ("pnl_usd", "net_pnl", "pnl"):
        try:
            if isinstance(trade, dict):
                if key not in trade:
                    continue
                raw = trade.get(key)
            else:
                if not hasattr(trade, key):
                    continue
                raw = getattr(trade, key)
            if raw is None:
                continue
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v != v or v in (float("inf"), float("-inf")):   # NaN / inf
            continue
        return v
    return None


def win_stats(trades: Iterable[Any]) -> dict:
    """Wins, scored count, unscored count, and the rate over SCORED trades.

    ``rate`` is None when nothing could be scored — not 0.0. A 0% win rate is
    a claim that everything lost; "we could not price any of these" is a
    different statement and gets a different value.
    """
    wins = scored = unscored = 0
    for t in trades or ():
        p = _pnl(t)
        if p is None:
            unscored += 1
            continue
        scored += 1
        if p > 0:
            wins += 1
    return {
        "wins": wins,
        "scored": scored,
        "unscored": unscored,
        "total": scored + unscored,
        "rate": (wins / scored) if scored > 0 else None,
    }


def win_rate(trades: Iterable[Any]) -> Optional[float]:
    """Convenience: the rate alone, or None when nothing was scorable."""
    rate = win_stats(trades)["rate"]
    return None if rate is None else float(rate)


def pnl_stats(trades: Iterable[Any]) -> dict:
    """The realized total, and how much of the set it actually covers.

    ``total`` is None when NOTHING could be priced — including the empty set.
    That is the whole point: `sum(())` is `0.0`, and a card that prints it
    says the book is flat when the truth is that there is no book. A total
    over zero measurements is not a measurement.

    A partial set still gets a total, because the sum of what we could read
    is a real quantity and hiding it would lose information. It travels with
    ``scored``/``unscored`` so the caller can disclose the window — the same
    contract as ``win_stats``, deliberately, since both run over the same
    reader and must agree about which rows were legible.
    """
    total = 0.0
    scored = unscored = 0
    for t in trades or ():
        p = _pnl(t)
        if p is None:
            unscored += 1
            continue
        scored += 1
        total += p
    return {
        "total": total if scored > 0 else None,
        "scored": scored,
        "unscored": unscored,
        "count": scored + unscored,
    }


def realized_total(trades: Iterable[Any]) -> Optional[float]:
    """Convenience: the total alone, or None when nothing was scorable."""
    total = pnl_stats(trades)["total"]
    return None if total is None else float(total)


def coverage_note(stats: dict, *, html: bool = True) -> str:
    """A disclosure for figures computed over less than the whole set.

    Names the TOTAL as well as the rate: they run over the same reader and
    therefore share a window, and the card prints them two lines apart. A
    note that qualified only the rate left the total beside it reading as
    complete — the half-fixed surface, one line down.

    Empty when everything was scorable — a caveat printed on every healthy
    surface is how a real one gets skipped, the same reason the scan coverage
    note stays silent on a complete pass.
    """
    try:
        unscored = int(stats.get("unscored", 0) or 0)
        total = int(stats.get("total", 0) or 0)
        scored = int(stats.get("scored", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        return ""
    if unscored <= 0 or total <= 0:
        return ""
    body = (f"Win rate and realized P&L cover {scored} of {total} closes — "
            f"{unscored} carry no recorded P&L and are scored neither way.")
    return f"\n<i>{body}</i>" if html else f"\n{body}"
