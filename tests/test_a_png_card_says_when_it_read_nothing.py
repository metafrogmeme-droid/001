"""A PNG is a surface no guard here could read as text.

Five cards printed a MEASURED ZERO for a figure nobody read, and every suite
stayed green for the life of each of them, because the only instrument that
existed for a PNG counts PIXELS
(``test_the_trend_headline_says_when_it_read_nothing``) — the right tool for
a COLOUR claim, and unable to answer *what did it say*. That file's own
fixture is the second half of the reason: it carries
``change_24h_pct: 1.2``, a readable value, and **a fixture where every field
is readable cannot tell a coerced figure from an honest one** — this
repository's recorded lesson about the RWA aggregate, arriving in the test
written for this very card. A source scan could not answer it either: the
defect is not a spelling, it is which quantity a figure holds.

``tests/png_text.py`` is the seam — every string a card draws, with its fill —
and what it found, from a bare ``{}``:

    render_signal_card      CONFIDENCE 0%  ·  SCORE 0%     in the ACCENT colour
    render_alpha_card       +0.00% 24h                     in GREEN
    render_scan_grid_card   +0.0% and a GREEN DIRECTION DOT
    render_patterns_card    +0.0% and a ● that reads as flat
    render_close_card       "LONG | HOLD" over an EMPTY STRING

Each is a row of the table CLAUDE.md tabulates. ``up = chg >= 0`` is
*unreadable WON* verbatim; ``.get("confidence", 0)`` is *absent field is
zero*; the grid footer's ``up + down`` over a set holding unread rows is *a
partial total, printed as whole*; and the HOLD cell is ``_status_lines``'
recorded defect — a section announcing itself and then saying nothing — in an
image.

TWO THINGS THE DRIVE FOUND THAT NO SCAN WOULD HAVE.

``SCORE`` is a second LABEL for ``confidence``, not a second field: the
renderer reads no ``score`` key and its docstring lists only ``confidence``.
With no margin, no TP2 and no RSI both cells are reached, so ONE reading was
drawn twice under two names, side by side — which tells a reader they are two
readings that agree. The comment I first wrote there claimed the branch was
unreachable when the confidence cell had been drawn; rendering the card said
otherwise, which is *a comment claiming a check the code does not make*, from
the author's side.

And the confidence has a FOURTH site — the auto-summary's ``Score
{confidence:.0f}%`` — which a scan for the three CELL sites misses entirely.
It was found by the card RAISING on ``None.__format__``.

WHAT IS NOT CHANGED, because a measured zero is a reading: a share card with
``pnl_pct: 0.0`` still prints ``+0.00%`` in green, an alpha card with
``change_24h_pct: 0.0`` still prints ``+0.00% 24h`` in green, and a signal
card with ``confidence: 0.0`` still prints ``0%``. ``pct_on_record`` is
deliberately NOT ``price_on_record``: that one refuses ``<= 0`` because a
price of zero is the shape of a level nobody stated, and for a percent both
zero and the negatives are real measurements.
"""

from __future__ import annotations

import pathlib

import pytest

from bot.core.position_telemetry import pct_on_record
from bot.formatters import signal_card as SC
from tests.png_text import drawn, fill_of, strings

_SRC = pathlib.Path("bot/formatters/signal_card.py")


class TestTheReading:
    """`pct_on_record` is a percent's reading, not a price's."""

    @pytest.mark.parametrize("raw", [None, "", "x", float("nan"),
                                     float("inf"), float("-inf"), True, False,
                                     object()])
    def test_what_is_not_a_percent(self, raw):
        assert pct_on_record(raw) is None

    @pytest.mark.parametrize("raw,want", [(0.0, 0.0), (0, 0.0), (-5.2, -5.2),
                                          (1.2, 1.2), ("3.5", 3.5)])
    def test_what_is(self, raw, want):
        """0.0 is a MEASURED flat day and -5.2 a measured fall. Refusing
        either would replace a reading with an absence, which is why this is
        not `price_on_record` with a different name."""
        assert pct_on_record(raw) == want

    def test_it_is_not_price_on_record(self):
        from bot.core.position_telemetry import price_on_record
        assert price_on_record(0.0) is None and pct_on_record(0.0) == 0.0
        assert price_on_record(-5.0) is None and pct_on_record(-5.0) == -5.0


