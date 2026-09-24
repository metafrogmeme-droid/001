"""The calibration curve is applied once, and only a fitted curve counts.

Three readers treated the curve's FLAG as the curve:

* The uncalibrated-LLM weight cap lifted when CONFIDENCE_CALIBRATION_ENABLED was
  on -- which it is by default, with no curve fitted, when calibration is
  identity. So the LLM ran at 0.6 of the blend on a confidence nothing had
  checked, while the frozen benchmark (calibration forced off) measured the
  capped 0.4 / 0.6 blend the 0.60 floor was tuned on.
* The auto-confirm bar calibrated ``idea.confidence``, which with that flag on
  had already been through the curve: cal(cal(raw)).
* The readiness card reported the curve as applied or not off
  AUTO_CONFIRM_USE_CALIBRATED alone, and never named the flag that moves every
  entry.

And the module docstring and docs/CONFIDENCE_CALIBRATION.md said that flag was
default OFF and the curve shadow-only.
"""
from __future__ import annotations

import ast
import pathlib
import random
import re
from types import SimpleNamespace

import pytest

from bot.config import CONFIG
from bot.learning import confidence_calibration as cc
from bot.learning.confidence_calibration import ConfidenceCalibrator, pre_calibration_confidence
from tests.default_comments import declared_defaults

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _fitted(win_at=lambda c: c, n=200, seed=5):
    """A real curve, fitted on outcomes that win at ``win_at(confidence)``."""
    rng = random.Random(seed)
    samples = []
    for _ in range(n):
        c = rng.uniform(0.55, 0.98)
        samples.append((c, rng.random() < win_at(c)))
    cal = ConfidenceCalibrator().fit(samples)
    assert cal.is_ready()
    return cal


@pytest.fixture
def _flags():
    was = (CONFIG.auto_confirm_use_calibrated,
           CONFIG.analyzer.confidence_calibration_enabled,
           CONFIG.analyzer.uncalibrated_llm_weight_cap_enabled)
    try:
        yield
    finally:
        object.__setattr__(CONFIG, "auto_confirm_use_calibrated", was[0])
        object.__setattr__(CONFIG.analyzer, "confidence_calibration_enabled", was[1])
        object.__setattr__(CONFIG.analyzer, "uncalibrated_llm_weight_cap_enabled", was[2])


# ── the auto-confirm bar ────────────────────────────────────────────────────

def _gate(cal, idea):
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.analyzer = SimpleNamespace(_get_calibrator=lambda: cal)
    return eng._auto_confirm_gate_value(idea)


def test_the_bar_calibrates_the_figure_the_curve_was_fitted_on(_flags):
    object.__setattr__(CONFIG, "auto_confirm_use_calibrated", True)
    cal = _fitted()
    raw = 0.95
    on_idea = round(cal.calibrate(raw), 2)          # what the analyzer puts there
    idea = SimpleNamespace(confidence=on_idea, blended_confidence_raw=raw)
    got = _gate(cal, idea)
    assert got == pytest.approx(min(on_idea, cal.calibrate(raw)))
    # Twice through the curve is strictly lower on this fit -- which is what
    # the bar used to be tested against.
    assert cal.calibrate(on_idea) < got


@pytest.mark.parametrize("raw", [None, 0.0, True, "0.9"])
def test_an_idea_without_the_analyzers_figure_calibrates_its_confidence(_flags, raw):
    # A manual ticket or a drift re-offer carries no analyzer figure; the bar
    # reads what it has, as it always did.
    object.__setattr__(CONFIG, "auto_confirm_use_calibrated", True)
    cal = _fitted()
    idea = SimpleNamespace(confidence=0.9, blended_confidence_raw=raw)
    assert _gate(cal, idea) == pytest.approx(min(0.9, cal.calibrate(0.9)))


def test_the_bar_never_rises_above_the_ideas_own_confidence(_flags):
    # A curve that says the record wins MORE often than it claims must not
    # loosen the bar: min() keeps the idea's own figure.
    object.__setattr__(CONFIG, "auto_confirm_use_calibrated", True)
    cal = _fitted(win_at=lambda c: 0.98)
    idea = SimpleNamespace(confidence=0.70, blended_confidence_raw=0.70)
    assert cal.calibrate(0.70) > 0.70
    assert _gate(cal, idea) == 0.70


def test_the_reading_is_one_function_both_readers_ask():
    # The learner reads it off a decision row, the bar off an idea: one field,
    # one reading.
    assert pre_calibration_confidence(SimpleNamespace(blended_confidence_raw=0.7)) == 0.7
    assert pre_calibration_confidence(SimpleNamespace()) is None
    src = pathlib.Path(cc.__file__).read_text()
    assert src.count("blended_confidence_raw\"") + src.count("blended_confidence_raw'") == 1
    eng = (ROOT / "bot" / "core" / "engine.py").read_text()
    body = eng[eng.index("def _auto_confirm_gate_value"):]
    body = body[:body.index("\n    def ", 10)]
    assert "pre_calibration_confidence(idea)" in body


