"""A learned curve counted under an older rule is refit, not applied.

The calibrator and the voter-weight learner persist a fitted curve, and a curve
rests on the samples its fit counted. The commit that stopped a manual ticket's
stamp being fitted as a measurement, and a retried trade being counted twice,
changed what counts -- and a curve saved before it still rested on both. The
auto-refit that would replace it counts closes in memory, so it restarts at zero
with every deploy: the old curve stayed applied for up to 25 closes after the
fix shipped, on a flag (`CONFIDENCE_CALIBRATION_ENABLED`) that is ON by default.

Each saved fit records the reading it was counted under now, the bot refits the
stale ones once when its loop starts, and the readiness card stops lending a
stale fit's count to this record.
"""
from __future__ import annotations

import ast
import asyncio
import json
import pathlib
from types import SimpleNamespace

import pytest

from bot.learning import auto_refit
from bot.learning import confidence_calibration as cc
from bot.learning import voter_weights as vw
from bot.learning.outcome_join import SAMPLE_READING, counted_under_current_rule, reading_of
from tests.default_comments import declared_defaults

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _plant(path, d):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d))


def _stale_cal():
    d = cc.ConfidenceCalibrator().to_dict()
    d.update(x=[0.55, 0.95], y=[0.40, 0.70], n_samples=40)
    d.pop("sample_reading")
    return d


def _stale_vw():
    d = vw.VoterWeightLearner().to_dict()
    d.update(mult={"rsi": 1.3}, n_samples=40)
    d.pop("sample_reading")
    return d


# ── the reading a fit records ───────────────────────────────────────────────

def test_a_fit_saves_the_reading_it_was_counted_under(tmp_path):
    for cls, fname in ((cc.ConfidenceCalibrator, "c.json"), (vw.VoterWeightLearner, "v.json")):
        path = str(tmp_path / fname)
        cls().save(path)
        assert json.loads(pathlib.Path(path).read_text())["sample_reading"] == SAMPLE_READING
        assert counted_under_current_rule(cls.load(path))


@pytest.mark.parametrize("d,expect", [
    ({}, None),                                    # saved before the field existed
    ({"sample_reading": SAMPLE_READING - 1}, SAMPLE_READING - 1),
    ({"sample_reading": True}, None),              # a flag, not a reading
    ({"sample_reading": "2"}, None),               # a spelling, not a reading
    ("not a dict", None),
])
def test_the_reading_is_read_only_off_an_integer(d, expect):
    assert reading_of(d) == expect


def test_an_older_or_absent_reading_is_not_current():
    for cls in (cc.ConfidenceCalibrator, vw.VoterWeightLearner):
        assert not counted_under_current_rule(cls().load_dict({}))
        assert not counted_under_current_rule(cls().load_dict({"sample_reading": SAMPLE_READING - 1}))
        assert counted_under_current_rule(cls().load_dict({"sample_reading": SAMPLE_READING}))


# ── refit_stale ─────────────────────────────────────────────────────────────

def test_a_stale_fit_is_refit_and_the_analyzer_reloads_it():
    _plant(cc._CAL_FILE, _stale_cal())
    _plant(vw._FILE, _stale_vw())
    seen = []
    analyzer = SimpleNamespace(refresh_calibrator=lambda: seen.append("refresh"))
    refit = auto_refit.refit_stale(analyzer)
    assert refit == ["confidence calibration", "voter weights"]
    assert seen == ["refresh"]
    # Refit over an empty record: the curve is identity, under the current rule,
    # and the stale curve's forty samples are gone with it.
    cal = cc.ConfidenceCalibrator.load()
    assert counted_under_current_rule(cal) and not cal.is_ready() and cal._n_samples == 0
    assert counted_under_current_rule(vw.VoterWeightLearner.load())


def test_a_current_fit_is_left_alone(monkeypatch):
    cc.ConfidenceCalibrator().save()
    vw.VoterWeightLearner().save()
    # RECORDED, not raised: `refit_stale` is fail-open per learner, so a
    # planted refit that raised would be caught and logged, and the test could
    # not tell a refit that was never called from one that was swallowed.
    called = []
    monkeypatch.setattr(cc, "refit_and_save", lambda *a, **k: called.append("cal"))
    monkeypatch.setattr(vw, "refit_and_save", lambda *a, **k: called.append("vw"))
    assert auto_refit.refit_stale() == [] and called == []


def test_no_fit_on_disk_is_not_made_here():
    assert auto_refit.refit_stale() == []
    assert not pathlib.Path(cc._CAL_FILE).exists()
    assert not pathlib.Path(vw._FILE).exists()


def test_one_learner_failing_does_not_stop_the_other(monkeypatch):
    _plant(vw._FILE, _stale_vw())

    def boom(*a, **k):
        raise OSError("planted")
    monkeypatch.setattr(cc.ConfidenceCalibrator, "load", classmethod(boom))
    assert auto_refit.refit_stale() == ["voter weights"]


