"""r_borrow = max(Aave v3 WETH, Morpho WETH).

One missing venue is not the other venue. The unread one could be higher,
so the max is unread.
"""
from __future__ import annotations

from decimal import Decimal

from bot.t2c.numbers import as_decimal


def borrow_rate(aave_weth: object, morpho_weth: object) -> Decimal | None:
    aave = as_decimal(aave_weth)
    morpho = as_decimal(morpho_weth)
    if aave is None or morpho is None:
        return None
    if aave >= morpho:
        return aave
    return morpho
