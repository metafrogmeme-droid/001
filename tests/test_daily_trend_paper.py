"""scripts/daily_trend_paper_book.py and daily_trend_paper_eval.py: the sealed
daily trend paper forward test (docs/research/prereg/daily_trend_paper_forward_2026-11/).

Nothing here reaches a network: every fetch takes an injected ``get``, the head
publisher is a stub, and the rule runs in-process except in the one test that
checks the pinned commit's archive."""
from __future__ import annotations

import bisect
import dataclasses
import hashlib
import importlib.util
import json
import math
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STUDY = ROOT / "docs/research/prereg/daily_trend_paper_forward_2026-11"
UTC = timezone.utc


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


bk = _load("daily_trend_paper_book")
ev = _load("daily_trend_paper_eval")
H, D = bk.HOUR_MS, bk.DAY_MS
STUDY_REAL = bk.study_from(bk.load_config())


def ms(text: str) -> int:
    return bk.parse_utc_ms(text)


def at(ms_: int) -> datetime:
    return datetime.fromtimestamp(ms_ / 1000, tz=UTC)


# ── the seal ───────────────────────────────────────────────────────────────

def test_the_config_is_read_only_when_its_bytes_are_the_sealed_ones(tmp_path):
    cfg = bk.load_config()
    assert cfg["study"] == "daily_trend_paper_forward_2026-11"
    assert bk.CONFIG_SHA256 in (STUDY / "PREREGISTRATION.md").read_text()
    edited = tmp_path / "config.json"
    edited.write_bytes(bk.CONFIG_PATH.read_bytes().replace(b'"max_drawdown":0.15', b'"max_drawdown":0.25'))
    with pytest.raises(bk.Refused, match="not the sealed"):
        bk.load_config(edited)


def test_the_study_reads_the_preset_and_costs_the_config_seals():
    s = STUDY_REAL
    assert len(s.bases) == 16 and "BTC" in s.bases
    assert s.band == 0.1 and s.costs == bk.Costs(0.0006, 0.0005) and s.stress == 2.0
    assert bk.iso_day(s.window_open) == "2026-11-23"
    assert bk.iso_day(s.earliest_close) == "2027-05-24" and bk.iso_day(s.hard_close) == "2027-11-22"


def test_the_pinned_commit_s_rule_files_are_the_sealed_ones():
    with bk.pinned_rule(STUDY_REAL) as rule_dir:
        assert (rule_dir / "bot/core/daily_trend.py").is_file()
        assert not str(rule_dir).startswith(str(ROOT)), "the archive is unpacked outside the repository"
    tampered = dataclasses.replace(STUDY_REAL, rule_files={"bot/core/daily_trend.py": "0" * 64})
    with pytest.raises(bk.Refused, match="sealed"):
        with bk.pinned_rule(tampered):
            pass


def test_the_pinned_rule_runs_in_its_own_interpreter_and_matches_the_rule_in_process():
    data = synth_data(STUDY_REAL, STUDY_REAL.window_open, days=3)
    cut = [STUDY_REAL.window_open, STUDY_REAL.window_open + D]
    with bk.pinned_rule(STUDY_REAL) as rule_dir:
        got = bk.rule_targets(data, STUDY_REAL, cut, rule_dir)
    want = in_process_targets(data, STUDY_REAL, cut, None)
    assert got.keys() == want.keys()
    for c in cut:
        assert got[c]["targets"] == pytest.approx(want[c]["targets"])
        assert any(v for v in got[c]["targets"].values()), "the fixture holds something"


# ── the rebalance rule ─────────────────────────────────────────────────────

@pytest.mark.parametrize("t,q,w,want", [
    (None, 1.0, 0.1, False),      # unread: nothing traded
    (0.0, 1.0, 0.1, True),        # exit
    (0.0, 0.0, 0.0, False),       # flat and no target
    (0.05, 0.0, 0.0, True),       # enter
    (0.105, 1.0, 0.1, False),     # inside the 10% band
    (0.111, 1.0, 0.1, True),      # outside it
    (0.089, 1.0, 0.1, True),
    (0.2, 1.0, None, False),      # held but unpriced: no rebalance
])
def test_the_rebalance_rule(t, q, w, want):
    assert bk.wants_trade(t, q, w, 0.1) is want


# ── the book, by hand ──────────────────────────────────────────────────────