# ── the bot runs it, once, at the start of its loop ─────────────────────────

def _engine(*, detached=False):
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.analyzer = SimpleNamespace(refresh_calibrator=lambda: None)
    if detached:
        eng._state_persistence_detached = True
    return eng


def _with_flag(on):
    from bot.config import CONFIG
    object.__setattr__(CONFIG.analyzer, "learning_auto_refit_enabled", on)


@pytest.fixture
def _restore_flag():
    from bot.config import CONFIG
    was = CONFIG.analyzer.learning_auto_refit_enabled
    try:
        yield
    finally:
        object.__setattr__(CONFIG.analyzer, "learning_auto_refit_enabled", was)


def test_the_bot_refits_a_stale_fit_at_start(_restore_flag):
    _with_flag(True)
    _plant(cc._CAL_FILE, _stale_cal())
    assert asyncio.run(_engine()._refit_stale_learned_curves()) == ["confidence calibration"]
    assert counted_under_current_rule(cc.ConfidenceCalibrator.load())


def test_a_reader_engine_refits_nothing(_restore_flag):
    _with_flag(True)
    _plant(cc._CAL_FILE, _stale_cal())
    assert asyncio.run(_engine(detached=True)._refit_stale_learned_curves()) == []
    assert not counted_under_current_rule(cc.ConfidenceCalibrator.load())


def test_with_auto_refit_off_nothing_is_refit(_restore_flag):
    _with_flag(False)
    _plant(cc._CAL_FILE, _stale_cal())
    assert asyncio.run(_engine()._refit_stale_learned_curves()) == []
    assert not counted_under_current_rule(cc.ConfidenceCalibrator.load())


def test_the_loop_awaits_it_before_anything_else_runs():
    # `run` starts a websocket, a dashboard pusher and reconciliation against a
    # venue, so it is not driven here; that it CALLS the seam, first, is a
    # shape, and the seam itself is driven above.
    tree = ast.parse((ROOT / "bot" / "core" / "engine.py").read_text(encoding="utf-8"))
    run = next(n for n in ast.walk(tree)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "run"
               and any(isinstance(a, ast.arg) and a.arg == "self" for a in n.args.args)
               and len(n.args.args) == 1)
    calls = [n for n in ast.walk(run) if isinstance(n, ast.Await)
             and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)]
    names = [c.value.func.attr for c in sorted(calls, key=lambda c: c.lineno)]
    assert names and names[0] == "_refit_stale_learned_curves", names


# ── the readiness card ──────────────────────────────────────────────────────

class _Store:
    def get_decisions(self, symbol=None, limit=100):
        return []


def _calibration():
    from bot.learning import readiness as rd
    return rd.assess_readiness(store=_Store())["components"]["calibration"]


def test_the_card_does_not_lend_a_stale_fits_count_to_the_record():
    _plant(cc._CAL_FILE, _stale_cal())
    comp = _calibration()
    assert comp["samples"] == 0
    assert "40 samples counted under an older rule" in comp["note"]
    assert "/calibration refit" in comp["note"]


def test_a_current_fits_count_is_shown_and_nothing_is_said():
    d = _stale_cal()
    d["sample_reading"] = SAMPLE_READING
    _plant(cc._CAL_FILE, d)
    comp = _calibration()
    assert comp["samples"] == 40
    assert "older rule" not in (comp.get("note") or "")


# ── what auto_refit says about itself ───────────────────────────────────────

def test_the_module_states_each_flags_real_default():
    # A docstring is outside the flag-comment rule by design, so this one is
    # pinned by name: it is what a reader consults to decide whether leaving
    # auto-refit on can change a live decision, and it used to say it could not.
    import re
    doc = ast.get_docstring(ast.parse(pathlib.Path(auto_refit.__file__).read_text()))
    claims = dict(re.findall(r"`([A-Z][A-Z_]+_ENABLED)`\s*\(default (ON|OFF)\)", doc))
    decl = {env: default for env, default in declared_defaults().values()}
    assert set(claims) == {"CONFIDENCE_CALIBRATION_ENABLED", "SETUP_EXPECTANCY_ENABLED",
                           "VOTER_WEIGHT_LEARNING_ENABLED", "LEARNING_AUTO_REFIT_ENABLED"}, claims
    for env, word in claims.items():
        assert decl[env] == (word == "ON"), (env, word, decl[env])
    assert "NEVER changes a trade" not in doc


def test_no_fit_on_disk_is_not_called_an_old_one():
    # A fresh install has no curve at all; "fitted under an older rule" would
    # describe a file that does not exist.
    assert not pathlib.Path(cc._CAL_FILE).exists()
    comp = _calibration()
    assert "older rule" not in (comp.get("note") or "")
