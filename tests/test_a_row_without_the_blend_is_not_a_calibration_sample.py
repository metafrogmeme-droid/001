"""A decision row with no analyzer blend is not a sample for a curve fitted on blends.

The confidence curve is FITTED on the analyzer's pre-calibration blend
(`blended_confidence_raw`) and APPLIED to it. A row recorded as measured with
no blend kept a fallback to its `confidence`, and the one producer that wrote
such a row was the scan card's button, which stamped 0.6 on every row: driven,
for scan scores 0.91 and 0.42 alike, the decision row the engine writes
(`blended_confidence_raw` 0.0, basis "measured") came back as the sample
`(0.6, lost)`. A real scan score is not a blend either, so the rule is about
the ROW, not the producer: without the blend it is left out, and counted.
"""
from __future__ import annotations

from types import SimpleNamespace

from bot.learning.confidence_calibration import ConfidenceCalibrator
from bot.learning.models import DecisionMemory
from bot.learning.outcome_join import SAMPLE_READING, counted_under_current_rule
from bot.learning.readiness import _calibration_left_out
from bot.risk.quality_ladder import MEASURED_BASIS


def _pair(tid, *, confidence, raw, basis, pnl):
    decision = DecisionMemory(symbol="SOL/USDT", direction="LONG", confidence=confidence,
                              blended_confidence_raw=raw, confidence_basis=basis,
                              paper_trade_id=tid, decision="TRADE_ACCEPTED_LIVE")
    outcome = DecisionMemory(symbol="SOL/USDT", direction="LONG", paper_trade_id=tid,
                             pnl_result=pnl)
    return [decision, outcome]


def test_the_scan_buttons_stamped_row_is_not_fitted():
    rows = ConfidenceCalibrator.rows_from_decisions(
        _pair("T1", confidence=0.6, raw=0.0, basis=MEASURED_BASIS, pnl=-12.0))
    assert rows.samples == [], "the stamp 0.6 was fitted as a measured sample"
    assert rows.no_blend == 1 and rows.unattributed == 0 and rows.not_measured == 0


def test_a_measured_score_without_a_blend_is_not_fitted_either():
    """Not only the stamp: a scan score is a measurement of a DIFFERENT
    quantity, and the curve would be fitted on one and applied to the other."""
    rows = ConfidenceCalibrator.rows_from_decisions(
        _pair("T2", confidence=0.91, raw=0.0, basis=MEASURED_BASIS, pnl=5.0))
    assert rows.samples == [] and rows.no_blend == 1


def test_a_row_with_the_blend_is_fitted_on_the_blend():
    rows = ConfidenceCalibrator.rows_from_decisions(
        _pair("T3", confidence=0.9, raw=0.66, basis=MEASURED_BASIS, pnl=5.0))
    assert rows.samples == [(0.66, True)] and rows.no_blend == 0


def test_an_old_row_that_says_nothing_is_still_unattributed_not_no_blend():
    rows = ConfidenceCalibrator.rows_from_decisions(
        _pair("T4", confidence=0.8, raw=0.0, basis="", pnl=-1.0))
    assert rows.samples == [] and rows.unattributed == 1 and rows.no_blend == 0


def test_a_stamp_is_still_not_measured_before_it_is_blendless():
    rows = ConfidenceCalibrator.rows_from_decisions(
        _pair("T5", confidence=1.0, raw=0.0,
              basis="manual ticket: confidence is a stamp, not a measurement", pnl=1.0))
    assert rows.not_measured == 1 and rows.no_blend == 0 and rows.samples == []


def test_the_readiness_card_names_the_left_out_rows():
    rows = ConfidenceCalibrator.rows_from_decisions(
        _pair("T1", confidence=0.6, raw=0.0, basis=MEASURED_BASIS, pnl=-12.0)
        + _pair("T3", confidence=0.9, raw=0.66, basis=MEASURED_BASIS, pnl=5.0))
    note = _calibration_left_out(rows)
    assert "1 with no analyzer blend, the figure the curve is fitted on" in note
    assert _calibration_left_out(ConfidenceCalibrator.rows_from_decisions(
        _pair("T3", confidence=0.9, raw=0.66, basis=MEASURED_BASIS, pnl=5.0))) == ""


def test_a_fit_counted_under_the_old_rule_is_refit():
    """What counts as a sample changed, so a fit saved under reading 2 rests on
    samples the current rule does not count (`auto_refit.refit_stale`)."""
    assert SAMPLE_READING == 3
    assert not counted_under_current_rule(SimpleNamespace(sample_reading=2))
    assert counted_under_current_rule(SimpleNamespace(sample_reading=3))
