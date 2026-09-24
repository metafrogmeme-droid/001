"""The signal-edge instrument's pure pieces, driven on planted bars.

`scripts/signal_edge.py` is how `docs/FROZEN_BENCHMARK.md`'s signal-direction
figures are produced, so its arithmetic is a claim like any other. Each rule
below has a planted input that separates it from the wrong version: a SHORT
move signed like a LONG, a horizon past the data read as a zero, overlapping
windows counted twice, and the direction's unconditional drift left in.
"""
from __future__ import annotations

import importlib.util
import json
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


# ── continuation: what a hold limit between two horizons forgoes ─────────


def _cont_row(moves, unc, sym="S", i=0, cluster=0):
    base = {"dataset": "d", "symbol": sym, "i": i, "direction": "LONG",
            "moves": moves, "cluster": [cluster]}
    return se.with_excess([base], {"LONG": unc})[0]


def test_continuation_is_the_excess_over_the_segment_alone():
    # Moved +1 by bar 12 and +4 by bar 48, against a drift of +0.5 and +1.5:
    # over 12 -> 48 it moved 3 while the market drifted 1.
    r = _cont_row({12: 1.0, 48: 4.0}, {12: 0.5, 48: 1.5})
    (got,) = se.continuation([r], 12, 48)
    assert got["excess"] == {48: pytest.approx(2.0)}


def test_the_in_profit_filter_reads_the_raw_move_not_the_excess():
    # A trade's profit is the raw move. This idea is in profit at bar 12
    # (+0.5) while lagging a +0.8 drift, and the other is losing (-0.1)
    # while beating a -0.6 drift: only the first is still being carried.
    carried = _cont_row({12: 0.5, 48: 1.0}, {12: 0.8, 48: 0.8}, sym="A")
    losing = _cont_row({12: -0.1, 48: 1.0}, {12: -0.6, 48: -0.6}, sym="B")
    kept = se.continuation([carried, losing], 12, 48, min_move=0.0)
    assert [r["symbol"] for r in kept] == ["A"]


def test_a_move_exactly_at_the_floor_is_not_above_it():
    r = _cont_row({12: 0.0, 48: 1.0}, {12: 0.0, 48: 0.0})
    assert se.continuation([r], 12, 48, min_move=0.0) == []
    assert len(se.continuation([r], 12, 48)) == 1


def test_a_missing_horizon_drops_the_idea_rather_than_reading_zero():
    short = _cont_row({12: 1.0}, {12: 0.0, 48: 0.0})
    assert se.continuation([short], 12, 48) == []


def _cont_file(tmp_path, name, rows):
    data = {"dataset": name, "ideas": len(rows), "unplaced": 0,
            "unconditional": {"LONG": {"12": 0.0, "48": 0.0},
                              "SHORT": {"12": 0.0, "48": 0.0}},
            "rows": rows}
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(data))
    return str(p)


def _file_rows(name, sig, n, m12, m48, prefix="S"):
    return [{"dataset": name, "symbol": f"{prefix}{k}", "i": 0, "ts": "2026-01-01T00:00:00",
             "direction": "LONG", "signal_type": sig, "confidence": 0.6,
             "approved": False, "moves": {"12": m12, "48": m48},
             "cluster": [name, f"{prefix}{k}", 2026, 1]} for k in range(n)]


def test_the_report_filters_by_signal_and_pools_only_across_files(tmp_path):
    a = _cont_file(tmp_path, "a", _file_rows("a", "momentum_confluence", 6, 1.0, 3.0)
                   # other symbols, or de-overlapping would drop them before
                   # the signal filter is ever asked about them
                   + _file_rows("a", "volume_spike", 6, 1.0, -5.0, prefix="V"))
    b = _cont_file(tmp_path, "b", _file_rows("b", "momentum_confluence", 6, -1.0, 0.0))
    one = se.continuation_report([a], 12, 48, "momentum_confluence")
    assert "POOLED" not in one
    # every momentum idea on file a moved +2 over the segment; the
    # volume_spike rows, at -6, are not in it.
    assert "+2.00 [+2.00,+2.00]" in one and "-6.00" not in one
    both = se.continuation_report([a, b], 12, 48, "momentum_confluence")
    pooled = both.split("POOLED (all files given)")[1].splitlines()
    every, favour = pooled[1], pooled[2]
    assert every.split()[2] == "12" and "+1.50" in every
    # file b's ideas were losing at bar 12: the in-favour subset is file a's.
    assert favour.split()[5] == "6" and "+2.00" in favour


def test_the_report_de_overlaps_before_it_measures(tmp_path):
    rows = _file_rows("a", "momentum_confluence", 6, 1.0, 3.0)
    for k, r in enumerate(rows):
        r["symbol"], r["i"], r["cluster"] = "S", k, ["a", "S", 2026, k]
    out = se.continuation_report([_cont_file(tmp_path, "a", rows)], 12, 48)
    # six ideas one bar apart share one 48-bar window: one is kept.
    assert out.splitlines()[4].split()[2] == "1"


def test_the_cli_refuses_horizons_it_did_not_record(tmp_path, capsys):
    f = _cont_file(tmp_path, "a", _file_rows("a", "momentum_confluence", 6, 1.0, 3.0))
    for bad in (["--from", "16"], ["--from", "48", "--to", "12"], ["--from", "12", "--to", "12"]):
        with pytest.raises(SystemExit):
            se.main(["continuation", *bad, f])
    assert se.main(["continuation", f]) == 0
    assert "bar 12 -> 48" in capsys.readouterr().out
