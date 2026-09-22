"""Paper-book arithmetic in Decimal.

Stage B of the money plan. The open quantity, the close settlement, and the
mark that equity and exposure sum are computed here and handed back as
floats, because the trade record and the state file store floats. The
multiplication itself is Decimal, through ``to_money``, so a binary residue
is not what the product is.

An input ``to_money`` cannot read raises. It is not settled as zero.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import NamedTuple

from bot.utils.money import to_money

_QTY = Decimal("0.00000001")


class CloseFigures(NamedTuple):
    """The close, full precision, as floats.

    ``gross``, ``commission`` and ``net`` are the figures the caller rounds
    to cents for the record. ``margin`` is the collateral returned to the
    balance. The balance moves by ``margin + net`` before that rounding —
    the cents on the trade and the cents in the balance are different
    questions, and this object keeps both.
    """

    gross: float
    commission: float
    net: float
    margin: float


def _amount(value: object, name: str) -> Decimal:
    amount = to_money(value)
    if amount is None:
        raise ValueError(f"{name} is not a money figure")
    return amount


def open_quantity(size_usd: object, leverage: object, entry_price: object) -> float:
    """Quantity for a paper open: notional / entry, at 8 decimal places.

    ``size_usd`` is the margin. Notional is margin times leverage.
    """
    size = _amount(size_usd, "size")
    lev = _amount(leverage, "leverage")
    entry = _amount(entry_price, "entry")
    if entry <= 0 or lev <= 0:
        raise ValueError("entry and leverage must be positive")
    qty = (size * lev / entry).quantize(_QTY, rounding=ROUND_HALF_EVEN)
    return float(qty)


def close_figures(
    entry: object,
    exit_price: object,
    quantity: object,
    leverage: object,
    commission_pct: object,
    *,
    is_long: bool,
) -> CloseFigures:
    """Gross, commission, net and margin for one paper close.

    Commission is the rate on the entry notional and the exit notional.
    ``commission_pct`` is the percent the book was configured with; ``0``
    is a book that charges nothing.
    """
    e = _amount(entry, "entry")
    x = _amount(exit_price, "exit")
    q = _amount(quantity, "quantity")
    lev = _amount(leverage, "leverage")
    pct = _amount(commission_pct, "commission_pct")
    if lev <= 0:
        raise ValueError("leverage must be positive")
    gross = (x - e) * q if is_long else (e - x) * q
    size = e * q
    exit_notional = x * q
    margin = size / lev
    commission = (size + exit_notional) * (pct / Decimal(100))
    net = gross - commission
    return CloseFigures(
        gross=float(gross),
        commission=float(commission),
        net=float(net),
        margin=float(margin),
    )


def leg_value(
    entry: object,
    quantity: object,
    leverage: object,
    mark: object,
    *,
    is_long: bool,
) -> tuple[float, float]:
    """``(margin + unrealized, unrealized)`` for one open position.

    The margin is entry times quantity over leverage — the collateral the
    open deducted — and the unrealized term is the price move times the
    quantity.
    """
    e = _amount(entry, "entry")
    q = _amount(quantity, "quantity")
    lev = _amount(leverage, "leverage")
    px = _amount(mark, "mark")
    if lev <= 0:
        raise ValueError("leverage must be positive")
    margin = e * q / lev
    upnl = (px - e) * q if is_long else (e - px) * q
    return float(margin + upnl), float(upnl)
