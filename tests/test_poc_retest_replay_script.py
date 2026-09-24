"""The POC-retest replay, driven on planted bars and planted reads.

`scripts/poc_retest_replay.py` is how `docs/FROZEN_BENCHMARK.md`'s POC-retest
figures are produced, so what it arms and when is a claim like any other. The
rules below each have a planted input that separates them from the wrong
version: a 4h candle that had not closed yet read as closed, a setup armed on
a retest candle nobody could have seen at the time, a scoring window one bar
longer than the observer's, and a stale read armed as though it had been
fresh.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.core.poc_retest import PocRetestParams, RetestRead

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("poc_retest_replay",
                                               ROOT / "scripts" / "poc_retest_replay.py")
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)

H1 = 3_600_000
#: A Monday 00:00 UTC, so 4h groups start on the planted bar indices 0, 4, 8, ...
T0 = int(datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp() * 1000)
WIN = pr.ENTRY_BARS
P = PocRetestParams()


def _rows(n: int) -> list[list[float]]:
    """1h rows whose close is the bar's own index, so a window says where it ends."""
    return [[T0 + k * H1, float(k) - 0.25, float(k) + 0.5, float(k) - 0.5, float(k), 1.0]
            for k in range(n)]


class _Spy:
    """Stands in for `retest_state`, records what it was handed and answers a
    planted read per call."""

    def __init__(self, answer):
        self.calls: list[dict] = []
        self.answer = answer

    def __call__(self, h4h, h4l, h4c, h4v, h1h, h1l, h1c, params=None):
        self.calls.append({"h4c": list(h4c), "h1c": list(h1c)})
        return self.answer(len(self.calls), list(h1c))


# ── collect: what each hour's read is handed ──────────────────────────────


