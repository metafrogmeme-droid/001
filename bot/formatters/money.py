"""One spelling for a signed dollar figure: ``+$209.66``, ``-$44.00``.

Every surface used to format ``f"${v:+,.2f}"``, which prints the sign after
the dollar (``$+209.66``), and a reader outside said so on 2026-10-19. The
sign goes before the currency, as a statement prints it. The helper takes a
number only: what to print for a figure nobody read ("—", "unread", "not
priced") is each surface's own sentence and stays where it is.
"""

from __future__ import annotations


def signed_usd(v, places: int = 2, grouped: bool = True) -> str:
    """``+$1,418.05`` / ``-$0.48`` / ``+$0.00``; thousands grouped by default."""
    n = float(v)
    body = f"{abs(n):,.{places}f}" if grouped else f"{abs(n):.{places}f}"
    sign = "-" if n < 0 else "+"
    return f"{sign}${body}"


def compact_signed_usd(v) -> str:
    """``+$1.2M`` / ``-$340K`` / ``+$12``: a market figure at a card cell's size.

    The sign before the currency, like ``signed_usd``; thousands and millions
    folded to one decimal because the cell has room for a magnitude, not a
    ledger. For a figure nobody read the caller prints its own word.
    """
    n = float(v)
    a = abs(n)
    sign = "-" if n < 0 else "+"
    if a >= 1e9:
        body = f"{a / 1e9:.1f}B"
    elif a >= 1e6:
        body = f"{a / 1e6:.1f}M"
    elif a >= 1e3:
        body = f"{a / 1e3:.0f}K"
    else:
        body = f"{a:.0f}"
    return f"{sign}${body}"
