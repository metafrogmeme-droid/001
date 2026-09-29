"""The price formatter every card and feed event prints a level with.

A leaf on purpose: it imports nothing, so a module on the money path can
print a price without pulling the card renderers (and their type backlog)
into the strict mypy gate. `rich_cards` re-exports it as `_fmt_price`.
"""

from __future__ import annotations


def fmt_price(p) -> str:
    """Smart price formatter — fewer decimals for larger prices.

    None renders as an em dash, never `$0.00`. Every caller is a display path,
    and a price is exactly the field this repo's doctrine opens on: an
    unfetchable one shown as a number is the defect, not the crash. Guarding
    here rather than at each call site means a new caller inherits the honest
    behaviour instead of having to remember it.
    """
    if p is None:
        return "—"
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "—"
    if p != p:            # NaN: not a price, and every comparison below is False
        return "—"
    if p >= 100:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:,.4f}"
    if p >= 0.01:
        return f"${p:,.5f}"
    if 0 < p < 0.0001:
        # Six places leave a PEPE-class price one or two significant digits,
        # so an entry at 0.0000102 and a stop at 0.0000098 both printed
        # `$0.000010`: a card whose stop sat ON its entry. Eight keeps four.
        return f"${p:,.8f}"
    return f"${p:,.6f}"