def test_each_hour_reads_only_4h_candles_closed_by_then(monkeypatch):
    spy = _Spy(lambda _n, _c: RetestRead("no_breakout", ""))
    monkeypatch.setattr(pr, "retest_state", spy)
    pr.replay_reads(_rows(WIN + 40), P)
    assert len(spy.calls) == 41
    for call in spy.calls:
        t = int(call["h1c"][-1])
        # a 4h candle's close is its last hour's close; the newest one handed
        # in is the last whose four hours had all closed by t's close -- the
        # candle t sits inside only when t is its fourth hour
        assert call["h4c"][-1] == 4 * ((t + 1) // 4) - 1
        assert len(call["h4c"]) <= pr.STRUCTURE_BARS


def test_each_hour_reads_the_last_entry_bars_ending_at_that_bar(monkeypatch):
    spy = _Spy(lambda _n, _c: RetestRead("no_breakout", ""))
    monkeypatch.setattr(pr, "retest_state", spy)
    rows = _rows(WIN + 3)
    states, _ = pr.replay_reads(rows, P)
    last = [c["h1c"] for c in spy.calls]
    assert len(last) == len(rows) - (WIN - 1) == sum(states.values())
    assert all(len(w) == WIN for w in last)
    assert [int(w[-1]) for w in last] == list(range(WIN - 1, len(rows)))


def test_the_first_read_already_has_the_4h_candles_the_leg_needs(monkeypatch):
    # why `read_setup`'s floor is not restated: it cannot bite here
    spy = _Spy(lambda _n, _c: RetestRead("no_breakout", ""))
    monkeypatch.setattr(pr, "retest_state", spy)
    pr.replay_reads(_rows(WIN), P)
    assert len(spy.calls[0]["h4c"]) >= 2 * P.swing_order + 2


def test_a_confirmed_read_records_which_bar_it_retested_on(monkeypatch):
    def answer(n, c):
        # the second read confirms on its own last bar, the third on a bar two back
        idx = {2: len(c) - 1, 3: len(c) - 3}.get(n)
        if idx is None:
            return RetestRead("no_breakout", "")
        return RetestRead("confirmed", "", side="long", retest_index=idx,
                          entry=1.0, stop=0.5, target=2.0, atr=0.5)
    spy = _Spy(answer)
    monkeypatch.setattr(pr, "retest_state", spy)
    rows = _rows(4 * (2 * P.swing_order + 2) + WIN + 5)
    _states, confirmed = pr.replay_reads(rows, P)
    t2, t3 = int(spy.calls[1]["h1c"][-1]), int(spy.calls[2]["h1c"][-1])
    assert [(c["t"], c["retest_t"]) for c in confirmed] == [(t2, t2), (t3, t3 - 2)]


def test_collect_covers_every_read_combo_with_the_bars(monkeypatch):
    from bot.backtest import snapshot
    bars = [SimpleNamespace(timestamp=datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc),
                            open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in _rows(200)]
    monkeypatch.setattr(snapshot, "load_dataset", lambda _d: {"S": bars})
    monkeypatch.setattr(pr, "retest_state", _Spy(lambda _n, _c: RetestRead("no_breakout", "")))
    data = pr.collect("somewhere/planted")
    assert data["dataset"] == "planted" and data["entry_bars"] == WIN
    assert sorted(data["reads"]) == sorted(
        f"{b:g}/{w}" for b in pr.ATR_BUFFERS for w in pr.RETEST_WINDOWS)
    # [ms, open, high, low]: the open is the scoring bars' fill, the close is not
    assert data["bars"]["S"][7] == [T0 + 7 * H1, 6.75, 7.5, 6.5]


# ── fresh arms, scored the way the observer would ─────────────────────────


def _flat(n: int, px: float = 99.0) -> list[list[float]]:
    """Bars that touch neither a 100 entry nor a 98 stop: [ms, open, high, low]."""
    return [[T0 + k * H1, px, px + 0.5, px - 0.5] for k in range(n)]


def _conf(t, retest_t=None, **lv):
    c = {"t": t, "retest_t": t if retest_t is None else retest_t, "side": "long",
         "entry": 100.0, "stop": 98.0, "target": 110.0, "atr": 2.0}
    c.update(lv)
    return c


def _data(bars, confirmed, key=None):
    return {"dataset": "d", "entry_bars": WIN, "bars": {"S": bars},
            "reads": {key or pr._read_key(P.atr_buffer, P.retest_window):
                      {"S": {"states": {}, "confirmed": confirmed}}}}


def test_only_a_read_retested_on_its_own_bar_is_armed():
    bars = _flat(400)
    got = pr.fresh_setups(_data(bars, [_conf(150), _conf(160, retest_t=158)]), P)
    assert [s["t"] for s in got] == [150]


def test_the_verdict_is_the_live_one_at_the_params_given():
    bars = _flat(400)
    d = _data(bars, [_conf(150)])
    assert len(pr.fresh_setups(d, P)) == 1
    from dataclasses import replace
    assert pr.fresh_setups(d, replace(P, min_net_r=50.0)) == []
    # a stop wider than the cap (3 ATR here) is refused too
    wide = _data(bars, [_conf(150, stop=94.0)])
    assert pr.fresh_setups(wide, P) == []
    assert pr.verdicts_seen(wide, P) == {"stop_too_wide": 1}


def test_the_scoring_window_is_the_observers_fetch():
    # the target is reached on the last bar the observer's window still holds
    # after the retest -> a win; one bar later is outside it -> still open
    for reach, want in ((WIN - 1, "target"), (WIN, "open")):
        bars = _flat(400)
        bars[151] = [T0 + 151 * H1, 99.0, 100.5, 99.0]          # trigger
        bars[150 + reach] = [T0 + (150 + reach) * H1, 105.0, 111.0, 104.0]
        got = pr.fresh_setups(_data(bars, [_conf(150)]), P)
        assert got[0]["outcome"].outcome == want


def test_the_retest_candle_itself_is_not_scored():
    # a real read's long entry IS the retest candle's high, so scoring that
    # candle would trigger every setup on the bar it was read on
    bars = _flat(400)
    bars[150] = [T0 + 150 * H1, 99.0, 100.0, 98.5]
    got = pr.fresh_setups(_data(bars, [_conf(150)]), P)
    assert got[0]["outcome"].outcome == "not_triggered"


def test_since_drops_a_retest_candle_at_or_before_it():
    bars = _flat(400)
    d = _data(bars, [_conf(150), _conf(151)])
    got = pr.fresh_setups(d, P, since_ms=bars[150][0])
    assert [s["t"] for s in got] == [151]


def test_a_trigger_that_opened_past_the_entry_is_measured_in_r():
    bars = _flat(400)
    bars[151] = [T0 + 151 * H1, 101.0, 101.5, 100.5]          # opened 1.0 past 100
    got = pr.fresh_setups(_data(bars, [_conf(150)]), P)[0]
    unit = pr._risk_unit(100.0, 98.0)
    assert got["gap_r"] == pytest.approx(1.0 / unit)
    bars[151] = [T0 + 151 * H1, 99.5, 100.5, 99.0]            # opened under it
    got = pr.fresh_setups(_data(bars, [_conf(150)]), P)[0]
    assert got["gap_r"] == 0.0


def test_the_risk_unit_is_the_stopped_out_loss_with_its_fees():
    from bot.core.trade_costs import net_reward_risk
    # net R x unit is the target's net reward, so the two agree on one unit
    c = net_reward_risk(100.0, 98.0, 110.0)
    reward = 10.0 - pr.fee_usd(100.0, pr.entry_rate_pct(None)) - pr.fee_usd(110.0, pr.exit_rate_pct())
    assert c.net * pr._risk_unit(100.0, 98.0) == pytest.approx(reward)


def test_setups_in_one_week_are_one_cluster():
    from bot.core.poc_retest_record import SetupOutcome
    rows = [{"week": ["d", 2026, 2], "outcome": SetupOutcome("target", "", r=2.0)}
            for _ in range(10)]
    assert pr.week_interval(rows) is None                   # one cluster: thin
    rows += [{"week": ["d", 2026, w], "outcome": SetupOutcome("stop", "", r=-1.0)}
             for w in range(3, 7)]
    n, clusters, mean, _lo, _hi = pr.week_interval(rows)
    assert (n, clusters) == (14, 5) and mean == pytest.approx((20.0 - 4.0) / 14)


def test_an_unscored_outcome_is_not_in_the_cluster_mean():
    from bot.core.poc_retest_record import SetupOutcome
    rows = [{"week": ["d", 2026, w], "outcome": SetupOutcome("stop", "", r=-1.0)}
            for w in range(5)]
    rows += [{"week": ["d", 2026, 9], "outcome": SetupOutcome("ambiguous", "")},
             {"week": ["d", 2026, 9], "outcome": SetupOutcome("not_triggered", "")}]
    assert pr.week_interval(rows)[:3] == (5, 5, -1.0)


# ── the observer as it is used ─────────────────────────────────────────────


def test_a_queried_observer_arms_a_stale_read_and_scores_it_from_the_retest():
    bars = _flat(400)
    bars[152] = [T0 + 152 * H1, 99.0, 100.5, 99.0]            # trigger
    bars[155] = [T0 + 155 * H1, 105.0, 111.0, 104.0]          # target
    # confirmed at 150 on its own bar; still confirmed (same retest) at 168
    d = _data(bars, [_conf(150), _conf(168, retest_t=150)])
    got = pr.observer_setups(d, P, every=24)                   # queries at 144, 168
    assert len(got) == 1
    row = got[0]
    assert (row["t"], row["armed_t"]) == (150, 168)
    # scored from the bar after the retest, bars that closed before anyone asked
    assert row["outcome"].outcome == "target"


def test_a_setup_read_twice_is_armed_once():
    bars = _flat(400)
    d = _data(bars, [_conf(168, retest_t=160), _conf(192, retest_t=160)])
    got = pr.observer_setups(d, P, every=24)
    assert [(r["t"], r["armed_t"]) for r in got] == [(160, 168)]


def test_a_retest_that_slid_out_of_the_window_keeps_its_last_score():
    bars = _flat(600)
    bars[200] = [T0 + 200 * H1, 99.0, 100.5, 99.0]            # triggered, still open
    bars[150 + WIN + 30] = [T0 + (150 + WIN + 30) * H1, 105.0, 111.0, 104.0]
    d = _data(bars, [_conf(168, retest_t=150)])
    row = pr.observer_setups(d, P, every=24)[0]
    # the target came after the retest left the fetched window: never seen
    assert row["outcome"].outcome == "open"


def test_the_observers_window_ends_where_its_fetch_does():
    # retest at 150: a read at 269 still holds it (120 bars back), a read at
    # 270 does not, so a target on bar 270 is never seen
    for reach, want in ((269, "target"), (270, "open")):
        bars = _flat(600)
        bars[152] = [T0 + 152 * H1, 99.0, 100.5, 99.0]
        bars[reach] = [T0 + reach * H1, 105.0, 111.0, 104.0]
        d = _data(bars, [_conf(168, retest_t=150)])
        assert pr.observer_setups(d, P, every=1)[0]["outcome"].outcome == want


def test_the_observer_arms_only_what_the_verdict_takes():
    bars = _flat(400)
    d = _data(bars, [_conf(168, retest_t=150, stop=94.0)])     # 3 ATR stop
    assert pr.observer_setups(d, P, every=24) == []


def test_a_stale_arm_of_a_setup_the_bar_by_bar_reader_also_armed_is_not_hindsight(tmp_path):
    import json
    bars = _flat(400)
    d = _data(bars, [_conf(150), _conf(168, retest_t=150)])
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d))
    out = pr.observer_report([str(f)], P, 24)
    assert "1 armed after their retest candle had closed; 0 are setups" in out