# ── the LLM weight cap ──────────────────────────────────────────────────────

def _analyzer():
    from bot.core.analyzer import Analyzer
    a = Analyzer.__new__(Analyzer)
    a._calibrator = None          # load from disk, as a fresh analyzer does
    return a


def test_no_curve_on_disk_keeps_the_llm_capped(_flags):
    object.__setattr__(CONFIG.analyzer, "confidence_calibration_enabled", True)
    object.__setattr__(CONFIG.analyzer, "uncalibrated_llm_weight_cap_enabled", True)
    assert not pathlib.Path(cc._CAL_FILE).exists()
    llm_w, _ = _analyzer()._blend_weights()
    assert llm_w == CONFIG.analyzer.uncalibrated_llm_weight_cap


def test_a_curve_below_its_minimum_keeps_the_llm_capped(_flags):
    object.__setattr__(CONFIG.analyzer, "confidence_calibration_enabled", True)
    object.__setattr__(CONFIG.analyzer, "uncalibrated_llm_weight_cap_enabled", True)
    ConfidenceCalibrator().fit([(0.7, True)] * 10).save()
    llm_w, _ = _analyzer()._blend_weights()
    assert llm_w == CONFIG.analyzer.uncalibrated_llm_weight_cap


def test_a_fitted_curve_on_disk_lifts_the_cap(_flags):
    object.__setattr__(CONFIG.analyzer, "confidence_calibration_enabled", True)
    object.__setattr__(CONFIG.analyzer, "uncalibrated_llm_weight_cap_enabled", True)
    _fitted().save()
    llm_w, _ = _analyzer()._blend_weights()
    assert llm_w == CONFIG.analyzer.llm_weight


# ── the readiness card ──────────────────────────────────────────────────────

class _Store:
    def get_decisions(self, symbol=None, limit=100):
        return []


@pytest.mark.parametrize("entry,confirm,expect", [
    (True, True, "CONFIDENCE_CALIBRATION_ENABLED + AUTO_CONFIRM_USE_CALIBRATED"),
    (True, False, "CONFIDENCE_CALIBRATION_ENABLED"),
    (False, True, "AUTO_CONFIRM_USE_CALIBRATED"),
])
def test_the_card_names_every_flag_that_applies_the_curve(_flags, entry, confirm, expect):
    from bot.learning.readiness import assess_readiness
    object.__setattr__(CONFIG.analyzer, "confidence_calibration_enabled", entry)
    object.__setattr__(CONFIG, "auto_confirm_use_calibrated", confirm)
    comp = assess_readiness(store=_Store())["components"]["calibration"]
    assert comp["flag"] == expect and comp["applied"] is True


def test_with_neither_flag_on_the_curve_is_not_applied(_flags):
    from bot.learning.readiness import assess_readiness
    object.__setattr__(CONFIG.analyzer, "confidence_calibration_enabled", False)
    object.__setattr__(CONFIG, "auto_confirm_use_calibrated", False)
    comp = assess_readiness(store=_Store())["components"]["calibration"]
    assert comp["applied"] is False


# ── what the module and its page say about the defaults ─────────────────────

def _defaults():
    return {env: default for env, default in declared_defaults().values()}


def test_the_module_docstring_states_both_flags_real_defaults():
    doc = ast.get_docstring(ast.parse(pathlib.Path(cc.__file__).read_text()))
    flat = " ".join(doc.split())
    d = _defaults()
    assert d["CONFIDENCE_CALIBRATION_ENABLED"] is True
    assert "both are ON by default" in flat
    assert "`CONFIDENCE_CALIBRATION_ENABLED` moves every idea's confidence" in flat
    # The retraction may name what it corrected, once.
    assert flat.count("default-OFF flag (CONFIDENCE_CALIBRATION_ENABLED)") == 0


def test_the_page_states_the_real_default_and_the_measurement():
    page = (ROOT / "docs" / "CONFIDENCE_CALIBRATION.md").read_text()
    head = page[:page.index("## Why")]
    assert "ON BY DEFAULT" in head
    assert not re.search(r"Default OFF\s*\n?>?\s*\(`CONFIDENCE_CALIBRATION_ENABLED=false`\)", head)
    assert "## What a fitted curve does to the entry floor, measured" in page


def test_the_warning_reads_as_two_flags_when_both_apply_it():
    from bot.learning.readiness import recommendations_for
    two = recommendations_for({"calibration": {
        "state": "VALIDATING", "applied": True,
        "flag": "CONFIDENCE_CALIBRATION_ENABLED + AUTO_CONFIRM_USE_CALIBRATED"}})
    one = recommendations_for({"calibration": {
        "state": "VALIDATING", "applied": True, "flag": "AUTO_CONFIRM_USE_CALIBRATED"}})
    assert "AUTO_CONFIRM_USE_CALIBRATED are ON" in two[0]
    assert "AUTO_CONFIRM_USE_CALIBRATED is ON" in one[0]
