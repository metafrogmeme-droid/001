"""The live base size and the price-derived close, in Decimal.

Stage C. The risk engine's fixed-fractional base, and the three live
closes that price a fill from an entry and an exit, are computed here
and handed back as floats. The trade record still stores floats and the
caller still rounds them. Later multipliers — session, regime, the
execution ceiling, the quality ladder, Kelly, the notional cap — stay
where they are. A close the venue already priced stays
``_reconcile_exchange_close_pnl``. Live order prices stay on float.

An input ``to_money`` cannot read raises. It is not settled as zero.
A numeric entry at or below zero is a reading: the stop distance is 0
and the floor then lifts it, which is what the float path did. A fee
rate of 0 and a quantity of 0 are readings.

``Decimal(str(float))`` then the caller's ``round`` matches the float
path on the cards this product sizes. It does not match on a half-cent
boundary. The suite pins one of each rather than snapping the Decimal
back onto the float.
"""
from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple

from bot.utils.money import to_money


class FractionalSize(NamedTuple):
    """Margin and the floored stop distance, as floats.

    Both come from one division. The note on the size trace prints the
    distance, and a second division at the call site would be a second
    answer about which stop the margin was sized against.
    """

    margin: float
    stop_distance_pct: float


class PriceClose(NamedTuple):
    """Gross, commission and net for a close priced off two prints.

    Full precision. The caller rounds to four decimals for the record.
    The net is not quantized here: half-even on a half-cent and
    ``round`` on the same float are different questions.
    """

    gross: float
    commission: float
    net: float


def _amount(value: object, name: str) -> Decimal:
    amount = to_money(value)
    if amount is None:
        raise ValueError(f"{name} is not a money figure")
    return amount


def fixed_fractional_margin(
    equity: object,
    risk_pct: object,
    entry: object,
    stop: object,
    *,
    floor: float = 0.001,
) -> FractionalSize:
    """Margin = equity × risk% / stop-distance, with the distance floored.

    ``risk_pct`` is the percent of equity the strategy may lose. The
    distance is ``abs(entry - stop) / entry`` when the entry is positive,
    otherwise 0, and then at least ``floor`` (0.1% — a near-zero stop
    must not produce an astronomical intermediate size). The floor lives
    here. A call site that restates it is a second answer.
    """
    eq = _amount(equity, "equity")
    risk = _amount(risk_pct, "risk_pct")
    e = _amount(entry, "entry")
    s = _amount(stop, "stop")
    fl = _amount(floor, "floor")
    dist = abs(e - s) / e if e > 0 else Decimal(0)
    if dist < fl:
        dist = fl
    margin = eq * (risk / Decimal(100)) / dist
    return FractionalSize(margin=float(margin), stop_distance_pct=float(dist))


def price_close(
    entry: object,
    exit_price: object,
    quantity: object,
    entry_fee_pct: object,
    exit_fee_pct: object,
    *,
    is_long: bool,
) -> PriceClose:
    """Gross, two-leg commission and net for one price-derived close.

    The entry leg and the exit leg carry their own fee percents. A paper
    close uses one rate on both notionals; this is the live path, and
    collapsing the two rates would change a live PnL. ``0`` is a book
    that charges nothing on that leg.
    """
    e = _amount(entry, "entry")
    x = _amount(exit_price, "exit")
    q = _amount(quantity, "quantity")
    entry_fee = _amount(entry_fee_pct, "entry_fee_pct")
    exit_fee = _amount(exit_fee_pct, "exit_fee_pct")
    gross = (x - e) * q if is_long else (e - x) * q
    commission = (e * q * entry_fee + x * q * exit_fee) / Decimal(100)
    return PriceClose(
        gross=float(gross),
        commission=float(commission),
        net=float(gross - commission),
    )
