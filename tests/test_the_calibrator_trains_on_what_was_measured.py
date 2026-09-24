"""The calibrator trains on confidences measured about their own trade, once each.

Two ways a closed trade reached the calibrator as something it was not:

- **A stamp, fitted as a measurement.** A manual ticket goes through the same
  confirm path as the engine's own ideas, and the decision row it leaves
  carries ``confidence=1.0`` -- the stamp `build_manual_idea` writes -- and no
  analyzer figure. When the ticket closed, the join paired that row with its
  outcome and the calibrator fitted a measured 100%, in the top bin, the one
  the auto-confirm threshold is read against. A drift re-offer's confidence
  was measured about another trade's levels, and joined the same way.
- **One trade, two samples.** The confirm path pops the pending idea only on
  a successful execution, and logs a decision row either way, so a failed
  attempt (``EXECUTION_FAILED``) and the retry that opened the position share
  one trade id; the calibrator's join and the voter learner's both joined
  both rows to the one outcome.

The record says whether a confidence was measured now (`confidence_basis`,
the auto-confirm door's own question), both learners ask one join, and the
readiness card names what the calibrator left out.
"""
from __future__ import annotations

import ast
import pathlib
from types import SimpleNamespace

import pytest

from bot.learning.confidence_calibration import ConfidenceCalibrator
from bot.learning.models import DecisionMemory
from bot.learning.outcome_join import NOT_OPENED_DECISIONS, join_outcomes, opened
from bot.learning.voter_weights import VoterWeightLearner
from bot.risk import quality_ladder
from bot.risk.quality_ladder import MEASURED_BASIS, confidence_basis
from bot.utils.models import Direction, TradeIdea

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / "bot" / "core" / "engine.py"


def _idea(**kw):
    base = dict(asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
                stop_loss=95.0, take_profit=110.0, confidence=0.72,
                reasoning="t")
    base.update(kw)
    return TradeIdea(**base)


def _decision(tid, *, conf=0.72, raw=0.0, basis="", decision="TRADE_ACCEPTED_LIVE",
              votes=None):
    return DecisionMemory(symbol="BTC/USDT", direction="LONG", confidence=conf,
                          blended_confidence_raw=raw, confidence_basis=basis,
                          decision=decision, paper_trade_id=tid,
                          confluence_votes=votes or [])


def _outcome(tid, pnl):
    return DecisionMemory(symbol="BTC/USDT", direction="LONG", pnl_result=pnl,
                          paper_trade_id=tid, decision=f"OUTCOME:{tid}",
                          source="live_outcome")


# ── the basis a decision row records ────────────────────────────────────────

class TestTheBasis:
    def test_the_analyzers_own_idea_is_measured(self):
        assert confidence_basis(_idea()) == MEASURED_BASIS

    def test_a_manual_tickets_stamp_is_not(self):
        why = confidence_basis(_idea(source="manual", confidence=1.0))
        assert why != MEASURED_BASIS and "stamp" in why

    def test_a_drift_re_offers_carried_confidence_is_not(self):
        from bot.formatters.drift_offer import reanalyzed_idea
        re_offer = reanalyzed_idea(_idea(), 104.0)
        why = confidence_basis(re_offer)
        assert why != MEASURED_BASIS and "carried from" in why

    def test_it_is_the_auto_confirm_doors_answer(self, monkeypatch):
        # One question, two readers: a second copy of the judgement here would
        # agree with every fixture and diverge on the first edit to either.
        monkeypatch.setattr(quality_ladder, "auto_confirm_refusal",
                            lambda idea: "planted reason")
        assert confidence_basis(_idea()) == "planted reason"
        monkeypatch.setattr(quality_ladder, "auto_confirm_refusal",
                            lambda idea: None)
        assert confidence_basis(_idea(source="manual")) == MEASURED_BASIS


def _log_decision_calls():
    tree = ast.parse(ENGINE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "log_decision"):
            yield node


def test_every_decision_the_engine_logs_records_its_basis():
    calls = list(_log_decision_calls())
    assert len(calls) >= 3, "the walk found fewer decision writers than exist"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "confidence_basis" in kw, f"engine.py:{call.lineno} records no basis"
        val = kw["confidence_basis"]
        assert (isinstance(val, ast.Call) and isinstance(val.func, ast.Name)
                and val.func.id == "confidence_basis"), (
            f"engine.py:{call.lineno} writes a basis the reading did not make")


def test_the_basis_survives_the_store(tmp_path):
    from bot.learning.experience import ExperienceMemory
    from bot.learning.store import LearningStore
    store = LearningStore(str(tmp_path))
    ExperienceMemory(store).record_trade_decision(
        symbol="BTC/USDT", direction="LONG", confidence=1.0,
        confidence_basis="manual ticket: confidence is a stamp, not a measurement",
        confluence_score=1.0, entry_price=100.0, stop_loss=95.0,
        take_profit=110.0, risk_reward=2.0, position_size_usd=50.0,
        decision="TRADE_ACCEPTED_LIVE", paper_trade_id="T1")
    ExperienceMemory(store).record_closed_outcome(
        symbol="BTC/USDT", direction="LONG", pnl_result=-3.0, trade_id="T1")
    rows = ConfidenceCalibrator.rows_from_decisions(store.get_decisions(limit=100))
    assert rows.samples == [] and rows.not_measured == 1


# ── what the calibrator fits ────────────────────────────────────────────────

