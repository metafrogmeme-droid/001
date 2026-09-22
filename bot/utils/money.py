"""Decimal at the boundary, and in the paper book.

A figure arriving as text or as a float becomes a Decimal, or None when it
is not a figure. ``quantize_to_tick`` and ``fmt`` are what a display does
with one. The paper book's open quantity, close settlement and
mark-to-market leg live in ``bot.utils.paper_money`` and call ``to_money``.

The risk engine's fixed-fractional base and the price-derived live
close live in ``bot.utils.live_money`` and call ``to_money``. The live
order-price snap stays on float. Changing an order price in the same
commit as a sizing reading is how a display fix moves live money.

``0`` is a reading. ``None``, a blank, a bool, a percent, NaN and infinity
are not.
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Optional


def to_money(value: object) -> Optional[Decimal]:
    """The value as a Decimal, or None when it is not a money figure.

    Floats go through ``Decimal(str(value))`` so the binary residue is not
    what gets stored. A leading ``$`` and thousands commas are spelling.
    A trailing ``%`` is a percent, which is a different quantity.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        amount = Decimal(str(value))
    else:
        text = str(value).strip()
        if not text or text.endswith("%"):
            return None
        text = text.replace("$", "").replace(",", "").strip()
        if not text:
            return None
        try:
            amount = Decimal(text)
        except InvalidOperation:
            return None
    if not amount.is_finite():
        return None
    return amount


def quantize_to_tick(value: object, tick: object) -> Optional[Decimal]:
    """``value`` rounded to ``tick`` by half-even, or None.

    None when the amount cannot be read, when the tick cannot be read,
    or when the tick is not strictly positive. Half-even matches
    Python's ``round`` on exact decimals.
    """
    amount = value if isinstance(value, Decimal) else to_money(value)
    step = tick if isinstance(tick, Decimal) else to_money(tick)
    if isinstance(amount, Decimal) and not amount.is_finite():
        return None
    if isinstance(step, Decimal) and not step.is_finite():
        return None
    if amount is None or step is None or step <= 0:
        return None
    return amount.quantize(step, rounding=ROUND_HALF_EVEN)


def fmt(value: object, places: int = 4, *, prefix: str = "$",
        thousands: bool = True) -> Optional[str]:
    """A rendered figure, or None when ``value`` is not one.

    Does not invent a zero and does not invent the word for an absence —
    the caller decides what None means on its own surface. A negative
    renders as ``-$1.2300`` (the sign before the prefix).
    """
    tick = Decimal(1).scaleb(-places)
    amount = quantize_to_tick(value, tick)
    if amount is None:
        return None
    negative = amount < 0
    magnitude = abs(amount)
    if thousands:
        body = f"{magnitude:,.{places}f}"
    else:
        body = f"{magnitude:.{places}f}"
    sign = "-" if negative else ""
    return f"{sign}{prefix}{body}"
