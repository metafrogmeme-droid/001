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


# ── per voter: which parts of the electorate carry the direction ─────────


def _call(votes, moves, week=1, sym="S", ds="d"):
    return {"dataset": ds, "symbol": sym, "i": 0, "ts": "2026-01-01T00:00:00",
            "votes": votes, "moves": moves, "week": [ds, 2026, week]}


_FLAT = {"LONG": {1: 0.0, 24: 0.0}, "SHORT": {1: 0.0, 24: 0.0}}


def test_a_vote_is_measured_in_the_direction_it_voted():
    # One call moved +2 ATR up. The voter that voted up is right by +2; the
    # voter that voted down is wrong by -2, whatever the size of its vote.
    sums = se.voter_sums([_call({"up": 0.3, "down": -1.0}, {24: 2.0})], _FLAT)
    assert sums["up"][24][("d", 2026, 1)] == [2.0, 1]
    assert sums["down"][24][("d", 2026, 1)] == [-2.0, 1]


def test_the_votes_direction_drift_is_subtracted():
    unc = {"LONG": {24: 0.5}, "SHORT": {24: -0.5}}
    sums = se.voter_sums([_call({"up": 1.0, "down": -1.0}, {24: 2.0})], unc)
    assert sums["up"][24][("d", 2026, 1)] == [1.5, 1]      # +2 - (+0.5)
    assert sums["down"][24][("d", 2026, 1)] == [-1.5, 1]   # -2 - (-0.5)


def test_a_horizon_past_the_data_or_the_baseline_is_absent_not_zero():
    sums = se.voter_sums([_call({"v": 1.0}, {1: 1.0, 24: 3.0})],
                         {"LONG": {1: 0.0}, "SHORT": {1: 0.0}})
    assert 24 not in sums["v"]
    sums = se.voter_sums([_call({"v": 1.0}, {1: 1.0})], _FLAT)
    assert 24 not in sums["v"] and sums["v"][1][("d", 2026, 1)] == [1.0, 1]


def test_symbols_voted_in_one_week_are_one_cluster():
    # Ten symbols moving together in one week are one market move, not ten
    # independent samples: they land in ONE cluster.
    calls = [_call({"v": 1.0}, {24: 1.0}, sym=f"S{k}") for k in range(10)]
    sums = se.voter_sums(calls, _FLAT)
    assert list(sums["v"][24]) == [("d", 2026, 1)]
    assert sums["v"][24][("d", 2026, 1)] == [10.0, 10]


def test_the_sums_bootstrap_is_the_rows_bootstrap():
    # One interval function, whatever the rows were: the idea table's
    # cluster_ci and the voter table's sums must agree on the same data.
    rows = _cl_rows({k: [k * 0.1, k * 0.1 + 0.3] for k in range(12)})
    sums = {}
    for r in rows:
        acc = sums.setdefault(tuple(r["cluster"]), [0.0, 0])
        acc[0] += r["excess"][24]
        acc[1] += 1
    assert se.cluster_ci_sums(sums) == se.cluster_ci(rows, 24)


def test_a_wider_level_widens_the_interval():
    rows = _cl_rows({k: [k * 0.1, k * 0.1 + 0.3] for k in range(12)})
    _n, _c, m95, lo95, hi95 = se.cluster_ci(rows, 24)
    _n, _c, m99, lo99, hi99 = se.cluster_ci(rows, 24, level=0.99)
    assert m95 == m99 and lo99 < lo95 and hi99 > hi95


def _voter_file(tmp_path, name, calls, unc=None):
    unc = unc or {"LONG": {str(h): 0.0 for h in se.HORIZONS},
                  "SHORT": {str(h): 0.0 for h in se.HORIZONS}}
    data = {"dataset": name, "ideas": 0, "unplaced": 0, "rows": [],
            "unconditional": unc, "calls": calls, "calls_unconditional": unc,
            "calls_unplaced": 0, "calls_repeated": 0, "since": None}
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(data))
    return str(p)


def _weeks(name, votes, move24, weeks=6):
    # a spread around move24 whose mean is move24 exactly, so an interval exists
    return [_call(votes, {"24": move24 + 0.02 * (w - 2.5)}, week=w, ds=name)
            for w in range(weeks)]


