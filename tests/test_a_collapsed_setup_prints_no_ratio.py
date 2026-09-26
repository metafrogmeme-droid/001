"""An ATR quantised on an absolute grid, and the ratio that cannot reveal it.

A live SUI card printed an entry, a stop and a target that were the same number
at the precision the card prints, under ``R:R 4.8``. The ratio is reward over
risk and both are the same multiple of one ATR, so the ATR CANCELS: the figure
a reader trusts most is the one structurally incapable of moving when the
setup collapses.

Two inputs produce such an ATR and the first is ordinary: the analyzer recorded
its ATR to six DECIMAL PLACES, so no asset priced below a cent could have one.
Driven here on the analyzer's own Wilder arithmetic rather than asserted.
"""

from __future__ import annotations

import ast
import inspect
import math
import textwrap

import numpy as np
import pytest

from bot.core.signal_levels import (
    ATR_SIG_DIGITS,
    atr_on_record,
    levels_separate,
    printed_rr,
    record_atr,
)

ENTRY = 1.171
SL_MULT, TP_MULT = 1.5, 7.2


def _levels(atr: float) -> tuple[float, float, float]:
    """The analyzer's own construction."""
    return ENTRY, ENTRY - SL_MULT * atr, ENTRY + TP_MULT * atr


def _wilder_atr(highs, lows, closes, period: int = 14) -> float:
    """The arithmetic analyzer.py runs, lifted verbatim for the drive."""
    highs, lows, closes = map(np.asarray, (highs, lows, closes))
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(abs(highs[1:] - closes[:-1]), abs(lows[1:] - closes[:-1])),
    )
    if len(tr) >= period:
        a = np.zeros(len(tr))
        a[period - 1] = np.mean(tr[:period])
        for i in range(period, len(tr)):
            a[i] = (a[i - 1] * (period - 1) + tr[i]) / period
        return float(a[-1])
    return float(np.mean(tr))


def _series(price: float, range_frac: float = 0.01, n: int = 60):
    closes = [price * (1 + 0.0005 * math.sin(i)) for i in range(n)]
    highs = [c * (1 + range_frac / 2) for c in closes]
    lows = [c * (1 - range_frac / 2) for c in closes]
    return highs, lows, closes


class TestTheRecordIsSignificantDigits:
    @pytest.mark.parametrize("price", [1.171, 0.0000112, 0.0000091, 63000.0])
    def test_a_healthy_one_percent_range_records_an_atr(self, price):
        """The whole defect: a sub-cent asset recorded 0.0 from a real range."""
        atr = _wilder_atr(*_series(price))
        assert atr > 0.0, "the drive's own fixture produced no ATR"
        assert record_atr(atr) > 0.0, (
            f"a 1% range on a ${price} asset recorded no ATR at all"
        )

    def test_the_old_absolute_grid_is_what_lost_them(self):
        """Pins the cause, so the fix cannot be read as arbitrary."""
        atr = _wilder_atr(*_series(0.0000112))
        assert round(atr, 6) == 0.0          # six DECIMAL PLACES
        assert record_atr(atr) == pytest.approx(atr, rel=1e-5)  # six SIGNIFICANT

    def test_nothing_above_a_cent_records_less_than_it_did(self):
        """Significant digits is never coarser than the old grid was."""
        atr = _wilder_atr(*_series(1.171))
        assert abs(record_atr(atr) - atr) <= abs(round(atr, 6) - atr)

    @pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf"), "x", None])
    def test_anything_that_is_not_a_measurement_keeps_the_absence_spelling(self, bad):
        # Every existing reader treats a recorded 0.0 as "no ATR on record",
        # so the record keeps that spelling rather than inventing a new one.
        assert record_atr(bad) == 0.0

    def test_the_digit_count_is_named_not_spelled_twice(self):
        assert ATR_SIG_DIGITS == 6


