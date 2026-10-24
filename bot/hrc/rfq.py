"""A supplier quote as a fraction of notional.

The primary series is a cap spread. If the trailing mean persists, the
fair value is max(0, min(mean, 0.40) − strike) times hours / 8760. That is
a rate, not a dollar premium. An unreadable mean or an unreadable strike
is an unreadable quote, not a quote of zero. There is no volatility model
behind it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from bot.caps.strictest import effective_cap
from bot.hrc.model import (
    DEFAULT_SPREAD_CEIL,
    KIND_CAP,
    KIND_SPREAD,
    accrual_per_unit,
    carry_strike,
    collateral_for_series,
)


def premium_fraction(
    mean: Optional[Decimal],
    strike: Optional[Decimal],
    hours: Optional[int],
    ceil: Optional[Decimal] = None,
) -> Optional[Decimal]:
    """max(0, min(mean, ceiling) − strike) × hours / 8760.

    ``ceil`` defaults to no ceiling other than the curve's own 100%, which
    is the cap. The primary series passes the 40% spread ceiling. None when
    the mean or the strike was not read. A mean at or under the strike is a
    measured premium of zero.
    """
    if mean is None or strike is None:
        return None
    if not isinstance(mean, Decimal) or not isinstance(strike, Decimal):
        return None
    if type(hours) is not int or hours < 0:
        return None
    kind = KIND_CAP if ceil is None else KIND_SPREAD
    per = accrual_per_unit(kind, mean, strike, ceil)
    if per is None:
        return None
    return per * Decimal(hours)


def quote_for(
    book: dict,
    vault: str,
    mean: Optional[Decimal],
    strike: Optional[Decimal],
    hours: Optional[int],
    requested: Optional[int],
    funding: Optional[Decimal],
    delta: Optional[Decimal],
) -> dict:
    """The fraction, or a refusal.

    A requested size above the strictest cap is refused. No proposals means
    there is no cap, so the size is not refused for that reason. A carry
    strike replaces the fixed strike when funding and a margin are passed.
    The token-unit payoff is computed so it cannot drift from the fraction,
    and it is not returned: the quote is the fraction.
    """
    if not isinstance(book, dict) or book.get("readable") is False:
        return {"premium": None, "reason": "unreadable"}
    cap = effective_cap(book, vault)
    if type(requested) is int and cap is not None and Decimal(requested) > cap:
        return {"premium": None, "reason": "above_cap"}
    used = strike
    if funding is not None or delta is not None:
        used = carry_strike(funding, delta)
    span = hours if type(hours) is int else None
    frac = premium_fraction(mean, used, span, DEFAULT_SPREAD_CEIL)
    per = accrual_per_unit(KIND_SPREAD, mean, used, DEFAULT_SPREAD_CEIL)
    units = None if per is None else int(Decimal(10**18) * per)
    if units is not None and frac is None:
        return {"premium": None, "reason": "unreadable"}
    coll = collateral_for_series(KIND_SPREAD, 10**18, used, span, DEFAULT_SPREAD_CEIL)
    coll_frac = None if coll is None else Decimal(coll) / Decimal(10**18)
    out: dict = {"premium": frac, "collateral_fraction": coll_frac}
    if frac is None:
        out["reason"] = "unreadable"
    return out
