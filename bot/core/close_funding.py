"""Funding is a cost, and the venue states it on the row this bot already reads.

A perpetual charges funding every settlement the position is open across. The
backtest was cured of subtracting that as zero -- `BacktestTrade.net_pnl_usd`
was `pnl - commission` with no funding term, and the cure was that "no funding"
has to be READABLE, because on a market that pays none it means there was
nothing to charge and on one that does it means nobody priced it, and those
must not produce the same number.

THE LIVE PATH NEVER GOT THAT CURE, and unlike the backtest it can read the
figure. `_close_from_history` fetches `/api/v2/mix/position/history-position`
and reads `openAvgPrice`, `closeAvgPrice`, `pnl`, `openFee`, `closeFee`,
`netProfit` and `leverage` off the matched row. The same row carries
`totalFunding` -- the accumulated funding over the position's whole life -- and
nothing in `bot/` read it. `closed_trade_row` had `pnl_usd`, `gross_pnl` and
`commission` and no funding field at all, so every reader of that record was
short by it: the governor's realized window, the loss streak, the cooldown, the
daily accumulator, the equity throttle's profit factor, parity's net,
/performance, the journal's R and the public close return.

WHICH FIGURE CONTAINS IT IS MEASURED, NOT BELIEVED. `docs/bitget-uta/trade.md`
documents the UTA twins of these fields -- `cumRealisedPnl` "Excluding fees and
funding costs", `netProfit` "Including fees and funding costs", `totalFunding`
"the accumulated fund fee during the position's duration. If the value is zero,
it indicates no fees have been charged" -- but the call this bot makes is the
CLASSIC v2 endpoint, whose table is not in this repo. So the relationship is
checked against the row's own fields rather than read off a doc for a sibling
endpoint:

    funding-inclusive:  netProfit == pnl + funding - (|openFee| + |closeFee|)
    fee-only:           netProfit == pnl - (|openFee| + |closeFee|)

The two candidates differ by exactly the funding, so the arithmetic decides --
and it decides only when it has to. When `totalFunding` is 0 both formulas are
the same expression, which is not a failure to measure: zero is inside every
net, the venue's own words for it are "no fees have been charged", and a caveat
about a cost of nothing is a hedge about nothing.

WHAT THIS CANNOT PLACE IS STATED RATHER THAN LEFT TO BE DISCOVERED. Because the
two candidates are one funding apart, a row decides only where the tolerance is
SMALLER than the funding -- and the relative term grows with the net. So a big
winner whose funding is under a tenth of a percent of its net comes back
UNPLACED, the funding is never folded, and the card says the content is unknown.
That is the conservative failure rather than a fabricated figure, and it is the
price of the relative term: an absolute cent alone would place such a row, and
would refuse any row whose own arithmetic misses by more than a cent. The one
real payload in this repo's vendored docs closes EXACTLY (-0.116 - 45.472 ==
-45.588), so the residual the relative term defends against is a bound nobody
here has measured; the pair stands because both of its failures are refusals
rather than guesses, and the exclusion is driven in
`tests/test_funding_is_a_cost_the_record_reads.py`.

That is `leverage_readback`'s shape one field over: the value, the field it came
from, and whether that field DECIDES -- never a number without the third.
"""

from __future__ import annotations

import math
from typing import Any, NamedTuple, Optional

#: How close the arithmetic has to come before a row is said to have decided.
#: Bitget spells these numbers as strings with eight decimals and a close can
#: be the average of several partial fills, so an absolute cent alone is too
#: tight on a large position and a relative term alone is too tight on a small
#: one.
ABS_TOL = 0.01
REL_TOL = 0.001

#: `totalFunding` is stated and zero. The venue's documented meaning of that is
#: "no fees have been charged", so it is a MEASUREMENT of no funding rather
#: than an absence -- and it is inside every net by arithmetic.
ZERO = "zero"
#: Stated, non-zero, and the row's own fields put it inside `netProfit`.
NET_INCLUDES = "net_includes"
#: Stated, non-zero, and the row's own fields put it OUTSIDE `netProfit`.
NET_EXCLUDES = "net_excludes"
#: Stated, non-zero, and the row does not carry what it would take to say --
#: a missing `pnl` or `netProfit`, or an arithmetic that closes to neither.
UNPLACED = "unplaced"
#: The row said nothing about funding. Every stage but position history is
#: this: a fill and a closed order carry no funding field, so a close priced
#: from one has a net whose funding content is unknown, not zero.
UNSTATED = "unstated"


class FundingRead(NamedTuple):
    """What a position paid in funding, and where that figure already sits."""

    #: Signed, in the margin coin, as the venue states it: NEGATIVE is funding
    #: this position PAID. None when the row carried no figure.
    usd: Optional[float]
    #: Does the venue's own `netProfit` already contain it? None where the row
    #: cannot say -- which is not the same fact as "it does not".
    in_net: Optional[bool]
    #: Which of the five readings above produced this.
    basis: str


