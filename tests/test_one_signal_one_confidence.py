"""One signal, one confidence — on the card, its caption, and the check line.

An idea carries two confidences once calibration is on: ``blended_confidence_raw``
(the analyzer's blend, the scale every floor here is defined on) and
``confidence`` (what the curve left on the field). Five surfaces printed them
under one word with five hand-written fallbacks, and the sharpest instance is
ONE MESSAGE: `_send_idea_with_door` builds the PNG and its own caption in the
same function, so a SUI signal went out as a caption reading ``Conf 70%`` over
an image whose CONFIDENCE cell read ``31%``.

Driven, not scanned: the card is rendered and its text read back with
`tests/png_text.capture_text`, and the door is driven with a stand-in self so
the caption is the one the handler really composes.
"""

from __future__ import annotations

import asyncio
import pathlib
import types

import pytest

from bot.core.signal_confidence import (
    STAMP_TEXT,
    UNREAD_DASH,
    ConfidenceReading,
    displayed_confidence,
)
from bot.utils.models import Direction, TradeIdea
from tests.png_text import capture_text

# The live figures: SUI at $1.171, blend 0.70, the curve's output 0.31.
BLEND = 0.70
CALIBRATED = 0.31


def _idea(**kw) -> TradeIdea:
    d = dict(
        id="T-1",
        asset="SUI/USDT",
        direction=Direction.LONG,
        entry_price=1.171,
        stop_loss=1.1535,
        take_profit=1.2552,
        confidence=CALIBRATED,
        reasoning="drive",
    )
    d.update(kw)
    return TradeIdea(**d)


def _analyzer_idea(**kw) -> TradeIdea:
    idea = _idea(**kw)
    idea.blended_confidence_raw = BLEND
    return idea


def _drawn(idea) -> list[str]:
    from bot.formatters.signal_card import signal_card_from_idea

    with capture_text() as drawn:
        signal_card_from_idea(idea, rank=1)
    return [text for text, _fill in drawn]


class TestTheReading:
    def test_the_blend_wins_because_it_is_the_scale_the_gates_use(self):
        r = displayed_confidence(_analyzer_idea())
        assert r == ConfidenceReading(BLEND, "blend")
        assert r.measured and r.pct() == "70%"

    def test_a_bool_blend_is_a_flag_not_a_confidence(self):
        # `float(True)` is 1.0 — the value that clears every floor and prints
        # as 100%. Two of the five fallbacks did exactly that.
        idea = _idea()
        idea.blended_confidence_raw = True
        assert displayed_confidence(idea).value != 1.0

    def test_the_unset_blend_is_absent_not_a_measured_zero(self):
        idea = _idea()
        idea.blended_confidence_raw = 0.0
        r = displayed_confidence(idea)
        assert r.basis != "blend"

    def test_a_manual_stamp_is_not_a_measurement(self):
        r = displayed_confidence(_idea(confidence=1.0, source="manual"))
        assert r == ConfidenceReading(None, "stamp")
        assert not r.measured
        assert r.pct() == STAMP_TEXT

    def test_a_producer_that_measured_its_own_score_is_read(self):
        # A scan row / drift re-offer sets no blend; its score is its own.
        r = displayed_confidence(_idea(confidence=0.55))
        assert r == ConfidenceReading(0.55, "own")

    def test_nothing_readable_is_unread_and_never_raises(self):
        r = displayed_confidence(object())
        assert r == ConfidenceReading(None, "unread")
        assert r.pct() == UNREAD_DASH

    def test_a_stamp_and_an_unread_field_get_different_words(self):
        # Different facts: a figure nobody measured, and a field nobody read.
        assert STAMP_TEXT != UNREAD_DASH


class TestOneMessage:
    """The caption and the picture it labels cannot disagree."""

    def _door(self, idea):
        """Drive the real `_send_idea_with_door` with a stand-in self."""
        from bot.skills.telegram_handler import TelegramHandler

        sent: dict = {}

        async def _send_photo(update, png, cap, reply_markup=None):
            sent["caption"] = cap
            return True

        async def _send(update, text, **kw):
            sent["text"] = text

        host = types.SimpleNamespace(
            _lang=lambda u: "en",
            _send_photo=_send_photo,
            _send=_send,
        )
        update = types.SimpleNamespace(
            effective_user=types.SimpleNamespace(id=4242)
        )
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            TelegramHandler._send_idea_with_door(host, update, "ok", idea)
        )
        return sent

    def test_the_caption_and_the_card_show_the_same_figure(self):
        idea = _analyzer_idea()
        caption = self._door(idea)["caption"]
        drawn = _drawn(idea)

        assert "Conf 70%" in caption, caption
        # The cell the image draws under CONFIDENCE, and the summary's Score.
        assert "70%" in drawn
        # The defect: the calibrated figure on one of the two surfaces.
        assert "31%" not in caption
        assert "31%" not in drawn

    def test_a_hand_typed_ticket_shows_no_fabricated_certainty(self):
        idea = _idea(confidence=1.0, source="manual")
        caption = self._door(idea)["caption"]
        drawn = _drawn(idea)

        assert STAMP_TEXT in caption, caption
        assert "100%" not in caption
        assert "100%" not in drawn
        # The cell abstains rather than printing the stamp.
        assert UNREAD_DASH in drawn
        # And the summary makes no Score claim at all.
        assert not [t for t in drawn if "Score" in t]