def test_the_voter_report_ranks_by_h24_and_shows_each_file(tmp_path):
    a = _voter_file(tmp_path, "a", _weeks("a", {"good": 1.0, "bad": 1.0}, 1.0)
                    + _weeks("a", {"bad": -1.0}, 1.0))
    b = _voter_file(tmp_path, "b", _weeks("b", {"good": -1.0}, -2.0))
    out = se.voters_report([a, b])
    lines = [ln for ln in out.splitlines() if ln.startswith(("good", "bad"))]
    assert [ln.split()[0] for ln in lines] == ["good", "bad"]
    good, bad = lines
    # good was right on both files: +1 on a and +2 on b, pooled over 12 weeks
    assert good.split()[1] == "12" and good.split()[-2:] == ["+1.00", "+2.00"]
    # bad voted up then down on the same up-moves: the two cancel on file a,
    # and it never voted on b
    assert bad.split()[1] == "12" and bad.split()[-2:] == ["+0.00", "-"]


def test_a_named_voter_that_never_voted_is_said_not_dropped(tmp_path):
    a = _voter_file(tmp_path, "a", _weeks("a", {"good": 1.0}, 1.0))
    out = se.voters_report([a], only=["good", "ghost"])
    assert "ghost" in out and "never voted" in out


def test_a_file_without_calls_is_refused_not_read_as_empty(tmp_path):
    f = _cont_file(tmp_path, "old", _file_rows("old", "momentum_confluence", 6, 1.0, 3.0))
    with pytest.raises(SystemExit, match="collect it again"):
        se.voters_report([f])


def test_the_voters_cli_refuses_a_level_that_is_not_a_coverage(tmp_path, capsys):
    a = _voter_file(tmp_path, "a", _weeks("a", {"good": 1.0}, 1.0))
    for bad in ("0.3", "1.0", "95"):
        with pytest.raises(SystemExit):
            se.main(["voters", "--level", bad, a])
    assert se.main(["voters", "--level", "0.99", a]) == 0
    assert "99% week-cluster bootstrap" in capsys.readouterr().out


# ── collect's taps, driven with a stand-in analyzer and planted bars ─────


def test_collect_records_every_calls_electorate(monkeypatch):
    import asyncio
    from datetime import timezone

    from bot.backtest import runner, snapshot
    from bot.core.analyzer import Analyzer
    from bot.risk import risk_engine as rk

    t0 = datetime(2026, 1, 5, tzinfo=timezone.utc)   # a Monday
    closes = [100.0 + k for k in range(80)]
    bars = []
    prev = closes[0]
    for k, c in enumerate(closes):
        bars.append(SimpleNamespace(timestamp=t0 + timedelta(hours=k), close=c,
                                    high=max(c, prev) + 1.0, low=min(c, prev) - 1.0))
        prev = c
    monkeypatch.setattr(snapshot, "load_dataset", lambda _d: {"S": bars})

    planted = [("rsi", 1.0, 1.5), ("macd", 0.0, 1.0), ("vwap", -0.5, 0.0), ("ema", -1.0, 1.0)]

    def fake_score(*_a, breakdown=None, **_k):
        if breakdown is not None:
            breakdown.extend(planted)
        return 0.5

    async def fake_analyze(self, signal, candles, *a, **k):
        # the real analyzer hands its own list in; a second scoring without
        # one must be captured too, but only the first reading of a bar kept
        mine: list = [("already", 1.0, 1.0)]
        Analyzer._score_confluence({}, None, signal, breakdown=mine)
        Analyzer._score_confluence({}, None, signal)
        return None

    monkeypatch.setattr(Analyzer, "_score_confluence", staticmethod(fake_score))
    monkeypatch.setattr(Analyzer, "analyze", fake_analyze)
    real_score = Analyzer._score_confluence

    def fake_main():
        async def go():
            for h in (20, 20, 30):     # bar 20 twice: its first reading is kept
                await Analyzer.analyze(object(), SimpleNamespace(symbol="S"), [],
                                       as_of=bars[h].timestamp)
        asyncio.run(go())

    monkeypatch.setattr(runner, "main", fake_main)
    data = se.collect("somewhere/planted")
    assert Analyzer._score_confluence is real_score        # restored
    assert Analyzer.analyze is fake_analyze
    assert rk.RiskEngine.evaluate.__name__ != "_tap_eval"

    calls = {c["i"]: c for c in data["calls"]}
    assert sorted(calls) == [20, 30]
    # the caller's own earlier entry is not this call's vote, an abstention
    # (0) and a voter with no weight cast no vote
    assert calls[20]["votes"] == {"rsi": 1.0, "ema": -1.0}
    # every scoring after a bar's first is a repeat: bar 20 was analysed twice
    # and scored twice each time (3), bar 30 scored twice once (1)
    assert data["calls_repeated"] == 4
    assert calls[20]["moves"][24] == pytest.approx(24 / 3.0)   # +24 over ATR 3
    assert calls[20]["week"] == ["planted", 2026, 2]

    later = se.collect("somewhere/planted", since=bars[25].timestamp)
    assert [c["i"] for c in later["calls"]] == [30]
