"""Read a PNG card as the STRINGS IT DREW.

A card rendered to PNG is a surface nothing in this tree could read. The only
PNG guard that existed counts PIXELS
(``test_the_trend_headline_says_when_it_read_nothing``), which is the right
instrument for a COLOUR claim and cannot answer *what did it say* — so five
cards printed a measured zero for a figure nobody read and every suite stayed
green. A source scan cannot answer it either: the defect is not a spelling, it
is which quantity a figure holds, which is the distinction CLAUDE.md records
about ``size_usd``.

``PIL.ImageDraw.ImageDraw.text`` is the one chokepoint every string on every
card crosses, so that is where the reading goes. This is the *when there is no
seam, make one* case, and it makes every PNG renderer in
``bot/formatters/signal_card.py`` driveable rather than only one of them.

Two things it deliberately does not do, stated because a guard whose coverage
is overstated is the failure this repo exists to prevent:

* It reads TEXT only. A dot, a bar, a sparkline and a rounded rectangle are
  drawn by other methods and are invisible here — where a shape carries a
  claim (the scan grid's direction dot), the colour is what says so, and the
  pixel reading or the fill recorded beside each string is the instrument for
  that.
* It does not render a font. The strings are captured on the way IN, so what
  a reader would see if the glyph is missing from the font is a different
  question, and not this one.

The patch is installed and removed around one call, so nothing leaks into the
next test.
"""

from __future__ import annotations

import contextlib
from typing import Any, Callable, List, Tuple

from PIL import ImageDraw

#: One row per ``draw.text`` call: the string, and the fill it was drawn in.
Drawn = Tuple[str, Any]


@contextlib.contextmanager
def capture_text():
    """Collect every ``ImageDraw.text`` call made inside the block."""
    rows: List[Drawn] = []
    original = ImageDraw.ImageDraw.text

    def _spy(self, xy, text, *args, **kwargs):
        # `fill` is the second positional after `text` and is far more often
        # passed by keyword; read both rather than assuming either.
        fill = kwargs.get("fill", args[0] if args else None)
        rows.append((str(text), fill))
        return original(self, xy, text, *args, **kwargs)

    ImageDraw.ImageDraw.text = _spy
    try:
        yield rows
    finally:
        ImageDraw.ImageDraw.text = original


def drawn(fn: Callable[..., Any], *args, **kwargs) -> List[Drawn]:
    """Render a card and hand back every (string, fill) it drew, in order."""
    with capture_text() as rows:
        fn(*args, **kwargs)
    return list(rows)


def strings(fn: Callable[..., Any], *args, **kwargs) -> List[str]:
    """Just the strings, for an assertion that does not care about colour."""
    return [t for t, _ in drawn(fn, *args, **kwargs)]


def text_of(fn: Callable[..., Any], *args, **kwargs) -> str:
    """Every string joined by newlines — for a plain ``in`` / ``not in``.

    Anchor to a ROW from ``drawn`` where the claim is one cell's; this is for
    the case where any occurrence anywhere on the card is the finding.
    """
    return "\n".join(strings(fn, *args, **kwargs))


def fill_of(rows: List[Drawn], needle: str) -> Any:
    """The fill of the FIRST row whose string is exactly ``needle``.

    Exact rather than substring: a card draws its labels and its values
    through the same call, and ``"0%"`` is inside ``"10%"``. Raises when the
    string is not on the card at all, because an assertion about the colour of
    something that was never drawn is an assertion that cannot fail.
    """
    for text, fill in rows:
        if text == needle:
            return fill
    raise AssertionError(
        f"{needle!r} was not drawn; the card drew: "
        f"{[t for t, _ in rows]!r}")