class TestTheSeamItself:
    """A guard that reports success over the defect it exists to find is the
    failure this repo is about, so the instrument is driven first."""

    def test_it_reads_the_strings_a_card_drew(self):
        rows = drawn(SC.render_alpha_card,
                     {"symbol": "BTC/USDT:USDT", "price": 63105.0,
                      "change_24h_pct": -1.2})
        assert ("$63,105.00", SC._WHITE) in rows
        assert ("-1.20% 24h", SC._RED) in rows

    def test_it_puts_the_patch_back(self):
        """A spy left installed would follow the next test into its own
        renderer. Read the attribute, not a flag."""
        from PIL import ImageDraw
        before = ImageDraw.ImageDraw.text
        drawn(SC.render_alpha_card, {})
        assert ImageDraw.ImageDraw.text is before

    def test_fill_of_refuses_a_string_the_card_never_drew(self):
        """An assertion about the colour of something that was never drawn is
        an assertion that cannot fail."""
        rows = drawn(SC.render_alpha_card, {})
        with pytest.raises(AssertionError):
            fill_of(rows, "a string no card draws")

    def test_fill_of_matches_the_whole_string_and_not_a_part(self):
        """A card draws its LABELS and its VALUES through the same call, and
        `"0%"` is inside `"10%"`. A needle that is merely a SUBSTRING of a
        drawn string must not resolve, or an assertion about one cell can be
        satisfied by another. The first draft's only decoy was a string that
        appears nowhere, which a substring match refuses too — so it measured
        nothing about the comparison it names."""
        rows = drawn(SC.render_signal_card, {"confidence": 0.72})
        assert fill_of(rows, "CONFIDENCE") == SC._GRAY
        with pytest.raises(AssertionError):
            fill_of(rows, "CONF")


class TestTheAlphaCardsChange:
    READ = {"symbol": "BTC/USDT:USDT", "price": 63105.0}

    def test_an_unread_change_is_not_a_flat_day_and_not_green(self):
        rows = drawn(SC.render_alpha_card, dict(self.READ))
        drew = [t for t, _ in rows]
        assert SC.CHANGE_UNREAD in drew
        assert not any(t.startswith("+0.00%") for t in drew), (
            "an unread 24h change rendered as a measured break-even")
        assert fill_of(rows, SC.CHANGE_UNREAD) == SC._GRAY, (
            "colour is a claim: green says the asset rose")

    def test_a_measured_flat_day_still_prints_and_is_still_green(self):
        """The direction that matters: a READ zero is a reading, and a fix
        that hid it would have replaced one wrong answer with another."""
        rows = drawn(SC.render_alpha_card, dict(self.READ, change_24h_pct=0.0))
        assert fill_of(rows, "+0.00% 24h") == SC._GREEN

    def test_a_measured_fall_is_red(self):
        rows = drawn(SC.render_alpha_card, dict(self.READ, change_24h_pct=-1.2))
        assert fill_of(rows, "-1.20% 24h") == SC._RED

    def test_the_producer_does_not_coerce_before_the_card_sees_it(self):
        """Fixing the renderer and leaving `float(tk.get("percentage") or 0)`
        in `build_alpha_insight` would leave the card with nothing to read:
        ccxt reports `percentage: None` for a market whose venue publishes no
        24h change, which `app/lib/tickers.js` already handles honestly."""
        import bot.core.alpha_card as AC
        src = AC.__file__ and pathlib.Path(AC.__file__).read_text()
        assert 'float(tk.get("percentage") or 0)' not in src
        assert "pct_on_record(tk.get(\"percentage\"))" in src

    def test_the_html_sibling_says_it_too(self):
        """Two cards, one producer: curing the PNG and leaving the HTML one
        would be `fixing two left the third` inside a single subject."""
        from bot.core.alpha_card import format_alpha_card
        unread = format_alpha_card(dict(self.READ))
        read = format_alpha_card(dict(self.READ, change_24h_pct=-1.2))
        assert "24h unread" in unread and "+0.00% 24h" not in unread
        assert "(-1.20% 24h)" in read


