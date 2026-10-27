"""The research harness measures what it says, and cannot see the future."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import signal_research as sr  # noqa: E402


def _bars(n=900, seed=1):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.004, n)
    c = 100 * np.exp(np.cumsum(r))
    o = np.concatenate(([c[0]], c[:-1]))
    spread = np.abs(rng.normal(0, 0.002, n)) * c
    return o, np.maximum(o, c) + spread, np.minimum(o, c) - spread, c, rng.uniform(1e3, 2e3, n)


def test_features_never_use_a_later_bar():
    o, h, lo, c, v = _bars()
    full = sr.features(o, h, lo, c, v)
    cut = 600
    part = sr.features(o[:cut], h[:cut], lo[:cut], c[:cut], v[:cut])
    for name, arr in full.items():
        a, b = arr[:cut], part[name]
        both = np.isfinite(a) & np.isfinite(b)
        assert np.allclose(a[both], b[both], rtol=1e-9, atol=1e-12), name
        assert (np.isfinite(a) == np.isfinite(b)).all(), name


def test_sums_give_the_same_correlation_as_numpy():
    rng = np.random.default_rng(2)
    x, y = rng.normal(size=300), rng.normal(size=300)
    y = y + 0.5 * x
    ux, uy = sr.uniform_ranks(x), sr.uniform_ranks(y)
    row = sr.cluster_row(ux, uy, y)
    assert sr.pearson(row) == pytest.approx(np.corrcoef(ux, uy)[0, 1], abs=1e-9)


def test_ties_share_their_mean_rank():
    u = sr.uniform_ranks(np.array([1.0, 2.0, 2.0, 3.0]))
    assert u[1] == u[2] and u[0] < u[1] < u[3]


def test_unmeasurable_is_nan_never_zero():
    assert np.isnan(sr.pearson(np.zeros(sr._COLS)))
    assert np.isnan(sr.spread(np.zeros(sr._COLS)))
    assert np.isnan(sr.interval(np.array([np.nan] * 100))[0])


def test_a_planted_signal_is_found_and_noise_is_not():
    rng = np.random.default_rng(3)
    k, per = 120, 40
    rows_sig, rows_noise = [], []
    for _ in range(k):
        x = rng.normal(size=per)
        y = 0.3 * x + rng.normal(size=per)
        z = rng.normal(size=per)
        ux = sr.uniform_ranks(x)
        rows_sig.append(sr.cluster_row(ux, sr.uniform_ranks(y), y))
        rows_noise.append(sr.cluster_row(sr.uniform_ranks(z), sr.uniform_ranks(y), y))
    boot = np.random.default_rng(1)
    lo, hi, p = sr.interval(sr.bootstrap(np.vstack(rows_sig), sr.pearson, 500, boot))
    assert lo > 0 and p < 0.05
    lo, hi, p = sr.interval(sr.bootstrap(np.vstack(rows_noise), sr.pearson, 500, boot))
    assert lo < 0 < hi


def test_benjamini_hochberg_keeps_the_small_p_values_only():
    keep = sr.bh_pass([0.001, 0.002, 0.8, 0.9, float("nan")], 0.10)
    assert keep == [True, True, False, False, False]


def test_a_forward_window_never_crosses_the_discovery_cut():
    ts = np.arange(0, 500) * 3600.0
    s = {"ts": ts}
    cut = ts[300]
    for h in sr.HORIZONS:
        pos = sr.eligible(s, h, "discovery", cut)
        assert (ts[pos + h] < cut).all() and len(pos) > 0
        assert np.diff(pos).min() == h
        hold = sr.eligible(s, h, "holdout", cut)
        assert (ts[hold] >= cut).all()
