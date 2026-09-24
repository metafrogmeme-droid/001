"""The signal-edge instrument's pure pieces, driven on planted bars.

`scripts/signal_edge.py` is how `docs/FROZEN_BENCHMARK.md`'s signal-direction
figures are produced, so its arithmetic is a claim like any other. Each rule
below has a planted input that separates it from the wrong version: a SHORT
move signed like a LONG, a horizon past the data read as a zero, overlapping
windows counted twice, and the direction's unconditional drift left in.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("signal_edge", ROOT / "scripts" / "signal_edge.py")
se = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(se)

_T0 = datetime(2025, 1, 1)


def _bars(closes, rng=2.0):
    """Bars with a constant high-low range, so ATR is exactly `rng`."""
    out, prev = [], closes[0]
    for k, c in enumerate(closes):
        out.append(SimpleNamespace(timestamp=_T0 + timedelta(hours=k), close=c,
                                   high=max(c, prev) + rng / 2,
                                   low=min(c, prev) - rng / 2))
        prev = c
    return out


def test_atr_needs_its_history():
    bars = _bars([100.0] * 20)
    assert se.atr_at(bars, se.ATR_N - 1) is None
    assert se.atr_at(bars, se.ATR_N) == pytest.approx(2.0)


def test_a_range_that_never_moved_has_no_atr():
    flat = [SimpleNamespace(timestamp=_T0, close=1.0, high=1.0, low=1.0)] * 20
    assert se.atr_at(flat, 15) is None


def test_moves_are_signed_by_direction_in_atr_units():
    bars = _bars([100.0] * 15 + [104.0] * 10)
    long_ = se.signed_moves(bars, 14, "LONG", (1,))
    short = se.signed_moves(bars, 14, "SHORT", (1,))
    assert long_[1] == pytest.approx(4.0 / se.atr_at(bars, 14))
    assert short[1] == pytest.approx(-long_[1])


def test_a_horizon_past_the_data_is_absent_not_zero():
    bars = _bars([100.0] * 20)
    got = se.signed_moves(bars, 18, "LONG", (1, 4))
    assert 1 in got and 4 not in got


def test_deoverlap_keeps_no_two_windows_that_share_bars():
    rows = [{"dataset": "d", "symbol": "S", "i": i} for i in (0, 3, 5, 12, 13, 30)]
    kept = [r["i"] for r in se.deoverlap(rows, 12)]
    assert kept == [0, 12, 30]
    other = [{"dataset": "d", "symbol": "T", "i": 1}]
    assert [r["i"] for r in se.deoverlap(rows + other, 12)] == [0, 12, 30, 1]


def test_excess_subtracts_the_same_directions_drift():
    unc = {"LONG": {24: 0.5}, "SHORT": {24: -0.5}}
    rows = [{"direction": "LONG", "moves": {24: 0.5}},
            {"direction": "SHORT", "moves": {24: 0.0}}]
    got = [r["excess"][24] for r in se.with_excess(rows, unc)]
    # A LONG that merely matched the market's drift earned nothing; a SHORT
    # that stood still in a rising market beat its own direction's drift.
    assert got == [pytest.approx(0.0), pytest.approx(0.5)]


def test_a_horizon_with_no_baseline_is_absent_from_the_excess():
    rows = [{"direction": "LONG", "moves": {24: 1.0, 48: 2.0}}]
    assert se.with_excess(rows, {"LONG": {24: 0.0}})[0]["excess"] == {24: 1.0}


def test_the_unconditional_short_is_the_long_mirrored():
    bars = {"S": _bars([100.0 + k for k in range(40)])}
    unc = se.unconditional(bars, None, (4,))
    assert unc["LONG"][4] > 0
    assert unc["SHORT"][4] == pytest.approx(-unc["LONG"][4])


def _cl_rows(values_by_cluster):
    return [{"excess": {24: v}, "cluster": [c]} for c, vs in values_by_cluster.items() for v in vs]


def test_cluster_ci_refuses_under_five_clusters():
    assert se.cluster_ci(_cl_rows({k: [1.0] for k in range(4)}), 24) is None
    assert se.cluster_ci(_cl_rows({k: [1.0] for k in range(5)}), 24) is not None


def test_cluster_ci_is_deterministic_and_brackets_the_mean():
    rows = _cl_rows({k: [k * 0.1, k * 0.1 + 0.3] for k in range(12)})
    a, b = se.cluster_ci(rows, 24), se.cluster_ci(rows, 24)
    assert a == b
    n, clusters, mean, lo, hi = a
    assert (n, clusters) == (24, 12)
    assert lo <= mean <= hi and lo < hi


def test_cluster_ci_resamples_clusters_not_rows():
    # One cluster holding many identical rows cannot narrow the interval the
    # way independent rows would: resampled whole, it moves the mean together.
    many = _cl_rows({0: [5.0] * 50, **{k: [0.0] for k in range(1, 10)}})
    _n, _c, _m, lo, hi = se.cluster_ci(many, 24)
    naive = se.normal_ci([r["excess"][24] for r in many])
    assert (hi - lo) > (naive[3] - naive[2])


def test_normal_ci_refuses_under_three():
    assert se.normal_ci([1.0, 2.0]) is None
    n, mu, lo, hi = se.normal_ci([1.0, 2.0, 3.0])
    assert (n, mu) == (3, 2.0) and lo < mu < hi
