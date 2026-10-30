"""scripts/timeframes_study.py: the pre-registered screen, both arms."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _m():
    spec = importlib.util.spec_from_file_location(
        "timeframes_study", ROOT / "scripts" / "timeframes_study.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _folds(rets):
    return [{"oos_start": f"2026-0{i + 1}-01T00:00:00", "return_pct": r}
            for i, r in enumerate(rets)]


def test_a_profitable_run_in_both_halves_passes():
    j = _m().judge(_folds([1.0, 2.0, 0.5, 1.0, 0.2, -1.0]))
    assert j["passed"] is True
    assert j["disc_mean"] == pytest.approx(3.5 / 3)
    assert j["hold_mean"] == pytest.approx(0.2 / 3)
    assert j["worst_hold"] == -1.0


@pytest.mark.parametrize("rets, why", [
    ([-1.0, 0.5, 0.4, 1.0, 1.0, 1.0], "discovery mean not above zero"),
    ([1.0, 1.0, 1.0, -0.5, 0.2, 0.2], "holdout mean not above zero"),
    ([1.0, 1.0, 1.0, 9.0, 9.0, -5.0], "a holdout fold at the loss floor"),
])
def test_each_condition_alone_fails_the_screen(rets, why):
    assert _m().judge(_folds(rets))["passed"] is False, why


def test_the_halves_are_cut_by_time_not_by_file_order():
    folds = _folds([-3.0, -3.0, -3.0, 1.0, 1.0, 1.0])
    j = _m().judge(list(reversed(folds)))
    assert j["returns"] == [-3.0, -3.0, -3.0, 1.0, 1.0, 1.0]
    assert j["passed"] is False


def test_fewer_than_six_folds_is_no_verdict_not_a_pass():
    j = _m().judge(_folds([1.0, 1.0, 1.0, 1.0, 1.0]))
    assert j["passed"] is False
    assert j["disc_mean"] is None and j["hold_mean"] is None


def test_a_missing_run_is_reported_missing(tmp_path, capsys):
    m = _m()
    (tmp_path / "1h.json").write_text(json.dumps({"folds": []}))
    res = m.report(tmp_path)
    assert res["15m"] == {"missing": True}
    assert res["1h"]["n_folds"] == 0 and res["1h"]["passed"] is False