class TestTheCalibratorsSamples:
    def test_a_measured_confidence_is_fitted(self):
        rows = ConfidenceCalibrator.rows_from_decisions(
            [_decision("T1", raw=0.72, basis=MEASURED_BASIS), _outcome("T1", 5.0)])
        assert rows.samples == [(0.72, True)]

    def test_a_stamp_is_left_out_and_counted(self):
        rows = ConfidenceCalibrator.rows_from_decisions([
            _decision("T1", conf=1.0, basis="manual ticket: a stamp"),
            _outcome("T1", -3.0)])
        assert rows.samples == [] and rows.not_measured == 1

    def test_an_old_row_with_the_analyzers_figure_is_fitted(self):
        rows = ConfidenceCalibrator.rows_from_decisions(
            [_decision("T1", conf=0.9, raw=0.66), _outcome("T1", 1.0)])
        assert rows.samples == [(0.66, True)] and rows.unattributed == 0

    def test_an_old_row_without_it_is_left_out_and_counted(self):
        # The exact shape of a manual ticket's row before the basis existed.
        rows = ConfidenceCalibrator.rows_from_decisions(
            [_decision("T1", conf=1.0), _outcome("T1", -3.0)])
        assert rows.samples == [] and rows.unattributed == 1


# ── one trade, one sample ───────────────────────────────────────────────────

def _failed_then_retried(tid="T1"):
    return [
        _decision(tid, raw=0.72, basis=MEASURED_BASIS, decision="EXECUTION_FAILED",
                  votes=[("rsi", 1, 1.0)]),
        _decision(tid, raw=0.72, basis=MEASURED_BASIS,
                  decision="TRADE_ACCEPTED_LIVE", votes=[("rsi", 1, 1.0)]),
        _outcome(tid, 4.0),
    ]


def test_a_failed_attempt_and_its_retry_are_one_calibration_sample():
    rows = ConfidenceCalibrator.rows_from_decisions(_failed_then_retried())
    assert rows.samples == [(0.72, True)] and rows.not_opened == 1


def test_a_failed_attempt_and_its_retry_are_one_voter_sample():
    assert len(VoterWeightLearner.samples_from_decisions(_failed_then_retried())) == 1


def test_a_trade_that_never_opened_is_no_sample():
    rows = ConfidenceCalibrator.rows_from_decisions([
        _decision("T1", raw=0.72, basis=MEASURED_BASIS, decision="EXECUTION_FAILED"),
        _outcome("T1", 4.0)])
    assert rows.samples == [] and rows.not_opened == 1


def test_two_opened_rows_under_one_id_are_one_sample():
    # No product path writes this today -- the idea is popped on the success
    # that writes the accepted row -- so the join's own claim, one row per
    # trade id, is measured on planted rows rather than trusted.
    rows = ConfidenceCalibrator.rows_from_decisions([
        _decision("T1", raw=0.72, basis=MEASURED_BASIS),
        _decision("T1", raw=0.72, basis=MEASURED_BASIS),
        _outcome("T1", 4.0)])
    assert rows.samples == [(0.72, True)]


def test_the_outcome_row_is_never_its_own_decision():
    joined = join_outcomes([_outcome("T1", 4.0)])
    assert joined.rows == [] and joined.not_opened == 0


def test_a_row_with_no_word_still_joins():
    # A row an older build wrote carries no decision word; refusing it would
    # drop history on a guess about vocabulary.
    d = SimpleNamespace(paper_trade_id="T1", pnl_result=None, confidence=0.7,
                        blended_confidence_raw=0.7, confidence_basis="")
    assert len(join_outcomes([d, _outcome("T1", 1.0)]).rows) == 1


def _decision_words():
    words = set()
    for call in _log_decision_calls():
        for k in call.keywords:
            if k.arg == "decision":
                for n in ast.walk(k.value):
                    if isinstance(n, ast.Constant) and isinstance(n.value, str):
                        words.add(n.value)
    return words


def test_every_word_the_engine_writes_is_placed():
    words = _decision_words()
    assert "EXECUTION_FAILED" in words and "TRADE_ACCEPTED_LIVE" in words, words
    unplaced = sorted(w for w in words if opened(w) is None)
    assert unplaced == [], f"decision words the join cannot place: {unplaced}"


@pytest.mark.parametrize("word", sorted(NOT_OPENED_DECISIONS))
def test_the_not_opened_words_are_not_opened(word):
    assert opened(word) is False


# ── the card ────────────────────────────────────────────────────────────────

class _Store:
    def __init__(self, rows):
        self._rows = rows

    def get_decisions(self, symbol=None, limit=100):
        return self._rows[-limit:]


def _calibration_note(rows):
    from bot.learning import readiness as rd
    out = rd.assess_readiness(store=_Store(rows))
    return out["components"]["calibration"].get("note") or ""


def test_the_card_names_what_it_left_out():
    note = _calibration_note([
        _decision("T1", conf=1.0, basis="manual ticket: a stamp"), _outcome("T1", 1.0),
        _decision("T2", conf=1.0), _outcome("T2", 1.0),
        *_failed_then_retried("T3"),
    ])
    assert "1 whose confidence was a stamp or carried from another idea" in note
    assert "1 recorded before the bot marked which confidences were measured" in note
    assert "1 failed attempt(s) before a retry that opened" in note


def test_the_card_says_nothing_when_nothing_was_left_out():
    note = _calibration_note([
        _decision("T1", raw=0.72, basis=MEASURED_BASIS), _outcome("T1", 1.0)])
    assert "not counted" not in note


def test_a_flag_where_the_analyzers_figure_belongs_is_not_one():
    # `True > 0.0` holds and `float(True)` is 1.0, so a bool read as a number
    # would be a measured 100% -- the stamp this reading exists to refuse.
    d = SimpleNamespace(paper_trade_id="T1", pnl_result=None, decision="TRADE_ACCEPTED_LIVE",
                        confidence=1.0, blended_confidence_raw=True, confidence_basis="")
    rows = ConfidenceCalibrator.rows_from_decisions([d, _outcome("T1", 1.0)])
    assert rows.samples == [] and rows.unattributed == 1
