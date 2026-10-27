"""The sweep judges an arm by the pre-registered rule and by nothing else."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import risk_sweep as rs  # noqa: E402


def _run(folds):
    return {"folds": [{"fold": i, "oos_start": s, "oos_end": e, "return_pct": r,
                       "max_dd_pct": dd} for i, (s, e, r, dd) in enumerate(folds)]}


def test_windows_keep_only_folds_inside_them():
    run = _run([("2025-09-01 00:00:00+00:00", "2026-05-17 00:00:00+00:00", 1.0, 5.0),
                ("2026-05-17 00:00:00+00:00", "2026-07-06 00:00:00+00:00", 2.0, 5.0),
                ("2026-07-11 00:00:00+00:00", "2026-07-23 00:00:00+00:00", 3.0, 5.0)])
    assert sorted(rs.window_folds(run, "discovery")) == [0]
    assert sorted(rs.window_folds(run, "holdout")) == [2]
    assert sorted(rs.window_folds(run, "all")) == [0, 1, 2]


def test_paired_is_arm_minus_baseline_and_skips_unshared_folds():
    d = ("2025-09-01 00:00:00+00:00", "2026-01-01 00:00:00+00:00")
    base = _run([(*d, -5.0, 8.0), (*d, -3.0, 6.0)])
    arm = _run([(*d, -2.0, 7.0)])
    rows = rs.paired(base, arm, "discovery")
    assert len(rows) == 1 and rows[0]["d_ret"] == 3.0 and rows[0]["d_dd"] == -1.0


def _rows(deltas, dd_arm=4.0, dd_base=5.0, start=0):
    return [{"fold": start + i, "d_ret": x, "d_dd": dd_arm - dd_base,
             "dd_arm": dd_arm, "dd_base": dd_base} for i, x in enumerate(deltas)]


def _per(disc_gain, hold_gain, dd_arm=4.0):
    return {"a": ("discovery", _rows([disc_gain] * 5)), "b": ("discovery", _rows([disc_gain] * 5)),
            "c": ("holdout", _rows([hold_gain] * 6, dd_arm=dd_arm)),
            "d": ("holdout", _rows([hold_gain] * 6, dd_arm=dd_arm))}


def test_a_consistent_gain_with_less_drawdown_passes():
    assert rs.judge_stage1(_per(2.0, 2.0), boot=500)["passed"] is True


def test_each_condition_alone_can_fail_it():
    assert rs.judge_stage1(_per(0.5, 2.0), boot=500)["rule1"] is False      # too small somewhere
    assert rs.judge_stage1(_per(2.0, 2.0, dd_arm=9.0), boot=500)["rule2"] is False  # holdout drawdown worse
    mixed = {"a": ("discovery", _rows([3.0, -3.0, 2.0, -2.0, 0.1])),
             "b": ("holdout", _rows([3.0, -3.0, 2.0, -2.0, 0.1, 0.0]))}
    assert rs.judge_stage1(mixed, boot=500)["rule3"] is False                # interval spans zero


def test_a_missing_measurement_is_nan_and_never_a_pass():
    j = rs.judge_stage1({"a": ("discovery", []), "c": ("holdout", [])}, boot=100)
    assert np.isnan(j["holdout_mean"]) and j["passed"] is False


def test_the_arm_list_is_twelve_and_matches_the_registration():
    text = (Path(__file__).resolve().parent.parent / "docs" / "research" / "prereg"
            / "risk_settings_2026-10" / "PREREGISTRATION.md").read_text()
    assert len(rs.ARMS) == 12
    for arm, env in rs.ARMS.items():
        assert f"| {arm} |" in text
        for key in env:
            tail = key.split("_", 1)[1] if key.startswith(
                ("SCALP_", "INTRADAY_", "SWING_", "POSITION_")) else key
            assert tail in text, (arm, key)