D0 = ms("2026-01-05T00:00:00Z")


def one_market(fill_d0=101.0, fill_d1=99.0, drop_d1_fill=False, funding=None):
    rows = []
    for t in range(D0 - 2 * D, D0 + 3 * D, H):
        c = 100.0
        if t == D0:
            c = fill_d0
        if t == D0 + D:
            if drop_d1_fill:
                continue
            c = fill_d1
        rows.append([float(t), 100.0, 101.0, 99.0, c, 1.0])
    return bk.Market(rows=rows, funding=funding or [])


def one_study():
    return dataclasses.replace(STUDY_REAL, bases=("AAA",), symbols=("AAAUSDT",), window_open=D0)


def test_an_episode_s_net_is_its_fills_fees_slippage_and_funding():
    funding = [(D0 + 8 * H, 0.001), (D0 + D + H, 0.001)]   # the second is at the exit's fill time
    data = {"AAA": one_market(funding=funding)}
    targets = {D0: {"targets": {"AAA": 0.5}}, D0 + D: {"targets": {"AAA": 0.0}}}
    book = bk.simulate(data, one_study(), targets, [D0, D0 + D], one_study().costs)
    assert len(book.closed) == 1 and not book.open
    ep = book.closed[0]
    q = 0.5 / 100.0
    buy = q * 101.0 * 1.0005 + q * 101.0 * 0.0006
    fund = q * 100.0 * 0.001 + q * 99.0 * 0.001          # pre-fill quantity at the fill time
    sell = q * 99.0 * 0.9995 - q * 99.0 * 0.0006
    assert ep.flows == pytest.approx(-buy - fund + sell, abs=1e-12)
    assert ep.ret == pytest.approx(ep.flows / 1.0)
    assert ep.funding == pytest.approx(fund) and ep.fees == pytest.approx(q * 101 * 0.0006 + q * 99 * 0.0006)
    assert book.days[1].equity == pytest.approx(1.0 - buy - q * 100 * 0.001 + q * 100.0)
    assert book.days[1].held["AAA"] == pytest.approx(q * 100.0 / book.days[1].equity)
    assert book.cash == pytest.approx(1.0 + ep.flows)


def test_a_missing_fill_candle_leaves_the_position_and_says_so():
    data = {"AAA": one_market(drop_d1_fill=True)}
    targets = {D0: {"targets": {"AAA": 0.5}}, D0 + D: {"targets": {"AAA": 0.0}}}
    book = bk.simulate(data, one_study(), targets, [D0, D0 + D], one_study().costs)
    assert book.days[1].unfilled == ["AAA"] and not book.closed and "AAA" in book.open
    assert book.days[1].after["AAA"] == book.days[1].held["AAA"]


def test_an_unread_target_trades_nothing_and_is_not_a_zero():
    data = {"AAA": one_market()}
    targets = {D0: {"targets": {"AAA": 0.5}}, D0 + D: {"targets": {"AAA": None}}}
    book = bk.simulate(data, one_study(), targets, [D0, D0 + D], one_study().costs)
    assert book.days[1].targets["AAA"] is None and not book.days[1].trades
    assert "AAA" in book.open, "an unread day is not an exit"


def test_doubled_costs_lower_every_episode():
    data = {"AAA": one_market()}
    targets = {D0: {"targets": {"AAA": 0.5}}, D0 + D: {"targets": {"AAA": 0.0}}}
    s = one_study()
    base = bk.simulate(data, s, targets, [D0, D0 + D], s.costs).closed[0].ret
    stress = bk.simulate(data, s, targets, [D0, D0 + D], s.costs.scaled(2)).closed[0].ret
    assert stress < base


def test_the_decision_day_is_not_filled_when_the_writer_runs_before_the_fill():
    data = {"AAA": one_market()}
    targets = {D0: {"targets": {"AAA": 0.5}}}
    book = bk.simulate(data, one_study(), targets, [D0], one_study().costs, fill_last=False)
    assert not book.days[0].filled and not book.days[0].trades and not book.open
    assert book.days[0].after["AAA"] == 0.5


# ── the close rule and the bootstrap ───────────────────────────────────────

