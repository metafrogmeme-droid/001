"""A confidence is a score, not a probability, on every surface that prints it.

Every card printed an idea's confidence as ``72%``, which reads as a chance of
winning, and the chat model was handed "confidence 72%" with nothing saying
what it was. The figure `displayed_confidence` answers is the analyzer's blend
snapshotted BEFORE any calibration curve, or a producer's own score that never
went through one, so no figure it answers has been mapped to outcomes. It is
printed ``score 72 (uncalibrated)``; the model is told what that means; and a
percent sign is left for a figure a fitted curve produced, which this reading
never answers.

Driven: the reading, the chat's pending-ideas block, and the scan card's
score column, rendered and read back.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from bot.core.signal_confidence import (
    SCORE_MEANING,
    STAMP_TEXT,
    UNREAD_DASH,
    ConfidenceReading,
    displayed_confidence,
    score_number,
    score_text,
)
from bot.utils.models import Direction, TradeIdea
from tests.png_text import strings


def _idea(**kw) -> TradeIdea:
    d = dict(id="T-1", asset="SOL/USDT", direction=Direction.LONG,
             entry_price=180.0, stop_loss=176.0, take_profit=190.0,
             confidence=0.31, reasoning="drive")
    d.update(kw)
    return TradeIdea(**d)


def _analyzer_idea(blend=0.72, **kw) -> TradeIdea:
    idea = _idea(**kw)
    idea.blended_confidence_raw = blend
    return idea


class TestTheReading:

    def test_a_measured_blend_is_a_score(self):
        assert displayed_confidence(_analyzer_idea()).text() == "score 72 (uncalibrated)"

    def test_a_producers_own_figure_is_a_score_too(self):
        r = displayed_confidence(_idea(confidence=0.55))
        assert r.basis == "own"
        assert r.text() == "score 55 (uncalibrated)"

    def test_no_measured_figure_carries_a_percent_sign(self):
        for v in (0.0, 0.004, 0.5, 0.72, 0.999, 1.0):
            t = ConfidenceReading(v, "blend").text()
            assert "%" not in t and t.startswith("score "), t

    def test_a_stamp_and_an_unread_field_keep_their_own_words(self):
        assert ConfidenceReading(None, "stamp").text() == STAMP_TEXT
        assert ConfidenceReading(None, "unread").text() == UNREAD_DASH

    def test_the_bare_number_and_the_text_are_one_rounding(self):
        assert score_number(0.725) in score_text(0.725)
        assert score_text(None) == UNREAD_DASH

    def test_there_is_no_percent_spelling_left_to_ask(self):
        """`pct()` was the method every surface called; a second spelling of
        the same reading would be a second answer."""
        assert not hasattr(ConfidenceReading(0.7, "blend"), "pct")

    def test_the_meaning_says_it_is_not_a_probability(self):
        assert "not a probability or a win rate" in SCORE_MEANING
        assert "calibration curve" in SCORE_MEANING


def _pending_block(ideas):
    from bot.skills.telegram_handler import TelegramHandler
    return TelegramHandler._pending_ideas_block(NS(engine=NS(pending_ideas=ideas)))


class TestTheModelIsToldWhatTheScoreIs:

    def test_the_row_carries_the_score_and_the_block_says_what_it_is(self):
        out = _pending_block([_analyzer_idea()])
        assert "confidence score 72 (uncalibrated)" in out
        assert SCORE_MEANING in out
        assert "72%" not in out

    def test_an_empty_queue_carries_no_meaning_it_does_not_need(self):
        out = _pending_block([])
        assert "none queued" in out and SCORE_MEANING not in out


class TestTheScanCardScoreColumn:

    def _drawn(self, score):
        from bot.formatters.signal_card import render_scan_results_card
        setups = [{"sym": "SOL/USDT", "dir": "LONG", "entry": 180.0, "sl": 176.0,
                   "tp": 190.0, "rr": 2.5, "rsi": 0, "vol_ratio": None,
                   "score": score}]
        return strings(render_scan_results_card, setups)

    def test_the_column_is_a_bare_number_under_its_label(self):
        drew = self._drawn(0.72)
        assert "SCORE" in drew and "72" in drew
        assert "72%" not in drew

    def test_a_score_already_on_the_hundred_scale_reads_the_same(self):
        drew = self._drawn(72)
        assert "72" in drew and "72%" not in drew
