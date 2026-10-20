"""The one look every Telegram image card is drawn in.

The cards in ``signal_card.py`` and ``etf_card.py`` grew one at a time, each
with its own copy of the palette, the font loader and a cell helper, and by
October 2026 they read as a different product from the website's cards: a
520-pixel canvas drawn at one device pixel per logical pixel (soft on every
phone), DejaVu Sans, solid badges with black text, a gold stripe. This module
is the shared layer the website's duel cards already have in CSS:

* **One palette**, the website's (``app/public/embed.css``): a near-black
  ground, elevated surfaces, one blue accent, green and red only for a claim
  that was measured, and a muted grey for a figure nobody read. The names
  the renderers (and the tests) use, ``_GREEN``/``_RED``/``_GRAY``/
  ``_MUTED``/``_CYAN``/``_WHITE``, keep their meaning; only the values move.
* **One font**: Inter, vendored under ``fonts/`` so the card looks the same
  on every box the bot runs on; DejaVu when a file cannot be opened; a CJK
  face for translated labels, as before.
* **Supersampling**: every renderer keeps its logical 520-wide layout and
  draws through :class:`Draw`, which multiplies coordinates by :data:`S`
  and loads fonts at ``size * S``. Telegram shows the picture at phone
  width either way; at two device pixels per logical one the type is crisp.
* **Primitives** that give the cards the website's shapes: a tile (label
  over value on an elevated surface), a pill (tinted ground, coloured text,
  a hairline), the wordmark with the rune.

WHAT DOES NOT CHANGE. ``tests/png_text.py`` reads a card as the strings it
drew, through ``PIL.ImageDraw.ImageDraw.text``; :meth:`Draw.text` calls
exactly that, with the same string and the same fill, so every honesty test
on the cards still drives the real seam. The accent stripe stays at the top
edge in the P&L's own colour, because ``tests/test_close_card_unpriced.py``
reads it out of the pixels, and because colour is a claim.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any, Optional, Sequence

log = logging.getLogger("runeclaw.card_theme")

#: Device pixels per logical pixel. Every renderer lays out at 520 wide.
S = 2

# ── Palette (embed.css) ───────────────────────────────────────────────────
BG = (11, 14, 20)            # --e-bg: the ground
SURFACE = (20, 24, 33)       # --e-surface: a tile
SURFACE_2 = (26, 31, 42)     # --e-surface-2: an elevated tile
LINE = (36, 42, 56)          # --e-line: a hairline
LINE_2 = (48, 56, 74)        # --e-line-2
TEXT = (232, 236, 244)       # --e-text
TEXT_2 = (160, 170, 190)     # --e-text-2: secondary text
TEXT_3 = (112, 122, 142)     # --e-text-3: a label
ACCENT = (63, 182, 255)      # --e-accent: the one highlight
UP = (43, 213, 118)          # --e-up: a measured gain, LONG
DOWN = (255, 92, 92)         # --e-down: a measured loss, SHORT
GOLD = (230, 176, 60)        # the entry marker, an insight card's stripe
WARN = (240, 190, 60)        # a pattern badge, a caution
#: Deliberately NOT green and NOT red: an unreadable figure must not be
#: coloured like a profit or a loss. Distinct from TEXT_3 too, because the
#: tests tell a label from an abstention by its fill.
MUTED = (150, 155, 170)
DIM = (70, 78, 96)           # a watermark, an empty bar track

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_WEIGHT_FILES = {
    "regular": "Inter-Regular.otf",
    "medium": "Inter-Medium.otf",
    "semibold": "Inter-SemiBold.otf",
    "bold": "Inter-Bold.otf",
}
_DEJAVU = {
    False: ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/TTF/DejaVuSans.ttf"],
    True: ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"],
}
# CJK-capable faces (cover Latin too) for cards whose labels are translated.
CJK_FONT_PATHS = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKtc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]
_FONT_CACHE: dict = {}


def font(size: int, bold: bool = False, *, weight: Optional[str] = None, cjk: bool = False):
    """A font at ``size`` logical pixels (``size * S`` device pixels).

    ``bold`` is the renderers' historical flag: it selects SemiBold, which
    reads better than Bold at the sizes a card uses; ``weight`` names one of
    regular / medium / semibold / bold outright. With ``cjk`` the CJK faces
    are tried first, as the stats card always did for a translated label.
    """
    from PIL import ImageFont
    w = weight or ("semibold" if bold else "regular")
    key = (size, w, cjk)
    hit = _FONT_CACHE.get(key)
    if hit is not None:
        return hit
    px = int(round(size * S))
    paths: list = []
    if cjk:
        paths += CJK_FONT_PATHS
    paths.append(os.path.join(_FONT_DIR, _WEIGHT_FILES.get(w, "Inter-Regular.otf")))
    paths += _DEJAVU[w in ("semibold", "bold")]
    f: Any
    for path in paths:
        try:
            f = ImageFont.truetype(path, px)
            break
        except (OSError, IOError):
            continue
    else:
        f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def blend(color: Sequence[int], onto: Sequence[int] = SURFACE, amount: float = 0.16) -> tuple:
    """``color`` laid over ``onto`` at ``amount`` opacity: a pill's tinted ground."""
    return tuple(int(round(o + (c - o) * amount)) for c, o in zip(color, onto))


def _flat(xy: Any) -> list:
    """Coordinates in any of PIL's shapes, flattened and scaled."""
    out: list = []
    for v in xy:
        if isinstance(v, (tuple, list)):
            out.extend(_flat(v))
        else:
            out.append(v * S)
    return out