def test_the_forward_rule_does_not_arm_a_read_whose_entry_already_traded():
    bars = _flat(400)
    bars[152] = [T0 + 152 * H1, 99.0, 100.5, 99.0]            # traded the entry
    bars[155] = [T0 + 155 * H1, 105.0, 111.0, 104.0]          # and the target
    d = _data(bars, [_conf(168, retest_t=150)])
    assert pr.observer_setups(d, P, every=24)[0]["outcome"].outcome == "target"
    assert pr.observer_setups(d, P, every=24, forward=True) == [{"missed": True}]


def test_the_forward_rule_scores_from_the_read_that_armed_it():
    bars = _flat(400)
    bars[170] = [T0 + 170 * H1, 99.0, 100.5, 99.0]            # after the arming read
    bars[172] = [T0 + 172 * H1, 105.0, 111.0, 104.0]
    d = _data(bars, [_conf(168, retest_t=150)])
    row = pr.observer_setups(d, P, every=24, forward=True)[0]
    assert (row["t"], row["armed_t"], row["from_t"]) == (150, 168, 168)
    assert row["outcome"].outcome == "target"
    assert row["outcome"].trigger_index == 1                   # bar 170 is the 2nd after 168


def test_the_forward_rule_asks_the_recorders_trigger_reading(monkeypatch):
    bars = _flat(400)
    d = _data(bars, [_conf(168, retest_t=150)])
    monkeypatch.setattr(pr, "entry_traded", lambda *_a: None)
    assert pr.observer_setups(d, P, every=24, forward=True) == [{"missed": True}]


