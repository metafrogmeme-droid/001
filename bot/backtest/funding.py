"""What the backtest could not charge, kept apart from what it charged as zero.

`BacktestTrade.net_pnl_usd` is documented in `models.py` as ``pnl - commission``
and that is exactly what it was: no funding term anywhere in either close path.
A perpetual position pays (or receives) funding every settlement it is open
across, so a backtest that omits it reports a net edge that is systematically
wrong — and always by a signed amount it never names.

THE SAME DEFECT IS ALREADY WRITTEN DOWN ONE MODULE OVER, with its cure.
`bot/proofofpnl/csf.py` records `compute_metrics` emitting ``funding: "0"``
under a comment saying perp funding was "PENDING (not in v0 data)" — *"an
unmeasured cost rendered as a measured zero"*, inside a commitment hash, on a
module whose docstring is about trust-tier honesty. What that section settled,
and what this one reuses, is that **"no funding" has to be readable**: on a
pure-spot epoch it means there was nothing to charge, and on a perp epoch it
means nobody priced it, *"and those must not produce the same number"*.

TWO THINGS THE BACKTEST CANNOT KNOW, AND ONE IT ALREADY DOES.

**It cannot tell whether its own symbol is a perpetual.** `csf.market_is_perp`
answers that for the statement path off the ccxt ``BASE/QUOTE:SETTLE`` suffix —
and driven, ``market_is_perp("BTC/USDT")`` is **False** while
``BacktestConfig().symbol`` *is* ``"BTC/USDT"``. Wiring that seam in here would
answer "not a perpetual, no funding applies" for every backtest this product
runs: a confident negative read off a field that cannot be present. Its own
docstring forbids the obvious alternative in as many words — *"guessing from
the quote currency would call every USDT market a perp"*. So perp-ness is an
INPUT here, never an inference, and unstated is `unpriced`.

**It cannot read a rate.** `data_loader` loads OHLCV and nothing else
(timestamp, open, high, low, close, volume), so there is no rate in the frozen
benchmarks to charge from. A rate is supplied or it is not; a default of `0.0`
would be the `funding: "0"` shape again, so the default is `None`.

**It already knows the hold.** Both close paths compute the entry and exit
timestamps, which is the other half — and `bot/risk/funding_clock.py` owns the
settlement definition the live path reads (``SETTLEMENT_INTERVAL_SEC``, "Bitget
USDT perps: 00/08/16 UTC") and the side convention (`pays_funding`: positive
rate, longs pay shorts). Restating either here would be a second answer, so
neither is restated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from bot.risk.funding_clock import SETTLEMENT_INTERVAL_SEC, pays_funding

#: Nothing was priced: no rate was supplied, or the caller never said whether
#: the market pays funding at all. NOT a zero — `usd` is None.
UNPRICED = "unpriced"
#: The caller stated this market does not pay funding. A real measured zero,
#: the `funding_applies` distinction: there was nothing to charge.
NOT_PERP = "not_perp"
#: Priced, and the position was open across no settlement at all. Also a real
#: zero, and a different fact from the one above.
NO_SETTLEMENT = "no_settlement"
#: Priced, and the position was open across at least one settlement.
CHARGED = "charged"


@dataclass(frozen=True)
class FundingRead:
    """A backtest position's funding, or the reason there is no figure.

    `usd` is SIGNED as a P&L adjustment — negative when the position paid,
    positive when it received — which is `csf.make_funding`'s own convention
    ("negative when the account paid, positive when it received. Both happen").
    It is `None` for `unpriced` and a float for every other state, so a caller
    that adds it without checking raises rather than quietly charging zero.
    Both engine call sites test `usd is not None` rather than a `priced`
    helper: the check is the thing a reader of a funding charge should see,
    and the helper it replaced had no production caller once they did.
    """

    state: str
    usd: Optional[float]
    intervals: Optional[int]


def settlements_spanned(entry_ts: float, exit_ts: float) -> int:
    """How many funding settlements a position open over ``(entry, exit]`` crossed.

    The count is of BOUNDARIES CROSSED, not the hold divided by the interval: a
    position opened at 07:59 and closed at 08:01 is open for two minutes and
    pays once, and one opened at 00:01 and closed at 07:59 is open for eight
    hours and pays nothing. Dividing would answer 0 and 0.
    """
    if exit_ts <= entry_ts:
        return 0
    interval = SETTLEMENT_INTERVAL_SEC
    return int(exit_ts // interval) - int(entry_ts // interval)


def _epoch(ts) -> Optional[float]:
    """Seconds since the epoch for a datetime, or None for anything else.

    A naive datetime is read as UTC, which is what the settlement grid is
    aligned to and what `data_loader` produces.
    """
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.timestamp()
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return float(ts)
    return None


def funding_for_position(direction: str,
                         notional_usd: float,
                         entry_time,
                         exit_time,
                         rate: Optional[float],
                         is_perp: Optional[bool]) -> FundingRead:
    """The funding a backtest position paid or received, or why there is none.

    `rate` is the per-settlement funding rate as a FRACTION (Bitget quotes
    0.0001 for one basis point), and `is_perp` is the caller's statement about
    the market — `None` for "not stated", which is `unpriced` rather than a
    guess. Funding is charged on the NOTIONAL, never the margin: it is a
    transfer between the two sides of the contract, sized by the contract.
    """
    if is_perp is False:
        return FundingRead(NOT_PERP, 0.0, 0)
    if is_perp is None or rate is None:
        return FundingRead(UNPRICED, None, None)

    entry_ts, exit_ts = _epoch(entry_time), _epoch(exit_time)
    if entry_ts is None or exit_ts is None:
        # A hold nobody can measure is not a hold of zero settlements.
        return FundingRead(UNPRICED, None, None)

    spanned = settlements_spanned(entry_ts, exit_ts)
    if spanned <= 0:
        return FundingRead(NO_SETTLEMENT, 0.0, 0)

    magnitude = abs(float(rate)) * float(notional_usd) * spanned
    # `pays_funding` is the one side convention, and a rate of exactly 0 pays
    # nobody — it answers False, and `magnitude` is already 0.0, so no separate
    # zero guard is reachable. The mutation round found the one I first wrote
    # here: a line no input can reach is not a check, it is a claim there is one.
    signed = -magnitude if pays_funding(direction, float(rate)) else magnitude
    return FundingRead(CHARGED, signed, spanned)


def funding_note(state: str, total: Optional[float], intervals: Optional[int] = None) -> str:
    """The sentence a report prints beside a net figure, for each state.

    The `unpriced` one is the point of the module: a reader who is shown a net
    P&L with no funding line reads it as a complete net, which is the claim
    this whole file exists to stop the backtest making silently.
    """
    if state == UNPRICED:
        return ("funding NOT priced — no rate was supplied and the frozen "
                "benchmarks carry none, so this net is before funding")
    if state == NOT_PERP:
        return "funding n/a — stated as a market that does not pay funding"
    if state == NO_SETTLEMENT:
        return "funding $0.00 — no position was open across a settlement"
    if state == CHARGED:
        across = f" across {intervals} settlement(s)" if intervals else ""
        return f"funding {total:+,.2f} USD{across}"
    return ("funding state not recognised — treat this net as before funding")


def funding_figure(state: str, total: Optional[float]) -> str:
    """What a scorecard's funding COLUMN prints — a figure, or the word that is
    not one.

    Both scorecards read this rather than each formatting a total, because a
    column that prints `$0.00` for an unpriced run is the whole defect in the
    narrowest possible space.
    """
    if state == UNPRICED or total is None:
        return "not priced"
    return f"${total:+,.2f}"


def combine(states: list) -> str:
    """One state for a run made of many positions.

    UNPRICED WINS. A run where one position could be priced and another could
    not is a run whose total is missing an unknown amount, and reporting it
    under the priced word would publish a partial sum as a whole — the shape
    `CLAUDE.md` tabulates as "a `sum(...)` over a set that includes unreadable
    rows". An empty run has nothing to price and says so.
    """
    seen = set(states or [])
    if not seen:
        return UNPRICED
    # UNPRICED is not in that set, so the subset check alone already answers
    # it — the explicit `UNPRICED in seen` clause it replaced was redundant,
    # and the mutation round proved it by deleting it with nothing changing.
    if not seen.issubset({NOT_PERP, NO_SETTLEMENT, CHARGED}):
        return UNPRICED
    if CHARGED in seen:
        return CHARGED
    if NO_SETTLEMENT in seen:
        return NO_SETTLEMENT
    return NOT_PERP
