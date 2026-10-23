"""A supplier quote as a fraction of notional.

The fair value, if the trailing mean persists, is max(0, mean − strike)
times hours / 8760. That is a rate, not a dollar premium. An unreadable
mean or an unreadable strike is an unreadable quote, not a quote of zero.
There is no volatility model behind it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from bot.caps.strictest import effective_cap
from bot.hrc.model import HOURS_PER_YEAR, carry_strike, collateral_for, hour_payoff


def premium_fraction(
    mean: Optional[Decimal],
    strike: Optional[Decimal],
    hours: Optional[int],
) -> Optional[Decimal]:
    """max(0, mean − strike) × hours / 8760.

    None when the mean or the strike was not read. A mean at or under the
    strike is a measured premium of zero.
    """
    if mean is None or strike is None:
        return None
    if not isinstance(mean, Decimal) or not isinstance(strike, Decimal):
        return None
    if type(hours) is not int or hours < 0:
        return None
    gap = mean - strike
    if gap < 0:
        gap = Decimal(0)
    return gap * Decimal(hours) / HOURS_PER_YEAR


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
    frac = premium_fraction(mean, used, hours if type(hours) is int else None)
    units = hour_payoff(10**18, mean, used)
    if units is not None and frac is None:
        return {"premium": None, "reason": "unreadable"}
    coll = collateral_for(10**18, used, hours if type(hours) is int else None)
    coll_frac = None if coll is None else Decimal(coll) / Decimal(10**18)
    out: dict = {"premium": frac, "collateral_fraction": coll_frac}
    if frac is None:
        out["reason"] = "unreadable"
    return out
