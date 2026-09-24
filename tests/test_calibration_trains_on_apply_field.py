"""
Calibrator trains on the same field it applies to (deep-audit low #35).

The confidence calibrator is APPLIED to the analyzer-stage blended confidence
(before the calibration remap and setup-expectancy nudge), but it used to TRAIN
on the decision record's `confidence` — the post-adjustment value. Fitting one
distribution and remapping another is a systematic miscalibration. The decision
record now carries `blended_confidence_raw` (the apply-target), and
samples_from_decisions trains on it, falling back to `confidence` for a row
that has no raw figure.

THE FALLBACK IS NARROWER THAN IT WAS, and the argument is a stamp. It used to
take every row with no raw figure, on the reading that such a row was the
analyzer's before this field existed -- and a manual ticket's decision row has
no raw figure either, with a `confidence` of 1.0 that `build_manual_idea`
stamps. A row records whether its confidence was measured now; one recorded as
measured keeps the fallback, and an old row that says nothing cannot be told
from a stamp, so it is left out and counted.
"""

from bot.learning.confidence_calibration import ConfidenceCalibrator
from bot.learning.models import DecisionMemory


def _decision(tid, *, confidence=0.0, blended_raw=0.0, pnl=None, basis=""):
    return DecisionMemory(symbol="BTC/USDT", direction="LONG",
                          confidence=confidence, blended_confidence_raw=blended_raw,
                          confidence_basis=basis, paper_trade_id=tid,
                          pnl_result=pnl)


class TestTrainsOnApplyField:
    def test_uses_blended_raw_when_present(self):
        # Decision row carries the apply-target (0.7); the outcome row sets pnl.
        decisions = [
            _decision("t1", confidence=0.9, blended_raw=0.7),       # decision
            _decision("t1", pnl=5.0),                               # winning outcome
        ]
        samples = ConfidenceCalibrator.samples_from_decisions(decisions)
        # Trains on the blended_raw (0.7), NOT the post-adjustment confidence 0.9.
        assert samples == [(0.7, True)]

    def test_a_measured_row_without_a_raw_figure_falls_back_to_confidence(self):
        decisions = [
            _decision("t2", confidence=0.8, blended_raw=0.0, basis="measured"),
            _decision("t2", pnl=-3.0),                              # losing outcome
        ]
        samples = ConfidenceCalibrator.samples_from_decisions(decisions)
        assert samples == [(0.8, False)]

    def test_an_old_row_without_a_raw_figure_is_not_counted(self):
        # It could be the analyzer's before the field existed, or a manual
        # ticket's 1.0 stamp; nothing on it says which, so it is left out and
        # counted rather than fitted as a measurement.
        decisions = [
            _decision("t2", confidence=0.8, blended_raw=0.0),
            _decision("t2", pnl=-3.0),
        ]
        rows = ConfidenceCalibrator.rows_from_decisions(decisions)
        assert rows.samples == [] and rows.unattributed == 1

    def test_blended_raw_takes_precedence_over_confidence(self):
        decisions = [
            _decision("t3", confidence=0.95, blended_raw=0.55),
            _decision("t3", pnl=1.0),
        ]
        (conf, won), = ConfidenceCalibrator.samples_from_decisions(decisions)
        assert conf == 0.55 and won is True


class TestModelsCarryField:
    def test_decision_memory_defaults_zero(self):
        assert DecisionMemory().blended_confidence_raw == 0.0

    def test_trade_idea_carries_field(self):
        from bot.utils.models import Direction, TradeIdea
        idea = TradeIdea(asset="BTC/USDT", direction=Direction.LONG,
                         entry_price=100.0, stop_loss=95.0, take_profit=110.0,
                         confidence=0.8, reasoning="t", blended_confidence_raw=0.72)
        assert idea.blended_confidence_raw == 0.72