class TestTheCheckLine:
    """The line quotes the figure the comparison used."""

    def test_with_calibration_on_the_gate_reads_the_blend(self):
        from unittest.mock import patch

        from bot.risk.confidence_floor import (
            clears_confidence_floor,
            confidence_for_floor,
        )

        idea = _analyzer_idea()
        with patch("bot.risk.confidence_floor.CONFIG") as m:
            m.analyzer.confidence_calibration_enabled = True
            m.risk.min_confidence = 0.60
            m.risk.per_strategy_confidence_floor_enabled = False
            # The figure the comparison uses is the blend, so the line that
            # quotes it cannot read "0.31 OK" against a 0.60 minimum.
            assert confidence_for_floor(idea) == pytest.approx(BLEND)
            assert clears_confidence_floor(idea)

    def test_with_calibration_off_the_field_is_already_the_raw_figure(self):
        # The shipped default: nothing moved `confidence`, so the gate's
        # reading is that field and the line quotes it.
        from bot.risk.confidence_floor import confidence_for_floor

        assert confidence_for_floor(_idea(confidence=0.64)) == pytest.approx(0.64)

    def test_the_engine_prints_the_figure_it_compared(self):
        import ast
        import inspect

        from bot.risk.risk_engine import RiskEngine
        from tests.source_scan import code_only

        src = code_only(inspect.getsource(RiskEngine))
        tree = ast.parse(src)
        # Every f-string that opens "CONFIDENCE: " must interpolate the
        # gate's own reading, not `idea.confidence`.
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            head = node.values[0] if node.values else None
            if not (isinstance(head, ast.Constant)
                    and isinstance(head.value, str)
                    and head.value.startswith("CONFIDENCE: ")):
                continue
            names = {ast.unparse(v.value) for v in node.values
                     if isinstance(v, ast.FormattedValue)}
            if any(n.endswith(".confidence") for n in names):
                bad.append(ast.unparse(node))
        assert not bad, f"check line quotes a figure it did not compare: {bad}"


class TestEveryReaderAsksIt:
    """No sixth hand-written copy of the question."""

    def test_no_surface_spells_its_own_blend_fallback(self):
        import ast

        from tests.source_scan import code_only, handler_sources

        # The one legitimate reader of the raw field is the leaf itself and
        # the calibrator that defines it; a card or a gate that re-spells the
        # `raw if raw is not None else confidence` shape is a second answer.
        offenders = []
        for path in handler_sources():
            if str(path).endswith(("signal_confidence.py",
                                   "confidence_calibration.py")):
                continue
            tree = ast.parse(code_only(pathlib.Path(path).read_text()))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "getattr"
                        and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and node.args[1].value == "blended_confidence_raw"):
                    offenders.append(path)
        assert not offenders, (
            "a surface reads the raw blend itself instead of asking "
            f"displayed_confidence(): {sorted(set(offenders))}"
        )


def _signal_engine(idea):
    """The engine's pending book with the ownership reading the real engine
    keeps, bound off the class (the harness `test_the_scheduled_posts...`
    uses, replicated rather than imported so this suite stands alone)."""
    from bot.core.engine import RuneClawEngine

    eng = types.SimpleNamespace(
        _pending_ideas={}, _engine_idea_ids=set(),
        live_executor=types.SimpleNamespace(_last_close_data=None, _positions={}),
    )
    for name in ("_engine_pending_ids", "_register_engine_idea"):
        setattr(eng, name, types.MethodType(getattr(RuneClawEngine, name), eng))
    eng._register_engine_idea(idea)
    return eng


class TestTheMonitorCard:
    """The NEW SIGNAL card is the third surface, and it gated on one figure
    while printing another."""

    def _alerts(self, idea):
        from bot.core.proactive_monitor import ProactiveMonitor

        return ProactiveMonitor(_signal_engine(idea))._check_trade_signals()

    def test_the_card_prints_the_figure_the_gate_admitted_it_on(self):
        alerts = self._alerts(_analyzer_idea())
        assert alerts, "the drive's own fixture produced no signal"
        body = alerts[0].body
        assert "70%" in body, body
        assert "31%" not in body

    def test_a_flag_never_clears_the_alert_bar(self):
        """`float(x or 0.0)` read a blend of `True` as 1.0.

        The first draft of this test planted `confidence=0.9` beside the
        bool, which `displayed_confidence` reads perfectly well as the idea's
        own figure -- so the mutant and the fix both alerted, and the test
        could not tell them apart. The idea's own confidence has to sit
        BELOW the bar for the bool to be the only thing that could clear it.
        """
        idea = _idea(confidence=0.20)
        idea.blended_confidence_raw = True
        assert not self._alerts(idea), (
            "a flag read as 1.0 cleared the alert bar for a 20% idea"
        )
