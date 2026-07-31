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
    return win_stats(trades)["rate"]


def coverage_note(stats: dict, *, html: bool = True) -> str:
    """A disclosure for a rate computed over less than the whole set.

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
    body = (f"Win rate covers {scored} of {total} closes — {unscored} carry "
            f"no recorded P&L and are scored neither way.")
    return f"\n<i>{body}</i>" if html else f"\n{body}"