def test_the_window_closes_on_the_first_monday_with_the_count_after_26_weeks():
    s = STUDY_REAL
    closes = [s.window_open + i * D for i in range(150)]
    got = ev.close_day(s, lambda m: sum(1 for c in closes if c < m), None, s.hard_close + D)
    assert got == (s.earliest_close, "minimum count reached")
    assert ev.close_day(s, lambda m: 149, None, s.hard_close - D) is None
    assert ev.close_day(s, lambda m: 149, None, s.hard_close) == (s.hard_close, "hard close")
    assert ev.close_day(s, lambda m: 500, None, s.earliest_close - D) is None, "never before 26 weeks"


def test_a_drawdown_breach_closes_early_only_when_it_comes_first():
    s = STUDY_REAL
    early = s.window_open + 10 * D
    assert ev.close_day(s, lambda m: 0, early, early) == (early, "drawdown limit")
    late = s.earliest_close + 3 * D
    assert ev.close_day(s, lambda m: 200, late, late + D) == (s.earliest_close, "minimum count reached")
    assert ev.first_breach([(1, 1.0), (2, 0.9), (3, 0.86)]) is None
    assert ev.first_breach([(1, 1.0), (2, 1.2), (3, 1.01)]) == 3


def _episodes(rets_by_week):
    out = []
    for week, rets in enumerate(rets_by_week):
        for r in rets:
            e = bk.Episode(base="X", open_day=D0, eq_open=1.0, close_day=D0 + week * 7 * D, flows=r)
            out.append(e)
    return out


def test_the_lower_bound_clears_zero_for_a_steady_edge_and_not_for_noise():
    steady = _episodes([[0.002, 0.003, 0.001]] * 30)
    assert ev.weekly_lower_bound(steady) > 0
    rng = random.Random(1)
    noise = _episodes([[rng.gauss(0.0005, 0.01) for _ in range(5)] for _ in range(30)])
    assert statistic_mean(noise) > 0 and ev.weekly_lower_bound(noise) < 0
    assert ev.weekly_lower_bound(steady) == ev.weekly_lower_bound(steady), "seeded"


def statistic_mean(eps):
    return sum(e.ret for e in eps) / len(eps)


# ── fetch (stubbed) ────────────────────────────────────────────────────────

def test_candles_page_back_and_keep_only_the_asked_range():
    calls = []
    end = D0 + 300 * H

    def get(path):
        calls.append(path)
        cursor = int(re.search(r"endTime=(\d+)", path).group(1))
        top = cursor - cursor % H
        return {"data": [[str(t), "1", "1", "1", "1", "1", "1"] for t in range(top - 99 * H, top + 1, H)]}
    rows = bk.fetch_candles("BTCUSDT", D0, end, get)
    assert all(D0 <= int(r[0]) < end for r in rows) and len(rows) == 300
    assert all("category=USDT-FUTURES" in c and "interval=1H" in c for c in calls)


def test_funding_pages_until_it_passes_the_start_and_drops_unreadable_rates():
    pages = {1: [{"fundingTime": str(D0 + 2 * D), "fundingRate": "0.0001"},
                 {"fundingTime": str(D0 + D), "fundingRate": "bad"}],
             2: [{"fundingTime": str(D0 - D), "fundingRate": "0.0002"}]}

    def get(path):
        return {"data": pages.get(int(re.search(r"pageNo=(\d+)", path).group(1)), [])}
    assert bk.fetch_funding("BTCUSDT", D0, get) == [[D0 + 2 * D, 0.0001]]


def test_clean_rows_keeps_closed_candles_and_drops_unreadable_ones():
    raw = [[D0, "1", "2", "0.5", "1.5", "10"], [D0 + H, "1", "x", "0.5", "1.5", "10"],
           [D0 + 2 * H, "1", "2", "0.5", "1.5", "10"]]
    assert [int(r[0]) for r in bk.clean_rows(raw, D0 + 2 * H + 30 * 60_000)] == [D0]


# ── the writer and the evaluator, end to end on synthetic data ─────────────

#: Three rising markets halve on these window days, so their trend scores fall
#: to zero and episodes close inside a short window.
CRASH_DAY = {1: 2, 2: 4, 4: 6}


