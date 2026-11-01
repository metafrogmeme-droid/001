"""scripts/closer_target_benchmark.py: the sealed rules, every outcome."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETS = ["s1", "s2", "s3", "s4", "s5"]
BASE = [-1.0, -2.0, -1.5, -1.0, -2.0, -1.0]


def _m():
    spec = importlib.util.spec_from_file_location(
        "closer_target_benchmark", ROOT / "scripts" / "closer_target_benchmark.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_bound_is_split_over_the_two_arms():
    assert _m().LOWER_PCT == 1.25


def test_a_reliable_gain_on_most_sets_helps_and_its_mirror_hurts():
    m = _m()
    b = {s: list(BASE) for s in SETS}
    up = {s: [v + 1.0 for v in BASE] for s in SETS}
    assert m.target_verdict(b, up)["verdict"] == "the closer target helps"
    assert m.target_verdict(up, b)["verdict"] == "the closer target hurts"


def test_a_gain_on_three_sets_is_no_verdict():
    m = _m()
    b = {s: [0.0] * 6 for s in SETS}
    t = {s: ([3.0] * 6 if s in ("s1", "s2", "s3") else [-0.1] * 6) for s in SETS}
    v = m.target_verdict(b, t)
    assert v["mean_diff"] > 0 and v["sets_better"] == 3 and v["verdict"] == "no verdict"


def test_noise_is_no_verdict():
    m = _m()
    b = {s: [0.0] * 6 for s in SETS}
    t = {s: [3.0, -3.1, 2.9, -3.0, 3.1, -2.8] for s in SETS}
    assert m.target_verdict(b, t)["verdict"] == "no verdict"


def test_unequal_folds_are_no_verdict():
    m = _m()
    b = {s: [0.0] * 6 for s in SETS}
    t = dict(b, s1=[0.0] * 5)
    assert m.target_verdict(b, t)["verdict"].startswith("no verdict")


def test_money_needs_the_mean_its_bound_and_most_sets():
    m = _m()
    assert m.money_verdict({s: [1.0, 1.2, 0.9, 1.1, 1.0, 0.8] for s in SETS})["verdict"] \
        == "makes money"
    loses = {s: [v for v in BASE] for s in SETS}
    assert m.money_verdict(loses)["verdict"] == "does not show it makes money"
    noisy = {s: [3.0, -2.9, 3.1, -3.0, 2.8, -2.9] for s in SETS}
    assert m.money_verdict(noisy)["verdict"] == "does not show it makes money"


def test_the_exit_mix_counts_an_unrecorded_reason_apart():
    m = _m()
    assert m.exit_mix([{"exit_reason": "tp"}, {"exit_reason": "tp"}, {}]) == \
        {"tp": 2, "unrecorded": 1}