class TestTheReadingSeparatesAbsenceFromZero:
    @pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf"), "x", None])
    def test_an_unmeasured_atr_is_none(self, bad):
        assert atr_on_record(bad) is None

    def test_a_measured_atr_reads(self):
        assert atr_on_record(0.01171) == pytest.approx(0.01171)


class TestTheRatioCannotHideACollapse:
    def test_the_old_ratio_reads_the_same_at_every_scale(self):
        """Why the card's most reassuring figure never revealed this."""
        ratios = set()
        for atr in (0.0117, 1e-05, 1e-06):
            e, s, t = _levels(atr)
            ratios.add(round(abs(t - e) / abs(e - s), 2))
        assert len(ratios) == 1, "the fixture does not reproduce the defect"
        assert ratios == {4.8}

    def test_a_healthy_setup_still_prints_its_ratio(self):
        assert printed_rr(*_levels(0.0117)) == pytest.approx(4.8)

    @pytest.mark.parametrize("atr", [1e-05, 1e-06, 1e-09])
    def test_a_setup_the_card_cannot_tell_apart_prints_no_ratio(self, atr):
        e, s, t = _levels(atr)
        assert not levels_separate(e, s, t)
        assert printed_rr(e, s, t) is None

    def test_the_test_is_what_the_card_prints_not_a_guessed_floor(self):
        """A sub-cent asset's real setup separates, because `_fmt_price` does."""
        from bot.formatters.rich_cards import _fmt_price

        atr = 1.12e-07
        e, s, t = 0.0000112, 0.0000112 - 1.5 * atr, 0.0000112 + 7.2 * atr
        assert _fmt_price(s) != _fmt_price(e)
        assert levels_separate(e, s, t)
        assert printed_rr(e, s, t) is not None

    @pytest.mark.parametrize(
        "e,s,t",
        [(float("nan"), 1.0, 2.0), (1.0, float("inf"), 2.0), ("x", 1.0, 2.0)],
    )
    def test_unreadable_levels_print_no_ratio(self, e, s, t):
        assert printed_rr(e, s, t) is None


class TestTheCardsAskIt:
    def test_the_png_card_shows_a_dash_over_a_collapsed_setup(self):
        from bot.formatters.signal_card import signal_card_from_idea
        from bot.utils.models import Direction, TradeIdea
        from tests.png_text import capture_text

        def card(atr):
            e, s, t = _levels(atr)
            idea = TradeIdea(id="T", asset="SUI/USDT", direction=Direction.LONG,
                             entry_price=e, stop_loss=s, take_profit=t,
                             confidence=0.7, reasoning="d")
            idea.blended_confidence_raw = 0.7
            with capture_text() as drawn:
                signal_card_from_idea(idea, rank=1)
            return [text for text, _ in drawn]

        healthy, collapsed = card(0.0117), card(1e-05)
        assert "1:4.8" in healthy
        assert "1:4.8" not in collapsed, (
            "the card still claims a ratio over levels it cannot tell apart"
        )
        assert "—" in collapsed

    def test_the_gates_own_ratio_is_untouched(self):
        """`risk_reward_ratio` decides whether a trade may open; it is not
        a display, and narrowing it here would change what trades."""
        from bot.utils.models import Direction, TradeIdea

        e, s, t = _levels(1e-05)
        idea = TradeIdea(id="T", asset="SUI/USDT", direction=Direction.LONG,
                         entry_price=e, stop_loss=s, take_profit=t,
                         confidence=0.7, reasoning="d")
        assert idea.risk_reward_ratio == pytest.approx(4.8)