def synth_data(study, open_ms, days, seed=0, bump=None):
    data = {}
    for i, b in enumerate(study.bases):
        r = random.Random(seed * 100 + i)
        p, rows = 100.0, []
        drift = 0.0006 if i % 3 else -0.0004
        crash = open_ms + CRASH_DAY[i] * D - 6 * H if i in CRASH_DAY else None
        for t in range(open_ms - 140 * D, open_ms + days * D + 2 * H, H):
            o = p
            p *= math.exp(drift + r.gauss(0, 0.012)) * (0.5 if t == crash else 1.0)
            c = p * (1.01 if bump and (b, t) == bump else 1.0)
            rows.append([float(t), o, max(o, c), min(o, c), c, 1.0])
        data[b] = bk.Market(rows=rows, funding=[(t, 0.0001) for t in range(open_ms, open_ms + days * D, 8 * H)])
    return data


def in_process_targets(data, study, cutoffs, rule_dir):
    from bot.core.daily_trend import read_book
    out = {}
    for cut in sorted(cutoffs):
        sl = {}
        for b, m in data.items():
            opens = [int(r[0]) for r in m.rows]
            sl[b] = m.rows[bisect.bisect_left(opens, cut - bk.WARMUP_DAYS * D):bisect.bisect_left(opens, cut)]
        book = read_book(sl, study.preset)
        reads = book["reads"]
        out[cut] = {"n_book": book["n_book"],
                    "targets": {b: (reads[b].weight if b in reads else None) for b in study.bases}}
    return out


W = ms("2026-01-05T00:00:00Z")      # a Monday
CLOCK = {"now": W}


class _ChainClock(datetime):
    """The chain stamps entries with ``datetime.now``; on synthetic days the
    stamp follows the simulated clock, as it follows the real one in use."""

    @classmethod
    def now(cls, tz=None):
        return datetime.fromtimestamp(CLOCK["now"] / 1000, tz=tz or UTC)


@pytest.fixture
def short_window(monkeypatch):
    import bot.utils.audit_chain as audit_chain
    monkeypatch.setattr(audit_chain, "datetime", _ChainClock)
    monkeypatch.setattr(bk, "rule_targets", in_process_targets)
    monkeypatch.setattr(bk, "EARLIEST_CLOSE_WEEKS", 1)
    monkeypatch.setattr(bk, "HARD_CLOSE_WEEKS", 2)
    monkeypatch.setattr(bk, "MIN_CLOSED_EPISODES", 1)
    return dataclasses.replace(STUDY_REAL, window_open=W)


class Stub:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def __call__(self, text):
        if self.fail:
            raise ConnectionError("token 123456:SECRETSECRET rejected")
        self.sent.append(text)
        return str(len(self.sent))


def run_writer(study, chain, data, days, late=(), skip=()):
    for i in range(days):
        d = W + i * D
        if i in skip:
            continue
        decide_at = d + (2 * H if i in late else 40 * 60_000)
        CLOCK["now"] = decide_at
        bk.run_decide(study, chain, data, at(decide_at), None, Stub())
        CLOCK["now"] = max(decide_at, d + 70 * 60_000)
        bk.run_fill(study, chain, data, at(CLOCK["now"]), None)


def test_the_writer_refuses_a_live_mainnet_environment_and_runs_on_paper():
    with pytest.raises(bk.Refused, match="live mainnet"):
        bk.refuse_live({"LIVE_TRADING_ENABLED": "1", "SIMULATION_MODE": "0"})
    bk.refuse_live({})
    bk.refuse_live({"LIVE_TRADING_ENABLED": "1", "SIMULATION_MODE": "0", "BITGET_SANDBOX": "1"})


def test_the_writer_records_nothing_before_the_window_and_once_a_day(tmp_path, short_window):
    chain = tmp_path / "chain.jsonl"
    data = synth_data(short_window, W, days=3)
    with pytest.raises(bk.Refused, match="window opens"):
        bk.run_decide(short_window, chain, data, at(W - H), None, Stub())
    with pytest.raises(bk.Refused, match="no decision"):
        bk.run_fill(short_window, chain, data, at(W + 70 * 60_000), None)
    pub = Stub()
    CLOCK["now"] = W + 40 * 60_000
    msg = bk.run_decide(short_window, chain, data, at(W + 40 * 60_000), None, pub)
    assert "decision recorded" in msg and "head published" in msg
    assert "already" in bk.run_decide(short_window, chain, data, at(W + 50 * 60_000), None, pub)
    entries = bk.read_chain(chain)
    assert [e["event_type"] for e in entries] == [bk.DECISION, bk.HEAD_PUBLISHED]
    p = entries[0]["payload"]
    assert p["config_sha256"] == bk.CONFIG_SHA256 and p["rule_commit"] == STUDY_REAL.commit
    assert set(p["targets"]) == set(short_window.bases) and p["read_at"].startswith("2026-01-05T00:40")
    assert entries[0]["entry_hash"] in pub.sent[0]
    assert entries[1]["payload"]["head_hash"] == entries[0]["entry_hash"]
    assert "fill(s) recorded" in bk.run_fill(short_window, chain, data, at(W + 70 * 60_000), None)


