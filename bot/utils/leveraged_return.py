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

The two REALIZED helpers at the bottom answer a third question — what a
CLOSED trade actually returned, net of what it cost to open and shut. They
live here for the reason the first two do: so nobody has to guess which
basis a percentage is on.
"""
from __future__ import annotations

from typing import Optional


def _finite(value: object) -> Optional[float]:
    """The value as a float, or None when it is absent or not a measurement."""
    if value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf
        return None
    return f


def position_leverage(stored: object, margin_usd: object,
                      notional_usd: object) -> Optional[float]:
    """A position's leverage — or None when it cannot be read.

    THE FALLBACK IT REPLACES WAS ONLY EVER EXERCISED IN THE CASE WHERE IT WAS
    WRONG. `callback_handler` derived leverage as `notional_now / sz`, where
    `sz` came from `cost_usd if cost_usd > 0 else entry * qty` — margin, or
    the NOTIONAL, under one name. So:

        cost_usd > 0   sz is the margin    notional / margin = the leverage ✓
        cost_usd == 0  sz is the notional  notional / notional ≈ 1.0        ✗

    and the second row is the ORPHAN — the position whose margin the venue
    never told us, which is precisely when a stored leverage is missing too
    and the fallback is reached at all. A derivation that is correct only when
    it is unnecessary.

    What that printed is the incident in this module's own docstring, arriving
    through a different door: at 1.0x the ROE collapses to the raw price move,
    so the detail card rendered `-0.13%` where the position had actually moved
    `-2.56%` on its margin. The fix there was to put the percent and the dollar
    on one basis; this is the basis itself going missing.

    None rather than 1.0, because 1.0x is a real leverage — a spot position has
    it — and a caller must be able to tell "unlevered" from "nobody could say".
    """
    stored_f = _finite(stored)
    if stored_f is not None and stored_f > 1:
        return stored_f
    margin = _finite(margin_usd)
    notional = _finite(notional_usd)
    if margin is None or notional is None or margin <= 0 or notional <= 0:
        return None
    return notional / margin


def _leveraged_return_pct(entry: float, last: float, direction: str,
                          leverage: float) -> Optional[float]:
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

    IT ANSWERED 0.0 ON AN UNUSABLE INPUT, and the justification below this
    module's realized helpers — "unrealized readings whose callers guard
    upstream" — was checked from outside the file and is half true.
    `trading_commands` guards the two prices and then hands over
    `getattr(p, "cost_usd", 0) or 0`, so an unrecorded margin reached the
    dollar helper and came back 0.0, two lines under that caller's own
    comment: "None means unreadable and the card renders '—'. Omit, never
    invent: a fabricated 0.00% is worse than an absent one, because it looks
    like a measurement." A claim about callers can only be checked from
    outside; this one had not been.

    None now, matching the two realized helpers at the bottom, and 0.0 is left
    to mean what it says: a measured break-even.
    """
    if entry <= 0 or last <= 0:
        return None
    lev = _finite(leverage)
    if lev is None or lev <= 0:
        return None
    raw = ((last - entry) / entry) if direction == "LONG" else ((entry - last) / entry)
    return raw * lev * 100.0


def _leveraged_pnl_usd(entry: float, last: float, direction: str,
                       cost_usd: float, leverage: float) -> Optional[float]:
    """Real unrealized USD P&L for a leveraged futures position.

    = price-move-fraction × leverage × margin  (equivalently: ROE × margin, or
    price-move × notional). The live position cards previously computed this as
    price-move × *margin* only — dropping the leverage factor — so a 10x position
    showing a −28.6% ROE reported just −$0.43 instead of the real −$4.3. The
    percentage (ROE) and the dollar were on different bases; this puts them on the
    same one so a leveraged % can never sit beside an unleveraged $ again.

    `cost_usd <= 0` is the ORPHAN — a position whose margin the venue never
    reported — so it takes None rather than 0.0 for the reason
    `realized_margin_return_pct` gives at length: 0.0 is a real, measured
    break-even and must not stand in for a quantity nobody recorded.
    """
    if entry <= 0 or last <= 0 or cost_usd <= 0:
        return None
    lev = _finite(leverage)
    if lev is None or lev <= 0:
        return None
    raw = ((last - entry) / entry) if direction == "LONG" else ((entry - last) / entry)
    return raw * lev * cost_usd


def realized_margin_return_pct(net_pnl: object,
                               margin_usd: object) -> Optional[float]:
    """What a CLOSED trade returned on its margin, NET of fees — or None.

    The partner of `_leveraged_return_pct` above, for a close rather than an
    open position, and it exists because the number three close-data producers
    publish is not this one. `close_pct` (live_executor:495) builds
    `pnl_pct_margin` out of two prices and a leverage and nothing else:

        pct = (exit - entry) / entry * 100 ;  margin_pct = pct * leverage

    That is the GROSS return on margin. Fees never enter it, and what it omits
    is not a rounding detail — it is a CONSTANT, because fees are a fraction of
    NOTIONAL and notional is margin times leverage:

        fees / margin  =  2 * fee_pct * leverage

    At the configured 0.06% taker (bot/config.py:563) that is 1.2% of margin at
    10x and 2.4% at 20x, before the trade has done anything. And it runs the
    FLATTERING way every time — gross is above net for a winner and above it
    for a loser too, so a win prints larger than it was and a loss smaller.

    `tests/test_close_card_pct_basis.py`'s own TRIA fixture is the
    demonstration, and it was internally consistent all along: it publishes
    +26.62% while $1.89 net on $7.44 of margin is +25.40%, the 1.22-point gap
    being exactly its $0.09 of fees over that margin.

    THERE USED TO BE A CONVENTION DIFFERENCE HERE, and this paragraph asserted
    it: the two helpers above answered 0.0 on an unusable input because they
    are "unrealized readings whose callers guard upstream". That was a claim
    about CALLERS, made from inside the file, and it can only be checked from
    outside — where it turned out to be half true. `trading_commands` guards
    the prices and then passes `cost_usd or 0`, so an unrecorded margin came
    back as a rendered $0.00. Both helpers answer None now and the whole module
    keeps one convention.

    The reasoning that was right here is the reasoning that spread: 0.0 is a
    real, measured break-even and must not stand in for "the margin was never
    recorded" — the position whose margin is missing is the ORPHAN, precisely
    the one nobody can vouch for. A non-positive margin is unreadable rather
    than infinite, so it takes the same answer.
    """
    pnl = _finite(net_pnl)
    margin = _finite(margin_usd)
    if pnl is None or margin is None or margin <= 0:
        return None
    return pnl / margin * 100.0


def fee_drag_on_margin_pct(fees: object, margin_usd: object) -> Optional[float]:
    """What the round trip cost, as a percentage of margin — or None.

    Always a POSITIVE magnitude: it is a cost, and printing it signed beside a
    signed return invites a reader to add the two and arrive at a third number
    that means nothing.
    """
    paid = _finite(fees)
    margin = _finite(margin_usd)
    if paid is None or margin is None or margin <= 0:
        return None
    return abs(paid) / margin * 100.0