class TestTheScanGridRow:
    def test_an_unread_change_paints_no_direction(self):
        """`col` paints the DOT as well as the figure, so a coerced change
        made a green dot, a green sparkline and a measured `+0.0%`."""
        rows = drawn(SC.render_scan_grid_card,
                     {"title": "T", "grid": [{"sym": "BTC", "price": 63105.0,
                                              "change_pct": None}]})
        assert fill_of(rows, SC.CHANGE_UNREAD) == SC._GRAY
        assert not any(t.startswith("+0.0%") for t, _ in rows)

    def test_a_measured_flat_is_still_a_reading(self):
        rows = drawn(SC.render_scan_grid_card,
                     {"title": "T", "grid": [{"sym": "BTC", "price": 63105.0,
                                              "change_pct": 0.0}]})
        assert fill_of(rows, "+0.0%") == SC._GREEN

    #: An EMPTY grid returns b"" before the footer is reached, so a fixture
    #: with no rows measures nothing about the footer -- the first draft of
    #: these two used one and drew zero strings.
    _ROW = {"sym": "BTC", "price": 63105.0, "change_pct": 1.0}

    def test_the_footer_counts_a_third_bucket(self):
        """`up + down` over a set holding unread rows is a partial total
        printed as the whole scan."""
        drew = strings(SC.render_scan_grid_card,
                       {"title": "T", "grid": [dict(self._ROW)],
                        "summary": {"up": 4, "down": 2, "unread": 3}})
        assert "● 3 unread" in drew and "▲ 4 up" in drew

    def test_a_clean_scan_carries_no_unread_row(self):
        """A permanent `0 unread` on every healthy card is the row that
        trains a reader to stop reading the line."""
        drew = strings(SC.render_scan_grid_card,
                       {"title": "T", "grid": [dict(self._ROW)],
                        "summary": {"up": 4, "down": 2, "unread": 0}})
        assert not any("unread" in t for t in drew)

    @pytest.mark.parametrize("changes,want", [
        ([1.2, -3.0, None, 0.0], {"up": 1, "down": 1, "unread": 1}),
        ([None, None], {"up": 0, "down": 0, "unread": 2}),
        (["x", True, float("nan")], {"up": 0, "down": 0, "unread": 3}),
        ([], {"up": 0, "down": 0, "unread": 0}),
        (None, {"up": 0, "down": 0, "unread": 0}),
    ])
    def test_the_counting_is_a_seam(self, changes, want):
        """The producer's arithmetic lived inside a 400-line async handler,
        where `up + down` was a partial total and nothing could reach it to
        say so. A MEASURED flat is in neither direction bucket and is not
        unread either, which is why three counts do not have to sum to the
        input length."""
        got = SC.breadth_counts(changes)
        assert {k: got[k] for k in want} == want

    def test_the_reading_is_what_makes_the_explicit_form_equivalent(self):
        """`(c or 0) > 0` in the seam is an EQUIVALENT MUTANT and survived a
        whole round: `read` has already been through `pct_on_record`, so it
        holds `None` or a float and both spellings answer identically for
        every one. The explicit form is kept because the terse one is
        literally a row of the shapes table and would read to the next reader
        as the defect — so the PROPERTY that makes them equivalent is driven
        here instead of the spelling being pinned. The day `pct_on_record`
        starts handing back something else, this fails rather than the count
        quietly starting to differ."""
        from bot.core.position_telemetry import pct_on_record as P
        for raw in [None, 0, 0.0, -1.0, 1.0, "x", "", True, float("nan"),
                    float("inf"), [], {}]:
            got = P(raw)
            assert got is None or isinstance(got, float), repr(got)
            assert not isinstance(got, bool)

    def test_the_producer_asks_the_seam_rather_than_counting(self):
        """A second copy of the count is a second answer about how many
        symbols rose."""
        import bot.skills.scan_commands as SCMD
        src = pathlib.Path(SCMD.__file__).read_text()
        assert "breadth_counts(" in src
        assert "sum(1 for s in signals" not in src, (
            "the handler kept its own two-bucket count")

    def test_both_producers_hand_the_card_a_reading(self):
        import bot.skills.scan_commands as SCMD
        src = pathlib.Path(SCMD.__file__).read_text()
        assert 'getattr(s, "change_pct_24h", 0) or 0' not in src, (
            "the producer coerced before the renderer could see it")
        assert 's["change_pct"],' not in src


class TestThePatternsCard:
    def test_unread_flat_and_fallen_are_three_different_marks(self):
        def mark(chg):
            rows = drawn(SC.render_patterns_card,
                         [{"symbol": "BTC", "price": 63105.0, "chg": chg,
                           "rsi": 55}])
            return rows[1][0]

        assert mark(None) == "?", "a ● says flat; nobody looked"
        assert mark(0.0) == "●"
        assert mark(-3.0) == "▼"

    def test_the_unread_figure_is_words(self):
        drew = strings(SC.render_patterns_card,
                       [{"symbol": "BTC", "price": 63105.0, "chg": None}])
        assert SC.CHANGE_UNREAD in drew and "+0.0%" not in drew


