"""OVERALL TREND — a failed read is not a measured range.

`overall_trend_label` ended `return "Range / Mixed"` as the fall-through for
EVERY input it did not recognise, so four different facts printed one
sentence:

    "neutral"   a measured range                       (a reading)
    ""          the MTF block raised and wrote nothing (a failed read)
    None        the key was never on the payload       (an absent read)
    "nonsense"  a word the function cannot place       (not a reading)

`build_alpha_insight` writes `htf_trend` INSIDE a try whose `except` only
logs at debug, so the second of those is the ORDINARY shape of an MTF
failure — and the operator was shown

    📈 Chart analysis — Range / Mixed

the calmest verdict on the card, assembled from an analysis that crashed.
The PNG sibling was worse: `except Exception: label = "Range / Mixed"`
drawn in the accent colour under a heading reading OVERALL TREND, in an
image about a trade.

WHAT THIS SLICE DELIBERATELY DID NOT CHANGE, because driving it said there
was nothing to change: `_analyze_structure`'s starved default. It answers
`ranging / bos:False / choch:False` for a window whose swings it never
found — the same shape `chart-read-model.js` was cured of — and CLAUDE.md
recorded the verdict as reaching "signal_card.py, rich_cards.py, the
position card and the chart renderer's BOS marker". Driven, it reaches none
of them: `MTFResult` carries no `structure` field at all, the word never
leaves `_analyze_single_tf`'s per-TF dict, and every reader of the two
FLAGS treats False/0 as an abstention. `TestTheStarvedStructureAbstains`
pins that, so a later reader that starts treating `bos: False` as evidence
AGAINST a break fails here.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

from bot.core.alpha_card import TREND_UNREAD, format_alpha_card, overall_trend_label
from bot.core.multi_timeframe import MTFResult, _analyze_structure, _find_swings
from tests.source_scan import code_only

SIGNAL_CARD = pathlib.Path("bot/formatters/signal_card.py")
SRC = code_only(SIGNAL_CARD.read_text(encoding="utf-8"))

#: The five verdict words. Each asserts something about the market.
VERDICTS = ("Breakout Continuation", "Uptrend", "Breakdown Continuation",
            "Downtrend", "Possible Reversal Up", "Possible Reversal Down",
            "Range / Mixed")


class TestAReadingAndAFailedReadAreDifferentSentences:
    @pytest.mark.parametrize("trend,bos,choch,expect", [
        ("bullish", 1, 0, "Breakout Continuation"),
        ("bullish", 0, 0, "Uptrend"),
        ("bearish", -1, 0, "Breakdown Continuation"),
        ("bearish", 0, 0, "Downtrend"),
        ("neutral", 0, 1, "Possible Reversal Up"),
        ("neutral", 0, -1, "Possible Reversal Down"),
        ("neutral", 0, 0, "Range / Mixed"),
    ])
    def test_a_word_the_engine_sets_is_a_reading(self, trend, bos, choch, expect):
        assert overall_trend_label(trend, bos, choch) == expect

    @pytest.mark.parametrize("trend", ["", None, "nonsense", "   ", 42, [],
                                       "bull", "ranging"])
    def test_anything_else_says_it_read_nothing(self, trend):
        out = overall_trend_label(trend, 0, 0)
        assert out == TREND_UNREAD
        assert out not in VERDICTS, "an absence must not borrow a verdict word"

    def test_the_mtf_failure_shape_is_the_one_that_mattered(self):
        """`build_alpha_insight` sets htf_trend/bos_dir/choch_dir together
        inside one try; a raise leaves the key absent, and `.get(k, "")`
        hands this function the empty string."""
        assert overall_trend_label("", 0, 0) == TREND_UNREAD

    @pytest.mark.parametrize("case", ["upper", "pad"])
    def test_a_real_word_still_reads_through_case_and_padding(self, case):
        t = "NEUTRAL" if case == "upper" else "  neutral  "
        assert overall_trend_label(t, 0, 0) == "Range / Mixed"


class TestTheDirectionAbstainsRatherThanRaising:
    """The break direction REFINES a trend that was read; it does not assert
    one. An unreadable direction must not delete the trend beside it — which
    is what `int(data.get("bos_dir", 0))` raising into an `except` did."""

    @pytest.mark.parametrize("junk", [None, "x", float("nan"), [], {}, True,
                                      float("inf")])
    def test_a_junk_direction_leaves_the_trend_readable(self, junk):
        assert overall_trend_label("bullish", junk, 0) in ("Uptrend",
                                                           "Breakout Continuation")
        assert overall_trend_label("bullish", junk, 0) != TREND_UNREAD

    def test_a_junk_direction_abstains_rather_than_asserting_a_break(self):
        """`inf` and `True` are truthy; neither is a measured break."""
        assert overall_trend_label("bullish", float("nan"), 0) == "Uptrend"
        assert overall_trend_label("bullish", True, 0) == "Uptrend"
        assert overall_trend_label("bullish", "1", 0) == "Uptrend"

    def test_a_real_direction_still_refines(self):
        assert overall_trend_label("bullish", 1, 0) == "Breakout Continuation"
        assert overall_trend_label("bullish", 2.5, 0) == "Breakout Continuation"

    @pytest.mark.parametrize("junk", [None, "x", float("nan"), [], True])
    def test_the_FLIP_direction_is_guarded_too(self, junk):
        """The round's survivor: every junk-direction case drove `bos_dir`
        with a bullish trend, so guarding only the break was indistinguishable
        from guarding both. A CHoCH is only reachable on a neutral trend."""
        assert overall_trend_label("neutral", 0, junk) == "Range / Mixed"

    def test_a_real_flip_still_refines(self):
        assert overall_trend_label("neutral", 0, 1) == "Possible Reversal Up"
        assert overall_trend_label("neutral", 0, -1) == "Possible Reversal Down"


class TestTheCardPrintsIt:
    def _card(self, **over):
        d = {"symbol": "BTC/USDT:USDT", "price": 63105.0,
             "change_24h_pct": 1.23}
        d.update(over)
        return format_alpha_card(d)

    def test_a_crashed_mtf_block_does_not_print_a_verdict(self):
        """No htf_trend key at all — exactly what the except leaves behind."""
        card = self._card()
        assert TREND_UNREAD in card
        for v in VERDICTS:
            assert v not in card, f"{v!r} asserted over an MTF read that failed"

    def test_a_read_trend_still_prints_its_verdict(self):
        card = self._card(htf_trend="bullish", bos_dir=1, choch_dir=0)
        assert "Breakout Continuation" in card
        assert TREND_UNREAD not in card

    @pytest.mark.parametrize("junk", [None, "x", [], float("nan")])
    def test_a_junk_direction_does_not_take_the_card_down(self, junk):
        """`int(d.get("bos_dir", 0))` sat in NO try here, so a junk direction
        raised out of the whole renderer. The round's survivor: every earlier
        fixture carried a real 0, which `int()` reads happily."""
        card = self._card(htf_trend="bullish", bos_dir=junk, choch_dir=junk)
        assert "Uptrend" in card
        assert TREND_UNREAD not in card


class TestThePngSaysItToo:
    """The PNG is the surface the `except` was on, and colour is a claim."""

    @staticmethod
    def _pixels(data: dict):
        import io

        from PIL import Image

        from bot.formatters.signal_card import render_alpha_card
        png = render_alpha_card(data)
        assert png.startswith(b"\x89PNG")
        return Image.open(io.BytesIO(png)).convert("RGB").getdata()

    BASE = {"symbol": "BTC/USDT:USDT", "price": 63105.0, "change_24h_pct": 1.2,
            "bos_dir": 0, "choch_dir": 0}

    def test_an_unread_trend_is_drawn_muted_and_a_read_one_is_not(self):
        """The two payloads differ in ONE key, and the unread word is the
        LONGER string — "Trend not read" against "Uptrend". So if the ternary
        went away and both wore the accent, the unread card would carry MORE
        accent pixels, not fewer: the DIRECTION of this assertion is what
        makes it a kill rather than a coincidence about string length."""
        from bot.formatters import signal_card as SC
        assert len(TREND_UNREAD) > len("Uptrend")
        read = dict(self.BASE, htf_trend="bullish")      # -> "Uptrend"
        unread = dict(self.BASE, htf_trend="")           # -> TREND_UNREAD

        def cyan(d):
            return sum(1 for q in self._pixels(d) if q == SC._CYAN)

        def grey(d):
            return sum(1 for q in self._pixels(d) if q == SC._GRAY)

        assert cyan(unread) < cyan(read), (
            "the unread headline is wearing the accent every measured verdict "
            "on this card wears")
        assert grey(unread) > grey(read), "and it is not simply unpainted"

    def test_a_junk_direction_renders_byte_for_byte_as_an_abstention(self):
        """The PNG's own `int(data.get("bos_dir", 0))`. Every earlier fixture
        carried a real 0 — which `int()` reads happily — so guarding it was
        indistinguishable from not. A junk direction abstains at 0, so these
        two cards must be the SAME IMAGE; with the `int()` back, the left one
        raises into the except and prints the unread word instead."""
        from bot.formatters.signal_card import render_alpha_card
        junk = dict(self.BASE, htf_trend="bullish", bos_dir=None,
                    choch_dir="x")
        zero = dict(self.BASE, htf_trend="bullish", bos_dir=0, choch_dir=0)
        assert render_alpha_card(junk) == render_alpha_card(zero)

    def test_the_renderer_holds_no_verdict_literal_on_a_failure_path(self):
        """The `except` used to read `label = "Range / Mixed"`.

        The first draft opened the slice at the `# -- Trend badge` COMMENT and
        `code_only` had already blanked it -- this file's own advice arriving
        from the direction it does not warn about: not a comment that matched,
        a comment that was GONE. Both anchors are code."""
        start = SRC.index('label = str(data.get("trend_label")')
        end = SRC.index('"OVERALL TREND"', start)
        block = SRC[start:end]
        assert "Range / Mixed" not in block, (
            "a failed read is wearing a verdict word again")
        assert "_TREND_UNREAD" in block

    def test_the_two_modules_hold_one_word(self):
        from bot.formatters import signal_card as SC
        assert SC._TREND_UNREAD == TREND_UNREAD


class TestTheStarvedStructureAbstains:
    """CLAUDE.md filed `_analyze_structure`'s starved default as reaching the
    cards. Driven, it reaches no renderer — and every reader of its two flags
    treats them as an abstention. This pins that, because the day one stops
    is the day "no break detected" starts being said about a window nobody
    could measure."""

    @staticmethod
    def _starved():
        c = np.array([100.0 + i for i in range(40)], dtype=float)
        return _analyze_structure(c * 1.002, c * 0.998, c)

    @staticmethod
    def _one_swing_each_side():
        """A window with exactly ONE fractal swing per side — the case the
        `< 2` floor exists for. The round's survivor: on the ramp the count is
        ZERO, so `< 2` and `< 1` agree, and a floor lowered to `< 1` here
        reaches `sh[-2]` and raises IndexError."""
        c = np.array([100 + (i if i <= 10 else 20 - i if i <= 25 else i - 30)
                      for i in range(40)], dtype=float)
        return c * 1.003, c * 0.997, c

    def test_the_floor_matches_what_the_body_needs(self):
        hi, lo, c = self._one_swing_each_side()
        sw = _find_swings(hi, lo, 5)
        assert len(sw["swing_highs"]) == 1 and len(sw["swing_lows"]) == 1
        st = _analyze_structure(hi, lo, c)   # must not raise on sh[-2]
        assert st["structure"] == "ranging" and st["bias"] == 0.0

    def test_the_fractal_really_does_starve_on_a_ramp(self):
        c = np.array([100.0 + i for i in range(40)], dtype=float)
        sw = _find_swings(c * 1.002, c * 0.998, 5)
        assert not sw["swing_highs"] and not sw["swing_lows"]

    def test_the_default_is_returned(self):
        st = self._starved()
        assert st == {"structure": "ranging", "bos": False, "choch": False,
                      "bias": 0.0, "bos_dir": 0, "choch_dir": 0}

    def test_every_confluence_voter_abstains(self):
        """DRIVEN through the real vote builder rather than restating its
        conditions: a guard that re-writes the `if` it is guarding agrees with
        every mutation of that `if`."""
        from bot.core.multi_timeframe import MTFConfluence
        # `confidence` must be non-zero or `to_confluence_votes` returns
        # empty for EVERY input and this assertion passes for a reason
        # unrelated to the rule — which is what the first draft did.
        starved = MTFResult(structure_bias=0.0, bos_detected=False, bos_dir=0,
                            choch_detected=False, choch_dir=0, confidence=0.8)
        votes, weights, labels = MTFConfluence.to_confluence_votes(starved)
        for name in ("mtf_structure", "mtf_bos", "mtf_choch"):
            assert name not in labels, (
                f"{name} voted on a structure nobody could measure")

    def test_the_same_voters_DO_fire_on_a_real_reading(self):
        """Or the test above would pass against a builder that votes never."""
        from bot.core.multi_timeframe import MTFConfluence
        read = MTFResult(structure_bias=0.7, bos_detected=True, bos_dir=1,
                         choch_detected=True, choch_dir=-1, confidence=0.8)
        _v, _w, labels = MTFConfluence.to_confluence_votes(read)
        for name in ("mtf_structure", "mtf_bos", "mtf_choch"):
            assert name in labels

    def test_the_structure_word_is_on_no_result_object(self):
        """It never leaves `_analyze_single_tf`'s per-TF dict — which is why
        no card can print it, and why the filed claim was wrong."""
        assert "structure" not in MTFResult.model_fields
        for f in ("structure_bias", "bos_detected", "bos_dir",
                  "choch_detected", "choch_dir"):
            assert f in MTFResult.model_fields

    def test_the_narrative_claims_nothing_it_did_not_measure(self):
        from bot.core.multi_timeframe import MTFConfluence as _A
        r = MTFResult(bos_detected=False, choch_detected=False,
                      htf_trend="neutral", aligned_timeframes=["1h"])
        text = _A._build_narrative(r, {"1h": {}})
        assert "Break of structure" not in text
        assert "Change of character" not in text
        # and it DOES say so once the flags are real readings
        r2 = MTFResult(bos_detected=True, bos_dir=1, choch_detected=True,
                       choch_dir=-1, htf_trend="bullish",
                       aligned_timeframes=["1h"])
        said = _A._build_narrative(r2, {"1h": {}})
        assert "Break of structure" in said
        assert "Change of character" in said
