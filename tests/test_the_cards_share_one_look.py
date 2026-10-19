"""Every Telegram image card is drawn through one theme (bot/formatters/card_theme.py).

The cards grew one renderer at a time, each with its own palette, font loader
and cell helper, and by 2026-10-19 they read as a different product from the
website's cards. The theme is the shared layer; this file holds the renderers
to it, and holds the theme to the two contracts the honesty tests depend on:
``ImageDraw.text`` still sees every string with its fill, and the accent
stripe is still the top edge of the picture.
"""
from __future__ import annotations

import io
import os
import re

import pytest

from bot.formatters import card_theme as T
from bot.formatters import etf_card
from bot.formatters import signal_card as SC
from tests.png_text import drawn, fill_of
from tests.source_scan import code_only

HERE = os.path.dirname(os.path.abspath(__file__))
FORMATTERS = os.path.join(os.path.dirname(HERE), "bot", "formatters")

POSITION = {"symbol": "B/USDT:USDT", "direction": "SHORT", "is_live": False, "entry": 0.12479, "now": 0.1211,
            "pnl_pct": 14.78, "pnl_usd": 209.66, "net_pnl": 207.86, "fees": 1.79, "size_usd": 1418.05,
            "leverage": 5, "hold_time": "6.5h", "rr": 0.6, "sl": 0.13095, "tp": 0.11555, "sl_pct": 8.1,
            "tp_pct": 4.6, "sl_status": "bot-managed", "tp_status": "bot-managed"}


def _png(data: bytes):
    from PIL import Image
    assert data.startswith(b"\x89PNG")
    return Image.open(io.BytesIO(data)).convert("RGB")


class TestOneLook:
    def test_no_renderer_keeps_a_palette_or_a_font_loader_of_its_own(self):
        """A second copy of the palette is a second answer about what green
        means; a second font loader is a second face. Both are scanned for
        because a driven render cannot tell which file a colour came from."""
        for name in ("signal_card.py", "etf_card.py"):
            src = code_only(open(os.path.join(FORMATTERS, name), encoding="utf-8").read())
            assert "ImageFont.truetype" not in src, f"{name} loads its own font"
            assert "Image.new(" not in src, f"{name} makes its own canvas"
            assert "ImageDraw.Draw(" not in src, f"{name} draws past the theme"
            assert not re.search(r"^_(?:GREEN|RED|WHITE|BG|CARD_BG|BORDER|MUTED) = \(\d", src, re.M), (
                f"{name} spells a colour by hand")

    def test_the_vendored_face_is_the_one_a_card_draws_with(self):
        f = T.font(14, bold=True)
        path = getattr(f, "path", "") or ""
        assert path.endswith(os.path.join("fonts", "Inter-SemiBold.otf")), path
        assert T.font(14, weight="bold").path.endswith("Inter-Bold.otf")
        assert T.font(14).path.endswith("Inter-Regular.otf")
        # A logical size is S device pixels tall.
        assert T.font(14).size == 14 * T.S

    def test_a_card_is_drawn_s_times_its_logical_size(self):
        img = _png(SC.render_position_card(POSITION))
        assert img.width == 520 * T.S
        assert img.height % T.S == 0

    def test_the_canvas_is_trimmed_to_what_was_drawn(self):
        """The position card used to be a fixed 480 tall with a third of it
        empty; it now ends where its last line ends. A card with a context
        line is taller than one without, by the line."""
        with_ctx = _png(SC.render_position_card(dict(POSITION, rsi=28, rsi_label="oversold")))
        without = _png(SC.render_position_card(POSITION))
        assert with_ctx.height > without.height
        assert without.height < 440 * T.S

    def test_the_palette_keeps_its_meanings_apart(self):
        """Green, red, grey and muted are claims the tests read by fill; the
        theme may move the values but never fold two meanings into one."""
        seen = {SC._GREEN, SC._RED, SC._GRAY, SC._MUTED, SC._CYAN, SC._WHITE, SC._DIM}
        assert len(seen) == 7
        assert SC._GREEN == T.UP and SC._RED == T.DOWN and SC._CYAN == T.ACCENT
        assert etf_card._GREEN == SC._GREEN and etf_card._MUTED == SC._MUTED


class TestTheSeamsStillHold:
    def test_draw_text_hands_imagedraw_the_string_and_the_fill(self):
        """`tests/png_text.py` spies on ImageDraw.text; the theme's Draw must
        pass through it with the same string and the same fill, or every
        honesty test on the cards would be reading a different surface."""
        rows = drawn(SC.render_position_card, POSITION)
        assert fill_of(rows, "SHORT") == SC._RED
        assert fill_of(rows, "PAPER") == SC._GRAY
        assert fill_of(rows, "+14.78%") == SC._GREEN
        assert fill_of(rows, "ENTRY") == SC._GRAY, "a tile's label keeps the label colour"
        unread = drawn(SC.render_position_card, dict(POSITION, now=None, pnl_pct=None, pnl_usd=None, net_pnl=None))
        assert fill_of(unread, "—") == SC._MUTED

    def test_the_stripe_is_still_the_top_edge_in_the_pnl_colour(self):
        img = _png(SC.render_position_card(POSITION))
        assert img.getpixel((img.width // 2, 1)) == SC._GREEN
        lost = _png(SC.render_position_card(dict(POSITION, pnl_pct=-3.1, pnl_usd=-44.0, net_pnl=-45.8)))
        assert lost.getpixel((lost.width // 2, 1)) == SC._RED

    def test_a_pill_tints_its_ground_and_colours_its_text(self):
        """A direction chip used to be a solid block with black text; the
        theme's pill is a tinted ground with the text in the colour, so the
        colour is read from the word and not from a slab."""
        img, draw = T.new_card(120, 40)
        w = draw.pill(10, 10, "SHORT", T.DOWN, T.font(10, bold=True), h=20)
        assert w > draw.textlength("SHORT", font=T.font(10, bold=True))
        px = img.getpixel((12 * T.S, 20 * T.S))
        assert px != T.DOWN and px != T.BG, "the ground is a tint, neither the colour nor bare canvas"
        assert px == T.blend(T.DOWN)

    @pytest.mark.parametrize("fn,args", [
        (SC.render_signal_card, ({"rank": 1, "symbol": "BTC", "direction": "LONG", "entry": 1,
                                  "stop_loss": 0.9, "tp1": 1.2, "confidence": None},)),
        (SC.render_close_card, ({"symbol": "BTC", "direction": "LONG", "entry": 1, "exit": 1.1,
                                 "pnl_usd": 2.0, "reason": "tp_hit"},)),
        (SC.render_scan_results_card, ([{"sym": "BTC", "dir": "LONG", "entry": 1, "sl": 0.9,
                                         "tp": 1.2, "rr": 2, "rsi": 50, "score": 70}],)),
        (SC.render_stats_card, ({"title": "T", "tiles": [{"label": "a", "value": "1"}]},)),
        (SC.render_share_card, ({"symbol": "BTC", "direction": "LONG", "pnl_pct": 1.0},)),
    ])
    def test_every_card_still_signs_off_with_the_wordmark(self, fn, args):
        # The share card draws its "Not investment advice." after the mark;
        # every other card ends on it. Either way it is among the last lines.
        rows = drawn(fn, *args)
        assert rows and any(t == "RUNECLAW" for t, _ in rows[-3:]), rows[-3:]