def test_a_failed_post_records_the_class_never_the_message(tmp_path, short_window):
    chain = tmp_path / "chain.jsonl"
    data = synth_data(short_window, W, days=2)
    out = bk.run_decide(short_window, chain, data, at(W + 40 * 60_000), None, Stub(fail=True))
    assert "NOT published" in out
    text = chain.read_text()
    assert "ConnectionError" in text and "SECRET" not in text
    out = bk.run_decide(short_window, tmp_path / "c2.jsonl", data, at(W + 40 * 60_000), None, None)
    assert "NOT published" in out and "no publisher" in (tmp_path / "c2.jsonl").read_text()


def test_the_telegram_publisher_needs_both_variables():
    assert bk.telegram_publisher({}) is None
    assert bk.telegram_publisher({"TELEGRAM_BOT_TOKEN": "x"}) is None
    assert callable(bk.telegram_publisher({"TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_ALERT_CHAT_ID": "1"}))


def test_status_prints_counts_and_never_a_return(tmp_path, short_window):
    chain = tmp_path / "chain.jsonl"
    data = synth_data(short_window, W, days=15)
    run_writer(short_window, chain, data, 4)
    st = ev.chain_status(short_window, chain, W + 4 * D + 30 * 60_000)   # day 5 not due yet
    assert st["days_recorded"] == 4 and st["days_missed"] == 0 and st["chain_verifies"] is True
    assert ev.chain_status(short_window, chain, W + 4 * D + 2 * H)["days_missed"] == 1
    assert st["days_covered_by_01_00"] == 4 and st["days_head_published"] == 4
    assert st["window_closed"] is False
    for k, v in st.items():
        assert v is None or isinstance(v, (bool, int, str)), k
        assert not isinstance(v, float), f"{k} is a figure"
    assert not any(w in k for k in st for w in ("equity", "return", "pnl", "drawdown_pct"))


def _evaluate(study, chain, data, tmp_path, now):
    manifest = {"files": {}}
    return ev.evaluate(study, chain, data, manifest, None, now, heads_dir=tmp_path / "heads")


def test_an_honest_record_stands_and_the_replay_decides(tmp_path, short_window):
    chain = tmp_path / "chain.jsonl"
    data = synth_data(short_window, W, days=15)
    run_writer(short_window, chain, data, 14)
    now = W + 14 * D + 2 * H
    st = ev.chain_status(short_window, chain, now)
    assert st["window_closed"] is True
    (tmp_path / "heads").mkdir()
    (tmp_path / "heads" / "2026-W02.txt").write_text(bk.export_heads(chain))
    res = _evaluate(short_window, chain, data, tmp_path, now)
    rec = res["record"]
    assert rec["chain_verifies"] and rec["published_heads_in_chain"]
    assert rec["live_coverage"] == 1.0 and rec["replay_match"] == 1.0 and rec["anchor_coverage"] == 1.0
    assert res["verdict"] in ("PASS", "FAIL"), (res["because"], res["window"])
    assert res["window"]["closed"] == st["closes_on"]
    n = res["reported_deciding_nothing"]["closed_episodes"]
    assert n >= 2 and res["criteria"]["expectancy_mean"] is not None
    assert st["episodes_closed"] == n, "status counts the episodes the replay closes"


def test_an_edited_chain_is_no_verdict(tmp_path, short_window):
    chain = tmp_path / "chain.jsonl"
    data = synth_data(short_window, W, days=15)
    run_writer(short_window, chain, data, 14)
    lines = chain.read_text().splitlines()
    e = json.loads(lines[0])
    e["payload"]["mark_equity"] = 1.5
    lines[0] = json.dumps(e)
    chain.write_text("\n".join(lines) + "\n")
    res = _evaluate(short_window, chain, data, tmp_path, W + 14 * D + 2 * H)
    assert res["record"]["chain_verifies"] is False and res["verdict"] == "NO VERDICT"


