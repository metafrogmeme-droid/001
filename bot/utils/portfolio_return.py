"""A portfolio return is total P&L over total margin — never a sum of percentages.

Three surfaces each computed the open-book header as ``sum(pnl_pct)``:

    bot/skills/telegram_handler.py    sum(p.get("pnl_pct", 0) for p in filled_positions)
    bot/formatters/rich_cards.py      sum(p.get("pnl_pct", 0) for p in positions)

Every ``pnl_pct`` is a LEVERAGED return measured against that position's OWN
margin. Adding them treats each position as if it were the whole account:
three positions down 10% read as -30%.

Live on 2026-08-02 the header said **-47.84%** on a book that was down
**-15.4%**:

    MSFT  -7.86%   net -$1.25   margin $12.51
    HYPE  -8.57%   net -$1.22   margin $11.38
    LTC  -33.67%   net -$2.13   margin  $5.93
    ────────────────────────────────────────
    sum of percentages          -50.10%   <- what was displayed
    -$4.60 / $29.82             -15.43%   <- the truth

The same account's ``/status`` reported "Daily PnL -2.2%" — because -$4.60
against $186.18 of equity is -2.5%. Two surfaces reading one book and
disagreeing by 20x, with the wrong one on the line the operator reads first
and is most likely to act on. LTC's real -$2.13 was the thing worth looking
at, and it was buried under a figure 3.3x too large.

    A SUM AND A WEIGHTED MEAN AGREE WHEN THERE IS EXACTLY ONE POSITION.

Which is why this lives in one module with a test that uses THREE positions
of different sizes. A single-position test passes against the broken code and
proves nothing — the same vacuous-guard shape this audit keeps finding.

The denominator is DEPLOYED MARGIN, not equity. Both are legitimate readings
and they are different numbers; the caller must say which it is showing.
``margin_usd`` is returned so a caller with an equity figure can compute the
other one without re-deriving the P&L.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional


def _finite(value: Any) -> Optional[float]:
    """The value as a float, or None when it is absent or not a measurement."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf
        return None
    return f


def open_book_return(positions: Iterable[Any]) -> dict:
    """Return on deployed margin across open positions, and its coverage.

    ``pct`` is **None** when nothing could be priced — not 0.0. A 0% return
    claims the book is exactly flat; "we could not read these marks" is a
    different statement and deserves a different value. This is the same rule
    ``win_rate.py`` applies to closes it cannot score, and the same rule the
    position cards already follow via ``price_unavailable``.

    A position whose mark could not be read leaves BOTH sides of the ratio.
    Counted in the denominator only, it would drag the result toward zero and
    state a measurement nobody made.

    ``size_usd`` is the margin committed (``cost_usd``), not notional. That is
    what the position dicts carry, and it is the correct denominator: notional
    would understate the return by the leverage multiple.
    """
    pnl = 0.0
    margin = 0.0
    measured = 0
    unmeasured = 0

    for p in positions or ():
        try:
            raw_pnl = p.get("pnl_usd")
            raw_margin = p.get("size_usd")
            raw_pct = p.get("pnl_pct")
            unavailable = bool(p.get("price_unavailable"))
        except AttributeError:
            unmeasured += 1
            continue

        v = _finite(raw_pnl)
        m = _finite(raw_margin)
        # A row can carry its P&L as a PERCENTAGE and no dollar figure —
        # several callers build the dicts that way. `render_open_positions`
        # already derives the dollars for the row (`size_usd * pnl / 100`),
        # so requiring `pnl_usd` here made one card contradict itself: the
        # row printed `🟢 +10.0% ($+50.00)` under a header reading
        # `⚠️ P&L unknown`. Deriving it in the SHARED helper is what stops
        # the two halves disagreeing — the same reason the win rate has one
        # source rather than five.
        #
        # "Absent is never a measurement" runs both ways. Reporting a mark
        # we DID read as unknown is equally a false claim, and the more
        # corrosive one: a card that cries unknown while showing the number
        # teaches the operator to scroll past the warning that matters.
        if v is None and not unavailable:
            pct = _finite(raw_pct)
            if pct is not None and m is not None and m > 0:
                v = m * pct / 100.0
        # A zero or negative margin has no honest denominator of its own and
        # contributes nothing to a shared one.
        if v is None or m is None or m <= 0:
            unmeasured += 1
            continue

        pnl += v
        margin += m
        measured += 1

    return {
        "pct": (pnl / margin * 100.0) if margin > 0 else None,
        "pnl_usd": round(pnl, 4),
        "margin_usd": round(margin, 2),
        "measured": measured,
        "unmeasured": unmeasured,
        "total": measured + unmeasured,
    }


def coverage_note(book: dict, *, html: bool = True) -> str:
    """A disclosure for a return computed over less than the whole book.

    Empty when everything was priced — a caveat printed on every healthy
    surface is how a real one gets skipped, the same reason
    ``win_rate.coverage_note`` stays silent on a complete pass.
    """
    try:
        unmeasured = int(book.get("unmeasured", 0) or 0)
        measured = int(book.get("measured", 0) or 0)
        total = int(book.get("total", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        return ""
    if unmeasured <= 0 or total <= 0:
        return ""
    body = (f"Covers {measured} of {total} positions — {unmeasured} have no "
            f"readable mark and are counted neither way.")
    return f"\n<i>{body}</i>" if html else f"\n{body}"
