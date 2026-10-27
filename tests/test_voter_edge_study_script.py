"""The all-voter study measures excess over drift, by calendar week, and gates on the registered rule."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import voter_edge_study as ves  # noqa: E402

T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)


def _file(tmp_path, name, calls, unc=0.0):
    rows = [{"ts": (T0 + timedelta(hours=i)).isoformat(), "votes": v, "moves": {str(h): m for h, m in mv.items()}}
            for i, (v, mv) in enumerate(calls)]
    path = tmp_path / name
    path.write_text(json.dumps({"calls": rows, "calls_unconditional": {
        "LONG": {str(h): unc for h in ves.HORIZONS}, "SHORT": {str(h): -unc for h in ves.HORIZONS}}}))
    return str(path)


def _total(tmp_path, calls, unc):
    f = _file(tmp_path, "a.json", calls, unc=unc)
    s = ves.excess_sums(f, "discovery", datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp())
    return sum(v[0] for v in s["x"][1].values()), sum(v[1] for v in s["x"][1].values())


def test_a_long_vote_loses_the_long_drift(tmp_path):
    tot, n = _total(tmp_path, [({"x": 1.0}, {1: 0.5})] * 2, unc=0.1)
    assert n == 2 and abs(tot - 0.8) < 1e-9          # 2 x (0.5 - 0.1); no drift would give 1.0


def test_a_short_vote_is_scored_on_the_negated_move_against_the_short_drift(tmp_path):
    tot, n = _total(tmp_path, [({"x": -1.0}, {1: -0.5})] * 2, unc=0.1)
    assert n == 2 and abs(tot - 1.2) < 1e-9          # 2 x (0.5 - (-0.1)); not negated would give -1.0


def test_the_windows_split_on_the_cut(tmp_path):
    calls = [({"x": 1.0}, {1: 1.0})] * 10
    f = _file(tmp_path, "a.json", calls)
    cut = (T0 + timedelta(hours=4)).timestamp()
    d = ves.excess_sums(f, "discovery", cut)
    h = ves.excess_sums(f, "holdout", cut)
    nd = sum(v[1] for v in d["x"][1].values())
    nh = sum(v[1] for v in h["x"][1].values())
    assert nd == 4 and nh == 6


def test_the_same_week_is_one_cluster_across_files(tmp_path):
    a = _file(tmp_path, "a.json", [({"x": 1.0}, {1: 1.0})] * 3)
    b = _file(tmp_path, "b.json", [({"x": 1.0}, {1: 1.0})] * 3)
    cut = datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp()
    m = ves.merge([ves.excess_sums(a, "discovery", cut), ves.excess_sums(b, "discovery", cut)])
    assert list(m["x"][1]) == [ves.week_key(T0.isoformat())]
    assert m["x"][1][ves.week_key(T0.isoformat())][1] == 6


def test_bh_keeps_small_p_only_and_handles_nan():
    assert ves.bh_pass([0.001, 0.002, 0.9, float("nan")], 0.10) == [True, True, False, False]


def _weeks(mean, n_weeks, spread, seed=1):
    rng = np.random.default_rng(seed)
    return {f"w{i}": [float((mean + rng.normal(0, spread)) * 50), 50] for i in range(n_weeks)}


def test_a_planted_effect_that_replicates_passes_and_a_noise_voter_does_not():
    disc = {"real": {4: _weeks(0.30, 30, 0.1)}, "noise": {4: _weeks(0.0, 30, 0.3, 2)}}
    hold = {"real": {4: _weeks(0.25, 10, 0.1, 3)}, "noise": {4: _weeks(0.0, 10, 0.3, 4)}}
    res = {r["voter"]: r["verdict"] for r in ves.study(disc, hold, boot=600)}
    assert res["real"] == "PASS" and res["noise"] == "fail"


def test_an_effect_that_does_not_replicate_fails_and_a_wrong_way_one_is_harmful():
    disc = {"a": {4: _weeks(0.3, 30, 0.1)}, "b": {4: _weeks(-0.3, 30, 0.1, 5)}}
    hold = {"a": {4: _weeks(-0.3, 10, 0.1, 6)}, "b": {4: _weeks(-0.3, 10, 0.1, 7)}}
    res = {r["voter"]: r["verdict"] for r in ves.study(disc, hold, boot=600)}
    assert res["a"] == "fail" and res["b"] == "HARMFUL"


def test_an_underpowered_holdout_is_never_a_pass():
    disc = {"a": {4: _weeks(0.3, 30, 0.1)}}
    hold = {"a": {4: _weeks(0.3, 3, 0.1, 8)}}                 # 150 calls over 3 weeks
    assert ves.study(disc, hold, boot=600)[0]["verdict"] == "fail"