class TestTheSignalCardsConfidence:
    def test_an_absent_confidence_is_a_dash_and_not_a_verdict(self):
        """As a CONFIDENCE, `0%` is a verdict — the engine has none in this
        setup — drawn in the colour every measured figure on this card wears,
        while every PRICE beside it abstains through `_fmt`."""
        rows = drawn(SC.render_signal_card, {})
        drew = [t for t, _ in rows]
        assert "CONFIDENCE" in drew and "0%" not in drew
        i = drew.index("CONFIDENCE")
        assert rows[i + 1] == ("—", SC._GRAY)

    def test_a_measured_zero_confidence_still_prints(self):
        rows = drawn(SC.render_signal_card, {"confidence": 0.0})
        assert fill_of(rows, "0%") == SC._CYAN

    def test_a_none_confidence_no_longer_takes_the_card_down(self):
        """`if confidence <= 1` raised on an explicit unread key, so a
        producer that was honest about not reading it lost the whole card."""
        assert SC.render_signal_card({"confidence": None}).startswith(b"\x89PNG")

    @pytest.mark.parametrize("data", [
        {}, {"margin_usd": 50}, {"rsi": 61}, {"tp2": 7.0},
        {"margin_usd": 50, "rsi": 61}, {"rsi": 61, "confidence": 0.72},
    ])
    def test_one_reading_is_never_drawn_under_two_labels(self, data):
        """SCORE is a second LABEL for `confidence`, not a second field: this
        renderer reads no `score` key. Both cells were reachable at once, so
        one figure appeared twice side by side."""
        drew = strings(SC.render_signal_card, data)
        assert not ("CONFIDENCE" in drew and "SCORE" in drew)
        assert drew.count("CONFIDENCE") <= 1

    def test_the_fourth_site_omits_rather_than_losing_the_line(self):
        """The auto-summary is the site a scan for the three CELLS misses.
        The bias and the RSI beside it are real readings, so the score is
        omitted rather than the whole line."""
        line = [t for t in strings(SC.render_signal_card, {"rsi": 61})
                if "bias" in t]
        assert line == ["LONG bias | RSI 61.0"]
        with_score = [t for t in strings(SC.render_signal_card,
                                         {"rsi": 61, "confidence": 0.72})
                      if "bias" in t]
        assert with_score == ["LONG bias | RSI 61.0 | Score 72%"]


class TestTheCloseCardsHold:
    def test_an_absent_hold_is_not_a_label_over_nothing(self):
        rows = drawn(SC.render_close_card, {})
        labels = [t for t, _ in rows]
        i = labels.index("LONG | HOLD")
        assert rows[i + 1][0] == "unread", (
            "the label announced itself and the cell said nothing")

    def test_a_read_hold_is_printed(self):
        assert "2.4h" in strings(SC.render_close_card, {"hold_time": "2.4h"})


class TestTheShareCard:
    """The one card that LEAVES the product. `/share-card` validates and 400s
    a missing or non-finite figure, so nothing reaches this today — it is
    guarded at the BOUNDARY rather than left to the route to keep
    remembering, which is the `_fmt_price(None)` rule."""

    def test_an_unreadable_return_is_not_a_96px_break_even_in_green(self):
        rows = drawn(SC.render_share_card, {"symbol": "BTC", "direction": "LONG"})
        drew = [t for t, _ in rows]
        assert "+0.00%" not in drew
        assert fill_of(rows, "return unread") == SC._GRAY

    def test_a_measured_flat_close_still_prints(self):
        rows = drawn(SC.render_share_card,
                     {"symbol": "BTC", "direction": "LONG", "pnl_pct": 0.0})
        assert fill_of(rows, "+0.00%") == SC._GREEN

    def test_the_privacy_contract_is_untouched(self):
        """Its docstring forbids a dollar figure, a size, a margin and an
        entry price. The reading added here takes `pnl_pct` and nothing
        else."""
        drew = strings(SC.render_share_card,
                       {"symbol": "BTC", "direction": "LONG", "pnl_pct": 12.5,
                        "pnl_usd": 4242.42, "size_usd": 9999.0,
                        "margin_usd": 500.0, "entry": 63105.0})
        blob = " ".join(drew)
        for leaked in ("4242", "9999", "500", "63105", "$"):
            assert leaked not in blob, f"{leaked!r} reached the share card"


class TestOneWordForOneAbsence:
    def test_three_renderers_share_it(self):
        """A second copy of the sentence is a second answer about what an
        unread change is."""
        src = _SRC.read_text()
        assert src.count('CHANGE_UNREAD = "') == 1
        # One definition and one read per renderer: alpha, scan grid,
        # patterns. Three renderers wording one absence three ways is the
        # thing the constant exists to prevent.
        assert src.count("CHANGE_UNREAD") == 4