def test_the_forward_window_slides_from_the_arming_read():
    # armed at 168 on a retest at 150: a read at 287 still holds bar 168, so a
    # target on bar 280 is seen -- the retest's own window closed at 269
    bars = _flat(600)
    bars[270] = [T0 + 270 * H1, 99.0, 100.5, 99.0]
    bars[280] = [T0 + 280 * H1, 105.0, 111.0, 104.0]
    d = _data(bars, [_conf(168, retest_t=150)])
    assert pr.observer_setups(d, P, every=1, forward=True)[0]["outcome"].outcome == "target"


def test_the_report_counts_a_setup_resolved_on_its_own_arming_bar(tmp_path):
    import json
    bars = _flat(400)
    bars[152] = [T0 + 152 * H1, 99.0, 100.5, 99.0]
    bars[168] = [T0 + 168 * H1, 105.0, 111.0, 104.0]          # the arming bar itself
    d = _data(bars, [_conf(168, retest_t=150)])
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d))
    out = pr.observer_report([str(f)], P, 24)
    assert "1 had already resolved before the read that armed them" in out


def test_the_forward_report_names_what_it_did_not_arm(tmp_path):
    import json
    bars = _flat(400)
    bars[152] = [T0 + 152 * H1, 99.0, 100.5, 99.0]            # traded before 168
    d = _data(bars, [_conf(168, retest_t=150), _conf(192, retest_t=190)])
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d))
    out = pr.observer_report([str(f)], P, 24, forward=True)
    assert "armed forward only: armed 1" in out
    assert "1 confirmed reads not armed: their entry had already traded" in out
    assert "not armed" not in pr.observer_report([str(f)], P, 24)


