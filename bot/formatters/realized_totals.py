"""Realized totals over closed trades, with the unreadable rows counted.

WHY THIS IS A MODULE AND NOT THREE `sum()` CALLS.

`/portfolio` computed its realized figures inline:

    live_total_pnl   = sum((p.pnl_usd or 0)               for p in live_closed)
    live_total_fees  = sum((p.commission or 0)            for p in live_closed)
    live_total_gross = sum((p.gross_pnl or p.pnl_usd or 0) for p in live_closed)

`pnl_usd` is Optional BY DESIGN — live_executor's loader preserves a JSON null
verbatim, under its own comment recording that `float(x or 0)` reading it back
as 0.0 "silently converted 'we could not price this' into 'this broke even'".
So each `or 0` folded every unpriced close into the total as break-even, and
the card printed the result as THE net P&L with a green accent on it. In the
all-unpriced case it printed `$+0.00 🟢`: a measured break-even, in green,
built from zero measurements.

The third line was wrong twice over: `gross_pnl or p.pnl_usd` is a FALSINESS
test, so a genuinely break-even gross fell through to the net, and an absent
net then fell through to 0.

Five lines below those, the UNREALIZED total had already been rewritten to
count what marked and say so when the count was short, under the sentence
"a partial sum presented as a whole one is a wrong number wearing a measured
number's authority". The realized total — the bigger claim, because it is the
money already gone — was left alone. Extracting it makes the rule enforceable
in one place instead of restated at each call site, and gives the card
something that CANNOT hand back a manufactured zero: an unreadable total is
None, and None has no colour.

An EMPTY book is not unreadable. A flat book really is $0.00, so `[]` returns
0.0 with priced == 0 — the unreadable case is closes existing and none of them
priced. Callers distinguish the two with `is None`, never with falsiness.
"""
from __future__ import annotations

from typing import Any, Iterable


def _num(value: Any) -> float | None:
    """A real number, or None. Bools are not numbers.

    `isinstance(True, int)` is True in Python, so a stray bool would otherwise
    be summed as 1.0 — a fabricated dollar.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def realized_totals(closed: Iterable[Any]) -> dict:
    """Sum what can be read; report what could not.

    Returns:
        net       float | None   — None when rows exist and none are priced
        fees      float | None   — an absent commission is not a zero fee
        gross     float | None
        priced    int            — rows contributing to `net`
        unpriced  int            — rows that could not be priced
        total     int            — rows considered
    """
    rows = list(closed)
    net_sum = 0.0
    priced = 0
    fee_sum = 0.0
    fee_rows = 0
    gross_sum = 0.0
    gross_rows = 0

    for row in rows:
        pnl = _num(getattr(row, "pnl_usd", None))
        if pnl is not None:
            net_sum += pnl
            priced += 1

        fee = _num(getattr(row, "commission", None))
        if fee is not None:
            fee_sum += fee
            fee_rows += 1

        # `is None`, not `or`: a gross of exactly 0.0 is a real, measured
        # break-even gross and must not fall through to the net.
        gross = _num(getattr(row, "gross_pnl", None))
        if gross is None:
            gross = pnl
        if gross is not None:
            gross_sum += gross
            gross_rows += 1

    def _or_none(total: float, count: int) -> float | None:
        # No rows at all is a readable answer: a flat book is 0.00. Rows with
        # nothing readable in them is not.
        if not rows:
            return 0.0
        return total if count else None

    return {
        "net": _or_none(net_sum, priced),
        "fees": _or_none(fee_sum, fee_rows),
        "gross": _or_none(gross_sum, gross_rows),
        "priced": priced,
        "unpriced": len(rows) - priced,
        "total": len(rows),
    }


def best_and_worst(rows: Iterable[Any]) -> tuple:
    """(best, worst) by realized P&L, considering ONLY rows that were priced.

    A sort key is usually not a claim — putting unreadable rows at one end of
    an ordering is a choice, not an assertion. This is the exception, because
    the ORDER ITSELF is published: the performance card prints the ends of it
    as "Best 🏆" and "Worst".

        sorted(user_trades, key=lambda t: (t.pnl_usd or 0))

    mapped every unpriced close to 0.0, so on a book of losses the row nobody
    could price sorted HIGHEST and was crowned best. With
    [A: -5.0 priced, B: unpriced] it named B the best trade.

    Returns (None, None) when nothing was priced — the caller's existing "N/A"
    is the honest answer, and inventing a winner out of an unscorable book is
    the same defect as inventing its total.
    """
    rankable = [r for r in rows if _num(getattr(r, "pnl_usd", None)) is not None]
    if not rankable:
        return (None, None)
    ordered = sorted(rankable, key=lambda r: _num(getattr(r, "pnl_usd", None)))
    return (ordered[-1], ordered[0])