def test_a_published_head_the_chain_no_longer_has_is_no_verdict(tmp_path, short_window):
    chain = tmp_path / "chain.jsonl"
    data = synth_data(short_window, W, days=15)
    run_writer(short_window, chain, data, 14)
    (tmp_path / "heads").mkdir()
    (tmp_path / "heads" / "w.txt").write_text("3 " + "f" * 64 + " x DT_DECISION 2026-01-06\n")
    res = _evaluate(short_window, chain, data, tmp_path, W + 14 * D + 2 * H)
    assert res["record"]["published_heads_in_chain"] is False and res["verdict"] == "NO VERDICT"


def test_late_or_missing_days_below_95_percent_are_no_verdict(tmp_path, short_window):
    chain = tmp_path / "chain.jsonl"
    data = synth_data(short_window, W, days=15)
    run_writer(short_window, chain, data, 14, late={2}, skip={4})
    res = _evaluate(short_window, chain, data, tmp_path, W + 14 * D + 2 * H)
    nwin = res["window"]["decision_days"]
    covered_days = round(res["record"]["live_coverage"] * nwin)
    assert covered_days == nwin - 2 and res["record"]["live_coverage_ok"] is False
    assert res["verdict"] == "NO VERDICT"


def test_a_record_the_replay_does_not_reproduce_is_no_verdict(tmp_path, short_window):
    chain = tmp_path / "chain.jsonl"
    data = synth_data(short_window, W, days=15)
    run_writer(short_window, chain, data, 14)
    other = synth_data(short_window, W, days=15, bump=("BTC", W + 2 * D - H))   # one mark differs
    res = _evaluate(short_window, chain, other, tmp_path, W + 14 * D + 2 * H)
    assert res["record"]["mismatched_days"] and res["record"]["replay_match_ok"] is False
    assert res["verdict"] == "NO VERDICT"


def test_the_result_is_written_once_and_inputs_are_fetched_once(tmp_path):
    path = tmp_path / "result.json"
    digest = ev.write_result({"verdict": "FAIL"}, path)
    assert (tmp_path / "result.json.sha256").read_text().startswith(digest)
    with pytest.raises(bk.Refused, match="runs once"):
        ev.write_result({"verdict": "PASS"}, path)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "manifest.json").write_text("{}")
    with pytest.raises(bk.Refused, match="fetched once"):
        ev.fetch_inputs(STUDY_REAL, inputs, W)


def test_an_input_whose_bytes_are_not_the_manifest_s_is_refused(tmp_path):
    inputs = tmp_path / "inputs"
    p = bk.write_market_file(inputs, "BTCUSDT", [[W, 1, 1, 1, 1, 1]], [])
    (inputs / "manifest.json").write_text(json.dumps({"files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()}}))
    data, _ = ev.load_inputs(STUDY_REAL, inputs)
    assert "BTC" in data
    p.write_bytes(p.read_bytes() + b"x")
    with pytest.raises(bk.Refused, match="manifest says"):
        ev.load_inputs(STUDY_REAL, inputs)


# ── Addendum 1 names these scripts' bytes ──────────────────────────────────

def test_addendum_1_is_sealed_and_names_the_scripts_as_they_stand():
    add = STUDY / "ADDENDUM_1.md"
    side = (STUDY / "ADDENDUM_1.sha256").read_text().split()
    assert side[0] == hashlib.sha256(add.read_bytes()).hexdigest() and side[1] == "ADDENDUM_1.md"
    text = add.read_text()
    for name in ("daily_trend_paper_book.py", "daily_trend_paper_eval.py"):
        digest = hashlib.sha256((ROOT / "scripts" / name).read_bytes()).hexdigest()
        assert f"`scripts/{name}`: `{digest}`" in text, f"{name} changed after Addendum 1 was sealed"
    sealed = (STUDY / "ADDENDUM_1.sealed_at").read_text().strip()
    assert datetime.fromisoformat(sealed.replace("Z", "+00:00")) < datetime.fromtimestamp(
        STUDY_REAL.window_open / 1000, tz=UTC)