def test_the_observer_report_names_what_the_bar_by_bar_reader_never_armed(tmp_path):
    import json
    bars = _flat(400)
    # fresh at 150; a stale confirmation of a DIFFERENT retest (158) that was
    # never confirmed on its own bar
    d = _data(bars, [_conf(150), _conf(168, retest_t=158)])
    f = tmp_path / "d.json"
    f.write_text(json.dumps(d))
    out = pr.observer_report([str(f)], P, 24)
    # the 24-hourly observer never reads hour 150, so it never sees the fresh
    # setup; it does see hour 168, and arms a retest nobody armed on its bar
    assert "bar-by-bar: armed 1" in out and "queried every 24h, armed as shipped: armed 1" in out
    assert "1 armed after their retest candle had closed; 1 are setups" in out


# ── reports and the CLI ─────────────────────────────────────────────────────


def _file(tmp_path, name, confirmed, bars=None):
    import json
    d = _data(bars or _flat(400), confirmed)
    d["dataset"] = name
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(d))
    return str(p)


def test_the_report_states_its_params_and_fees(tmp_path):
    f = _file(tmp_path, "a", [_conf(150)])
    out = pr.report([f], P, since="2026-01-01T00:00:00")
    assert f"entry taker {pr.entry_rate_pct(None):g}%" in out
    assert "retest candles after 2026-01-01T00:00:00" in out
    assert "armed 1: not_triggered 1" in out


def _grid_file(tmp_path, n, wins=True, **levels):
    """`n` setups, one every 40 hours, each a win (or a stop), at every cell."""
    import json
    bars = _flat(2000)
    confirmed = []
    for k in range(n):
        t = 100 + k * 40
        bars[t + 1] = [T0 + (t + 1) * H1, 99.0, 100.5, 99.0]
        bars[t + 2] = ([T0 + (t + 2) * H1, 105.0, 111.0, 104.0] if wins
                       else [T0 + (t + 2) * H1, 99.0, 99.5, 90.0])
        confirmed.append(_conf(t, **levels))
    data = {"dataset": "g", "entry_bars": WIN, "bars": {"S": bars},
            "reads": {pr._read_key(b, w): {"S": {"states": {}, "confirmed": confirmed}}
                      for b in pr.ATR_BUFFERS for w in pr.RETEST_WINDOWS}}
    f = tmp_path / f"g{n}{wins}{sorted(levels.items())}.json"
    f.write_text(json.dumps(data))
    return str(f)


def _grid_line(path, cell="0.25/5/2/2"):
    out = pr.grid([path], cells=[cell])
    return next(ln for ln in out.splitlines() if ln.startswith(cell))


