"""
Uncalibrated-LLM weight cap + env-configurable blend weights
(LLM Optimization Plan Phase 5 / audit issue #3).

The LLM drives `llm_weight` (0.6) of the blended confidence, but until
confidence calibration is ON its confidence is unproven against realized
outcomes — a hallucinated/overconfident thesis flows straight into sizing.
When the guard is ON and no fitted calibration curve is being applied, the LLM
weight is capped and the freed weight is shifted to the deterministic confluence
score (total preserved). The cap lifts once a fitted curve is applied -- the
flag being on is not enough, because with no curve fitted calibration is
identity. Tests exercise the _blend_weights helper.
"""

from types import SimpleNamespace
from unittest.mock import patch

from bot.core.analyzer import Analyzer

#: A fitted curve, and one that has not reached its minimum yet.
READY = SimpleNamespace(is_ready=lambda: True)
UNREADY = SimpleNamespace(is_ready=lambda: False)


def _analyzer(cal=False):
    """``cal`` is what `_get_calibrator` hands back: ``False`` is the
    analyzer's own "no curve on disk" sentinel."""
    a = Analyzer.__new__(Analyzer)
    a._calibrator = cal
    return a


def _cfg(llm_w=0.6, conf_w=0.4, cap_enabled=False, cap=0.4, calib=False):
    p = patch("bot.core.analyzer.CONFIG")
    m = p.start()
    a = m.analyzer
    a.llm_weight = llm_w
    a.confluence_weight = conf_w
    a.uncalibrated_llm_weight_cap_enabled = cap_enabled
    a.uncalibrated_llm_weight_cap = cap
    a.confidence_calibration_enabled = calib
    return p


class TestDefault:
    def test_disabled_returns_configured_weights(self):
        p = _cfg(cap_enabled=False)
        try:
            assert _analyzer()._blend_weights() == (0.6, 0.4)
        finally:
            p.stop()


class TestCapActive:
    def test_caps_llm_and_shifts_to_confluence(self):
        # Guard ON, calibration OFF, llm 0.6 > cap 0.4 → (0.4, 0.6).
        p = _cfg(cap_enabled=True, cap=0.4, calib=False)
        try:
            llm_w, conf_w = _analyzer()._blend_weights()
        finally:
            p.stop()
        assert llm_w == 0.4
        assert abs(conf_w - 0.6) < 1e-9
        assert abs((llm_w + conf_w) - 1.0) < 1e-9  # total preserved

    def test_total_weight_preserved_for_nonunit_sum(self):
        # Weights that don't sum to 1 still preserve their total after capping.
        p = _cfg(llm_w=0.8, conf_w=0.1, cap_enabled=True, cap=0.5, calib=False)
        try:
            llm_w, conf_w = _analyzer()._blend_weights()
        finally:
            p.stop()
        assert llm_w == 0.5
        assert abs(conf_w - 0.4) < 1e-9          # 0.1 + (0.8 - 0.5)
        assert abs((llm_w + conf_w) - 0.9) < 1e-9  # original total 0.9


class TestCapInactive:
    def test_an_applied_curve_lifts_cap(self):
        # Once a fitted curve is applied, the LLM's confidence has been checked
        # against outcomes and the cap no longer applies.
        p = _cfg(cap_enabled=True, cap=0.4, calib=True)
        try:
            assert _analyzer(READY)._blend_weights() == (0.6, 0.4)
        finally:
            p.stop()

    def test_no_cap_when_llm_already_below_cap(self):
        p = _cfg(llm_w=0.3, conf_w=0.7, cap_enabled=True, cap=0.4, calib=False)
        try:
            assert _analyzer()._blend_weights() == (0.3, 0.7)
        finally:
            p.stop()


class TestTheFlagIsNotTheCurve:
    """CONFIDENCE_CALIBRATION_ENABLED was on by default, and until the curve has
    its minimum of closes calibration is identity. The cap used to lift on the
    flag, so it never held."""

    def _weights(self, cal):
        p = _cfg(cap_enabled=True, cap=0.4, calib=True)
        try:
            return _analyzer(cal)._blend_weights()
        finally:
            p.stop()

    def test_no_curve_on_disk_keeps_the_cap(self):
        llm_w, conf_w = self._weights(False)
        assert llm_w == 0.4 and abs(conf_w - 0.6) < 1e-9

    def test_a_curve_below_its_minimum_keeps_the_cap(self):
        llm_w, conf_w = self._weights(UNREADY)
        assert llm_w == 0.4 and abs(conf_w - 0.6) < 1e-9

    def test_a_curve_that_cannot_be_read_keeps_the_cap(self):
        def boom():
            raise OSError("planted")
        a = _analyzer()
        a._get_calibrator = boom
        p = _cfg(cap_enabled=True, cap=0.4, calib=True)
        try:
            assert a._blend_weights()[0] == 0.4
        finally:
            p.stop()

    def test_a_fitted_curve_with_the_flag_off_is_not_applied(self):
        # The analyzer computes the curve in shadow and applies nothing, so the
        # LLM is as unproven as it was.
        p = _cfg(cap_enabled=True, cap=0.4, calib=False)
        try:
            assert _analyzer(READY)._blend_weights()[0] == 0.4
        finally:
            p.stop()
