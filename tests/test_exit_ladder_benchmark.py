"""scripts/exit_ladder_benchmark.py: the sealed rules, every outcome, and the
manipulation checks that stop a verdict on an arm that changed nothing."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETS = ["s1", "s2", "s3", "s4", "s5"]
BASE = [-1.0, -2.0, -1.5, -1.0, -2.0, -1.0]


def _m():
    spec = importlib.util.spec_from_file_location(
        "exit_ladder_benchmark", ROOT / "scripts" / "exit_ladder_benchmark.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_bound_is_split_over_three_comparisons():
    assert abs(_m().LOWER_PCT - 2.5 / 3) < 1e-12


def test_a_reliable_gain_helps_only_when_the_fresh_set_agrees():
    m = _m()
    b = {s: list(BASE) for s in SETS}
    up = {s: [v + 1.0 for v in BASE] for s in SETS}
    assert m.compare(b, up, 0.5)["verdict"] == "helps"
    assert m.compare(b, up, -0.5)["verdict"] == "no verdict: the confirmation set disagrees"
    assert m.compare(b, up, None)["verdict"].startswith("no verdict")
    assert m.compare(up, b, -0.5)["verdict"] == "hurts"


def test_too_few_sets_or_noise_is_no_verdict():
    m = _m()
    b = {s: [0.0] * 6 for s in SETS}
    three = {s: ([3.0] * 6 if s in ("s1", "s2", "s3") else [-0.1] * 6) for s in SETS}
    assert m.compare(b, three, 1.0)["verdict"] == "no verdict"
    noise = {s: [3.0, -3.1, 2.9, -3.0, 3.1, -2.8] for s in SETS}
    assert m.compare(b, noise, 1.0)["verdict"] == "no verdict"


def test_money_needs_the_fresh_set_too():
    m = _m()
    good = {s: [1.0, 1.2, 0.9, 1.1, 1.0, 0.8] for s in SETS}
    assert m.money(good, 0.4)["verdict"] == "makes money"
    assert m.money(good, -0.1)["verdict"] == "does not show it makes money"
    assert m.money(good, None)["verdict"] == "does not show it makes money"
    assert m.money({s: list(BASE) for s in SETS}, 1.0)["verdict"] == "does not show it makes money"


def _row(sym, t, reason):
    return {"symbol": sym, "entry_time": t, "exit_reason": reason}


def test_the_ladder_check_needs_a_larger_tp1_share():
    m = _m()
    base = [_row("A", 1, "SL"), _row("B", 2, "TP1"), _row("B", 2, "TRAILING_SL")]
    same = [_row("A", 1, "SL"), _row("B", 2, "TP1"), _row("B", 2, "TRAILING_SL")]
    more = [_row("A", 1, "TP1"), _row("A", 1, "SL"), _row("B", 2, "TP1"), _row("B", 2, "SL")]
    assert m.ladder_moved(base, more) is True
    assert m.ladder_moved(base, same) is False
    assert m.ladder_moved([], more) is False


def test_the_entry_check_counts_filled_limits():
    m = _m()
    assert m.limits_filled({"folds": [{"limits": {"filled": 3}}, {"limits": {"filled": 0}}]}) == 3
    assert m.limits_filled({"folds": [{"limits": {"filled": 0}}, {}]}) == 0
