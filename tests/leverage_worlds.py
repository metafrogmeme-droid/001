"""The one-leverage world, for tests of a property the split does not touch.

Since 11 October an order has two leverages: the one it is SIZED at
(`SIZING_LEVERAGE`, 5x) and the one the VENUE is set to (the standard, 10x),
and the venue follows the sized one in proportion (`bot.core.leverage`). A
test of a property that holds in both worlds -- the margin-risk cap reaching
the venue, one read of the leverage per order, the read-back governing the
fill -- was written with the venue and the sizing as one number, and its
figures are that world's. `one_leverage(n)` puts both standards at ``n`` for
the test, so the figures keep their meaning; the split itself is driven in
`tests/test_ten_x_keeps_the_five_x_trade.py`.

The real config object is changed in place (it is frozen, so through
``object.__setattr__``) and restored, because every reader imports the same
`CONFIG`; a stand-in config would forget the next attribute.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from bot.config import CONFIG


@contextmanager
def one_leverage(standard: int = 5) -> Iterator[None]:
    """Both standards at ``standard`` for the duration: the venue's default
    and the sizing leverage, so `order_standards` answers (n, n) and
    `venue_leverage` is the identity."""
    ex = CONFIG.exchange
    saved = (ex.default_leverage, ex.sizing_leverage)
    object.__setattr__(ex, "default_leverage", int(standard))
    object.__setattr__(ex, "sizing_leverage", int(standard))
    try:
        yield
    finally:
        object.__setattr__(ex, "default_leverage", saved[0])
        object.__setattr__(ex, "sizing_leverage", saved[1])
