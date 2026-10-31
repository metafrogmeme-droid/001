"""scripts/live_history_benchmark.py: the sealed Q2 rule, all three outcomes."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _m():
    spec = importlib.util.spec_from_file_location(
        "live_history_benchmark", ROOT / "scripts" / "live_history_benchmark.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SETS = ["s1", "s2", "s3", "s4", "s5"]


def test_the_gate_costs_money_when_removing_it_wins_reliably_on_most_sets():
    m = _m()
    b = {s: [-1.0, -2.0, -1.5, -1.0, -2.0, -1.0] for s in SETS}
    c = {s: [v + 1.0 for v in b[s]] for s in SETS}
    assert m.gate_verdict(b, c)["verdict"] == "the gate costs money"


def test_the_gate_earns_its_place_in_the_mirror_case():
    m = _m()
    c = {s: [-1.0, -2.0, -1.5, -1.0, -2.0, -1.0] for s in SETS}
    b = {s: [v + 1.0 for v in c[s]] for s in SETS}
    assert m.gate_verdict(b, c)["verdict"] == "the gate earns its place"


def test_a_win_on_too_few_sets_is_no_verdict():
    m = _m()
    b = {s: [0.0] * 6 for s in SETS}
    c = {s: ([3.0] * 6 if s in ("s1", "s2", "s3") else [-0.1] * 6) for s in SETS}
    v = m.gate_verdict(b, c)
    assert v["mean_c_minus_b"] > 0 and v["sets_c_better"] == 3 and v["verdict"] == "no verdict"


def test_noise_is_no_verdict():
    m = _m()
    b = {s: [0.0] * 6 for s in SETS}
    c = {s: [3.0, -3.1, 2.9, -3.0, 3.1, -2.8] for s in SETS}
    assert m.gate_verdict(b, c)["verdict"] == "no verdict"
