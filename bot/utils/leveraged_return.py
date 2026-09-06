"""Return on margin and dollar P&L for a leveraged position — a leaf out of the handler.

Two helpers, kept adjacent on purpose. `_leveraged_pnl_usd` was written so
"a leveraged % can never sit beside an unleveraged $ again", and then the
dollar was fixed at every site while the percent was fixed at one; on
2026-08-17 the same live position read -2.56% on /open_positions and
-0.13% a minute later on its detail card, with an identical $-0.64 beside
both. `_leveraged_return_pct` exists so the two bases are defined in one
place with the same guard clauses, and `tests/test_position_return_basis.py`
pins that they stay next to each other.

A leaf, not a mixin: pure arithmetic over four numbers. It moved here
because the position cards left the handler for the trading mixin while the
position-detail callback stayed, and a mixin must not import from the
handler; both read it from here now.
"""
from __future__ import annotations


def _leveraged_return_pct(entry: float, last: float, direction: str,
                          leverage: float) -> float:
    """Return on MARGIN (ROE) — the partner of `_leveraged_pnl_usd` below.

    The dollar got a helper on 2026-07-xx precisely so "a leveraged % can never
    sit beside an unleveraged $ again". The dollar was then fixed at every site
    and the PERCENT was fixed at one, so on 2026-08-17 the same live position
    rendered -2.56% on /open_positions and -0.13% a minute later on the
    position-detail card, with an identical $-0.64 beside both:

        raw price move      -0.13%
        x20 leverage (ROE)  -2.55%
        gross PnL           $-0.64   <- shown against BOTH percentages

    Read in sequence that is a 2.4-point recovery that never happened. Both
    numbers were individually correct; neither said which question it answered.

    This exists so the two helpers sit next to each other and a fourth call
    site cannot pick one basis for the dollar and the other for the percent.
    Same guard clauses and same leverage convention as the dollar helper, so
    they cannot disagree about an unusable input either.
    """
    if entry <= 0 or last <= 0:
        return 0.0
    raw = ((last - entry) / entry) if direction == "LONG" else ((entry - last) / entry)
    lev = leverage if (leverage and leverage > 0) else 1.0
    return raw * lev * 100.0


def _leveraged_pnl_usd(entry: float, last: float, direction: str,
                       cost_usd: float, leverage: float) -> float:
    """Real unrealized USD P&L for a leveraged futures position.

    = price-move-fraction × leverage × margin  (equivalently: ROE × margin, or
    price-move × notional). The live position cards previously computed this as
    price-move × *margin* only — dropping the leverage factor — so a 10x position
    showing a −28.6% ROE reported just −$0.43 instead of the real −$4.3. The
    percentage (ROE) and the dollar were on different bases; this puts them on the
    same one so a leveraged % can never sit beside an unleveraged $ again.
    """
    if entry <= 0 or last <= 0 or cost_usd <= 0:
        return 0.0
    raw = ((last - entry) / entry) if direction == "LONG" else ((entry - last) / entry)
    lev = leverage if (leverage and leverage > 0) else 1.0
    return raw * lev * cost_usd
