"""
Learning auto-refit — keep the learned models fresh as outcomes accrue.

The three learners (confidence calibration, voter weights, per-setup expectancy)
are fitted on closed-trade history, but were only ever fitted on demand (a manual
/calibration refit). Left alone they go stale: the market moves on, but the curves
don't. This counts closed outcomes and re-fits all three every N trades, so the
models track recent reality automatically.

What a refit changes, stated rather than assumed:
  - A refit rewrites the persisted curves, and two of the three are APPLIED by
    default: `CONFIDENCE_CALIBRATION_ENABLED` (default ON) and
    `SETUP_EXPECTANCY_ENABLED` (default ON), so a refit moves the confidence and
    the nudges the next analysis uses. Voter weights are applied only behind
    `VOTER_WEIGHT_LEARNING_ENABLED` (default OFF). This paragraph used to say
    every learner's application sat behind its own off-by-default flag, so a
    refit could never change a decision; that stopped being true when those two
    defaults flipped, and the sentence is how a reader would decide leaving
    auto-refit on was harmless.
  - Fail-open per learner: one learner failing to fit never blocks the others or
    the close path.
  - Gated by `LEARNING_AUTO_REFIT_ENABLED` (default ON); the caller checks the
    flag.
  - The count lives in memory, so it restarts at zero with the process. A fit
    saved under an older sample reading (`outcome_join.SAMPLE_READING`) would
    therefore stay applied for up to ``interval`` closes after a deploy that
    changed what counts as a sample; `refit_stale` refits those once when the
    bot's loop starts.
"""

from __future__ import annotations

import logging

from bot.learning.outcome_join import counted_under_current_rule

log = logging.getLogger("runeclaw.auto_refit")


class LearningAutoRefit:
    """Counts closed outcomes and refits the learners every ``interval`` trades."""

    def __init__(self, interval: int = 25) -> None:
        self.interval = max(1, int(interval))
        self._count = 0
        self._refits = 0

    def note_closed_trade(self, analyzer=None) -> bool:
        """Record one closed outcome. Returns True if a refit was triggered."""
        self._count += 1
        if self._count % self.interval != 0:
            return False
        self._refit_all(analyzer)
        self._refits += 1
        return True

    def _refit_all(self, analyzer=None) -> None:
        # Confidence calibration.
        try:
            from bot.learning.confidence_calibration import refit_and_save as _cal_refit
            _cal_refit()
            if analyzer is not None and hasattr(analyzer, "refresh_calibrator"):
                analyzer.refresh_calibrator()
        except Exception as exc:
            log.warning("auto-refit: calibration failed: %s", exc)
        # Voter weights.
        try:
            from bot.learning.voter_weights import refit_and_save as _vw_refit
            _vw_refit()
        except Exception as exc:
            log.warning("auto-refit: voter weights failed: %s", exc)
        # Per-setup expectancy (reload the singleton from the store).
        try:
            from bot.learning.setup_expectancy import get_setup_expectancy
            get_setup_expectancy(reload=True)
        except Exception as exc:
            log.warning("auto-refit: setup expectancy failed: %s", exc)
        log.info("Learning auto-refit complete (%d closed trades)", self._count)

    def summary(self) -> str:
        return (f"auto-refit: {self._count} closed trades, {self._refits} refits "
                f"(every {self.interval})")


def refit_stale(analyzer=None) -> list:
    """Refit each learned fit on disk that was counted under an older sample
    reading, and name the ones refit.

    A fit that is not on disk has never been made, and is left to the ordinary
    count rather than made here. A fit under the current reading is left alone.
    Fail-open per learner, like `_refit_all`: a learner that cannot be read or
    refit is logged and the other still runs.
    """
    refit = []
    try:
        from bot.learning import confidence_calibration as _cc
        cal = _cc.ConfidenceCalibrator.load()
        if cal is not None and not counted_under_current_rule(cal):
            _cc.refit_and_save()
            if analyzer is not None and hasattr(analyzer, "refresh_calibrator"):
                analyzer.refresh_calibrator()
            refit.append("confidence calibration")
    except Exception as exc:
        log.warning("stale refit: calibration failed: %s", exc)
    try:
        from bot.learning import voter_weights as _vw
        vw = _vw.VoterWeightLearner.load()
        if vw is not None and not counted_under_current_rule(vw):
            _vw.refit_and_save()
            refit.append("voter weights")
    except Exception as exc:
        log.warning("stale refit: voter weights failed: %s", exc)
    return refit