class Draw:
    """``ImageDraw`` at logical coordinates, drawn ``S`` times larger.

    Every method hands the real ``ImageDraw`` the same text and the same
    fill it was given, so a test that spies on ``ImageDraw.text`` sees what
    the card said and in which colour.
    """

    def __init__(self, image):
        from PIL import ImageDraw
        self.image = image
        self.d = ImageDraw.Draw(image)

    # ── PIL's own verbs, scaled ──
    def text(self, xy, text, fill=None, font=None, **kw):
        self.d.text(tuple(_flat(xy)), text, fill=fill, font=font, **kw)

    def textlength(self, text, font=None) -> float:
        return self.d.textlength(text, font=font) / S

    def rectangle(self, box, fill=None, outline=None, width: int = 1):
        self.d.rectangle(_flat(box), fill=fill, outline=outline, width=width * S)

    def rounded_rectangle(self, box, radius: int = 0, fill=None, outline=None, width: int = 1):
        self.d.rounded_rectangle(_flat(box), radius=radius * S, fill=fill, outline=outline, width=width * S)

    def line(self, xy, fill=None, width: int = 1):
        self.d.line(_flat(xy), fill=fill, width=max(1, width * S))

    def ellipse(self, box, fill=None, outline=None, width: int = 1):
        self.d.ellipse(_flat(box), fill=fill, outline=outline, width=width * S)

    def polygon(self, xy, fill=None, outline=None):
        self.d.polygon(_flat(xy), fill=fill, outline=outline)

    # ── The cards' shapes ──
    def tile(self, x: float, y: float, w: float, h: float, label: str, value: str,
             color=TEXT, f_label=None, f_value=None, *, label_color=TEXT_3,
             radius: int = 10, surface=SURFACE, pad: int = 12):
        """A label over a value on an elevated surface with a hairline."""
        self.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=surface, outline=LINE)
        self.text((x + pad, y + 9), label, fill=label_color, font=f_label or font(9, weight="medium"))
        self.text((x + pad, y + h - 10 - (f_value or font(15, bold=True)).size / S), value,
                  fill=color, font=f_value or font(15, bold=True))

    def pill(self, x: float, y: float, text: str, color, f=None, *, h: int = 20,
             filled: bool = False, pad: int = 8) -> float:
        """A rounded chip: tinted ground, hairline and text in ``color``.

        ``filled`` draws the ground solid in ``color`` with dark text, for the
        one place a card wants a loud badge. Returns the chip's width.
        """
        f = f or font(10, weight="semibold")
        tw = self.textlength(text, font=f)
        w = tw + pad * 2
        if filled:
            self.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=color)
            self.text((x + pad, y + (h - f.size / S) / 2 - 1), text, fill=BG, font=f)
        else:
            self.rounded_rectangle([x, y, x + w, y + h], radius=h // 2,
                                   fill=blend(color), outline=blend(color, amount=0.5))
            self.text((x + pad, y + (h - f.size / S) / 2 - 1), text, fill=color, font=f)
        return w

    def hairline(self, x0: float, y: float, x1: float):
        self.line([(x0, y), (x1, y)], fill=LINE, width=1)

    def asset(self, name: str, x: float, y: float, height: float) -> float:
        """Paste a brand asset (``assets/<name>.png``, RGBA) ``height`` logical
        pixels tall with its top-left at (x, y); returns its logical width, or 0
        when the file is missing (the card draws on without it)."""
        im = _asset(name, height)
        if im is None:
            return 0.0
        self.image.paste(im, (int(round(x * S)), int(round(y * S))), im)
        return float(im.width) / S

    def logotype(self, x: float, y: float, height: float = 14) -> float:
        """The RUNECLAW logotype (the serif mark with the three-bar glyph)."""
        return self.asset("wordmark", x, y, height)

    def wordmark(self, W: int, H: int, pad: int, f=None, *, height: float = 22):
        """The logotype at the bottom right, above the bottom stripe. ``f`` is
        accepted for the renderers that pass their footer font; the mark is a
        picture now and does not use it."""
        del f
        im = _asset("wordmark", height)
        if im is None:
            self.text((W - pad - self.textlength("RUNECLAW", font=font(10)), H - 22),
                      "RUNECLAW", fill=DIM, font=font(10))
            return
        self.image.paste(im, (int(round((W - pad) * S)) - im.width, int(round((H - 10 - height) * S))), im)


_ASSET_CACHE: dict = {}


def _asset(name: str, height: float):
    """A brand asset scaled to ``height`` logical pixels, RGBA; None when missing."""
    key = (name, round(height * S))
    if key in _ASSET_CACHE:
        return _ASSET_CACHE[key]
    try:
        from PIL import Image
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", name + ".png")
        src = Image.open(path).convert("RGBA")
        h = max(1, int(round(height * S)))
        im = src.resize((max(1, int(round(src.width * h / src.height))), h), Image.Resampling.LANCZOS)
    except Exception:
        im = None
    _ASSET_CACHE[key] = im
    return im


def new_card(W: int, H: int, bg=BG):
    """An ``S``-times canvas and its :class:`Draw`, for a ``W`` x ``H`` logical card."""
    from PIL import Image
    img = Image.new("RGB", (W * S, H * S), bg)
    return img, Draw(img)


def crop(img, W: int, H: int):
    """The card trimmed to its drawn height, in logical pixels."""
    return img.crop((0, 0, W * S, H * S))


def export(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def pillow_available() -> bool:
    """Whether a card can be drawn at all; a renderer answers ``b""`` when not."""
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


__all__ = [
    "S", "BG", "SURFACE", "SURFACE_2", "LINE", "LINE_2", "TEXT", "TEXT_2", "TEXT_3",
    "ACCENT", "UP", "DOWN", "GOLD", "WARN", "MUTED", "DIM", "CJK_FONT_PATHS",
    "font", "blend", "Draw", "new_card", "crop", "export", "pillow_available",
]