def test_the_grid_marks_a_candidate_only_past_the_floor(tmp_path):
    assert pr.GRID_MIN_SCORED == 30
    at = _grid_line(_grid_file(tmp_path, 30))
    assert at.split()[1:3] == ["30", "30"] and at.endswith("<- candidate")
    under = _grid_line(_grid_file(tmp_path, 29))
    assert under.split()[1:3] == ["29", "29"] and "candidate" not in under
    losing = _grid_line(_grid_file(tmp_path, 30, wins=False))
    assert "candidate" not in losing


def test_the_grid_applies_the_verdict_params_of_each_cell(tmp_path):
    # a 105.5 target over a 98 stop is about 2.6R after fees: the 2R floor
    # keeps it and the 3R floor refuses it
    f = _grid_file(tmp_path, 10, target=105.5)
    assert _grid_line(f, "0.25/5/2/2").split()[1] == "10"
    assert _grid_line(f, "0.25/5/3/2").split()[1] == "0"
    # a stop 1.8 ATR wide: the 2 ATR cap keeps it and the 1.5 ATR cap refuses it
    f = _grid_file(tmp_path, 10, stop=96.4)
    assert _grid_line(f, "0.25/5/2/2").split()[1] == "10"
    assert _grid_line(f, "0.25/5/2/1.5").split()[1] == "0"


def test_the_grid_reads_each_cells_own_read_combo(tmp_path):
    import json
    f = _grid_file(tmp_path, 10)
    data = json.loads(Path(f).read_text())
    data["reads"]["0.5/8"]["S"]["confirmed"] = []
    Path(f).write_text(json.dumps(data))
    assert _grid_line(f, "0.25/5/2/2").split()[1] == "10"
    assert _grid_line(f, "0.5/8/2/2").split()[1] == "0"


def _mixed_file(tmp_path, wins_every=5):
    """30 setups one every 40 hours, a win every `wins_every`-th and a stop
    otherwise: a positive mean whose interval reaches below zero."""
    import json
    bars = _flat(2000)
    confirmed = []
    for k in range(30):
        t = 100 + k * 40
        bars[t + 1] = [T0 + (t + 1) * H1, 99.0, 100.5, 99.0]
        bars[t + 2] = ([T0 + (t + 2) * H1, 105.0, 111.0, 104.0] if k % wins_every == 0
                       else [T0 + (t + 2) * H1, 99.0, 99.5, 90.0])
        confirmed.append(_conf(t))
    data = {"dataset": "m", "entry_bars": WIN, "bars": {"S": bars},
            "reads": {pr._read_key(b, w): {"S": {"states": {}, "confirmed": confirmed}}
                      for b in pr.ATR_BUFFERS for w in pr.RETEST_WINDOWS}}
    f = tmp_path / "mixed.json"
    f.write_text(json.dumps(data))
    return str(f)


def _bounds(line):
    import re
    m = re.search(r"([+-]\d\.\d\d)R \[([+-]\d\.\d\d), ([+-]\d\.\d\d)\]", line)
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


def test_a_positive_mean_is_not_a_candidate_until_its_interval_clears_zero(tmp_path):
    line = _grid_line(_mixed_file(tmp_path))
    mean, lo, _hi = _bounds(line)
    assert mean > 0 > lo                       # the fixture is what it says
    assert "candidate" not in line


def test_the_grid_level_widens_the_interval(tmp_path):
    f = _mixed_file(tmp_path)
    cell = "0.25/5/2/2"
    at95 = next(ln for ln in pr.grid([f], cells=[cell]).splitlines() if ln.startswith(cell))
    at99 = next(ln for ln in pr.grid([f], level=0.99, cells=[cell]).splitlines()
                if ln.startswith(cell))
    _m, lo95, hi95 = _bounds(at95)
    _m, lo99, hi99 = _bounds(at99)
    assert lo99 < lo95 and hi99 > hi95


def test_the_cli_refuses_an_observer_that_never_asks(tmp_path):
    f = _file(tmp_path, "a", [_conf(150)])
    with pytest.raises(SystemExit):
        pr.main(["observer", "--every", "0", f])
    with pytest.raises(SystemExit):
        pr.main(["grid", "--level", "95", f])