class TestTheAnalyzerAsksTheReading:
    """`dict.get` fires its default for an absent KEY, never for a 0.0."""

    def test_every_recorded_atr_read_asks_or_guards(self):
        """The rule is the PROPERTY, not one spelling.

        The first draft of this guard forbade `indicators.get("atr",
        <default>)` anywhere in the analyzer and accused FOUR correct sites --
        the SMC block, the zone detector, the strategy classifier and the POC
        read -- each of which already writes `if atr > 0:` on the next line,
        which IS reading a recorded 0.0 as absence. A checker with a blind
        spot manufactures exactly the accusation it exists to prevent. What is
        true of all five sites is that the figure is either put through
        `atr_on_record` or compared against zero before anything is built
        from it; the level builder was the one that did neither.
        """
        from bot.core.analyzer import Analyzer

        tree = ast.parse(textwrap.dedent(inspect.getsource(Analyzer)))
        bad = []
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if not _modelled_atr_default(node):
                    continue
                bad.append(f"{fn.name}: {ast.unparse(node)}")
        assert not bad, (
            "a site substitutes a MODELLED figure for an absent ATR without "
            "asking atr_on_record(), so a recorded 0.0 takes a different "
            f"path from an absent key: {bad}"
        )

    def test_the_rule_bites_on_planted_source(self):
        """The real tree passes, so the rule is measured where it is the
        only thing in play."""
        planted = textwrap.dedent('''
            class Analyzer:
                def analyze(self, indicators, entry):
                    atr = indicators.get("atr", entry * 0.02)
                    return entry - 1.5 * atr
        ''')
        tree = ast.parse(planted)
        assert [ast.unparse(n) for n in ast.walk(tree) if _modelled_atr_default(n)] == [
            "indicators.get('atr', entry * 0.02)"
        ]
        # ...and the four guarded sites, whose default IS the absence
        # spelling, are acquitted by the same rule.
        ok = ast.parse('x = indicators.get("atr", 0)\ny = indicators.get("atr", 0.0)')
        assert not [n for n in ast.walk(ok) if _modelled_atr_default(n)]

    def test_the_record_goes_through_record_atr(self):
        from bot.core.analyzer import Analyzer

        src = inspect.getsource(Analyzer)
        assert "round(float(atr), 6)" not in src, (
            "the absolute grid is back: a sub-cent asset records no ATR"
        )
        assert "record_atr(" in src


class TestTheMonitorCardAsksItToo:
    """The NEW SIGNAL text card built its own ratio inline."""

    def _body(self, atr):
        import types

        from bot.core.engine import RuneClawEngine
        from bot.core.proactive_monitor import ProactiveMonitor
        from bot.utils.models import Direction, TradeIdea

        e, s, t = _levels(atr)
        idea = TradeIdea(id="T", asset="SUI/USDT", direction=Direction.LONG,
                         entry_price=e, stop_loss=s, take_profit=t,
                         confidence=0.78, reasoning="scan")
        idea.blended_confidence_raw = 0.78
        eng = types.SimpleNamespace(
            _pending_ideas={}, _engine_idea_ids=set(),
            live_executor=types.SimpleNamespace(_last_close_data=None,
                                                _positions={}),
        )
        for name in ("_engine_pending_ids", "_register_engine_idea"):
            setattr(eng, name, types.MethodType(getattr(RuneClawEngine, name), eng))
        eng._register_engine_idea(idea)
        alerts = ProactiveMonitor(eng)._check_trade_signals()
        assert alerts, "the drive's own fixture produced no signal"
        return alerts[0].body

    def test_a_healthy_setup_keeps_its_ratio(self):
        assert "4.8" in self._body(0.0117)

    def test_a_collapsed_setup_prints_the_dash(self):
        body = self._body(1e-05)
        assert "4.8" not in body, body
        assert "—" in body


def _modelled_atr_default(node) -> bool:
    """`indicators.get("atr", <something that is not zero>)`.

    A default of 0/0.0 is the spelling every reader here documents as "no ATR
    on record", and those sites guard `> 0` on the next line. A default that
    MODELS one -- `entry * 0.02` -- is the site that must ask the reading, so
    that a recorded 0.0 takes the same path as an absent key rather than
    building `stop_loss == entry`.
    """
    if not (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) > 1
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "atr"):
        return False
    default = node.args[1]
    return not (isinstance(default, ast.Constant)
                and isinstance(default.value, (int, float))
                and not isinstance(default.value, bool)
                and default.value == 0)