def _num(value: Any) -> Optional[float]:
    """A venue field as a number, or None when it is not one.

    Bitget spells its numbers as strings, and an absent or empty field is an
    absence rather than a zero -- the distinction this whole module is about.
    A bool is not a figure: `float(True)` is 1.0, and a flag read as a cost is
    the shape `confidence_on_record` refuses one quantity over.
    """
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def funding_on_row(row: Any) -> FundingRead:
    """Read `totalFunding` off a position-history row and place it."""
    if not isinstance(row, dict):
        return FundingRead(None, None, UNSTATED)
    funding = _num(row.get("totalFunding"))
    if funding is None:
        return FundingRead(None, None, UNSTATED)
    if funding == 0.0:
        return FundingRead(0.0, True, ZERO)

    gross = _num(row.get("pnl"))
    if gross is None:
        gross = _num(row.get("achievedProfits"))
    net = _num(row.get("netProfit"))
    if gross is None or net is None:
        return FundingRead(funding, None, UNPLACED)

    # BOTH fee legs must be stated, or the arithmetic is missing a term and
    # this row cannot decide. That is the honest answer for a payload whose fee
    # fields are spelled some other way -- the UTA one calls them
    # `openFeeTotal`/`closeFeeTotal` -- where reading the gap as zero fees
    # would decide the question off an incomplete formula.
    #
    # The first draft SKIPPED an unstated leg and left `fees` at its 0.0 start,
    # under a comment claiming the leg was "left OUT rather than read as a fee
    # of zero". Those are the same number, so the comment claimed a check the
    # code did not make and the test named for it measured nothing: the
    # mutation round is what said so, by replacing the guard with
    # `abs(fee or 0.0)` and watching every assertion stay green.
    open_fee = _num(row.get("openFee"))
    close_fee = _num(row.get("closeFee"))
    if open_fee is None or close_fee is None:
        return FundingRead(funding, None, UNPLACED)
    fees = abs(open_fee) + abs(close_fee)

    tol = max(ABS_TOL, REL_TOL * abs(net))
    d_inc = abs(net - (gross + funding - fees))
    d_exc = abs(net - (gross - fees))
    if d_inc <= tol and d_exc > tol:
        return FundingRead(funding, True, NET_INCLUDES)
    if d_exc <= tol and d_inc > tol:
        return FundingRead(funding, False, NET_EXCLUDES)
    return FundingRead(funding, None, UNPLACED)


class FundingPlacement(NamedTuple):
    """How a venue-reported P&L and a funding read fit together.

    A reported figure either already carries the funding or does not, and the
    reconcile needs both halves of that at two different points: the figure the
    FEE arithmetic must run on (so the recorded gross stays the price move) and
    what the resulting net must gain. Restating either inside the reconcile is
    a second answer to one question -- which is what the first draft of this
    slice did, and which is why this leaf had no production caller at all.
    """

    #: The reported figure with any funding taken out of it. The fee
    #: arithmetic runs on this, so `gross_pnl` stays the price move rather
    #: than the move PLUS the funding -- a quantity with no name, on the field
    #: every card labels as the move before fees.
    base: float
    #: What a net built from `base` must gain, or None where nothing may be
    #: added. None is the whole tri-state: it says the figure's funding
    #: content is not known, which a card can state and a reader can weigh.
    add_to_net: Optional[float]


def place_funding(reported_pnl: float, funding_usd: Optional[float],
                  in_reported: Optional[bool]) -> FundingPlacement:
    """Split a venue-reported P&L and a funding read into base and addend.

    ``funding_usd`` is signed as the venue states it, so it is ADDED: a
    position that paid funding carries a negative figure and the net goes down.

    Four inputs reach this, one per arm:

    ``funding_usd is None``  every stage but position history -- a fill and a
                             closed order carry no funding field.
    ``in_reported is None``  an UNPLACED row: the venue stated a funding and
                             its own arithmetic closed to neither candidate.
                             Folding there would be a guess in the flattering
                             direction, on a cost.
    ``in_reported`` True     the `netProfit` branch on a NET_INCLUDES or ZERO
                             row: the funding comes out so the gross is the
                             move, and goes back into the net.
    ``in_reported`` False    a NET_EXCLUDES row, and every branch that derives
                             a net from the GROSS field (whose UTA twin is
                             documented "Excluding fees and funding costs").
    """
    if funding_usd is None or in_reported is None:
        return FundingPlacement(reported_pnl, None)
    base = reported_pnl - funding_usd if in_reported else reported_pnl
    return FundingPlacement(base, funding_usd)
