"""The outcome walk keeps up with the stream, and no call vanishes pending.

The first walk took the twelve rows checked longest ago each fifteen minutes,
with a row never checked sorting first. At ~700 calls a day every call got one
look a few minutes after it was published, before any bar after it had closed,
and never a second: the newer never-checked rows always sorted ahead. The cap
(2000 rows) then evicted it still pending, and its website row stayed NEW for
good. These drive the replacement (`bot.core.signal_outcomes` and
`RuneClawEngine._resolve_signal_outcomes`): a row is walked when a bar it has
not read can have closed, a pass is bounded by markets, the pass's answers are
one write, a row nobody could walk is closed UNSCORED at its deadline, and the
cap never deletes a row the website has not received.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import shutil
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from bot.core import signal_outcomes as so

H = so.BAR_MS
WINDOW = 4 * 3600
T0 = so.parse_time_ms("2026-10-01T00:00:00+00:00")


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def _call(key, symbol, created_ms, direction="LONG", entry=100.0, gap=5.0):
    long = direction == "LONG"
    return {"signal_key": key, "symbol": symbol, "direction": direction,
            "entry_price": entry,
            "stop_loss": entry - gap if long else entry + gap,
            "take_profit": entry + gap if long else entry - gap,
            "confidence": 0.7, "created_at": _iso(created_ms), "status": "NEW"}


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "signal_outcomes.json"
    monkeypatch.setattr(so, "ledger_path", lambda: p)
    return p


def _engine(monkeypatch, bars_for, website):
    """The real `_resolve_signal_outcomes` on an engine with stand-in candles
    and a stand-in website that keeps the last word it was sent per key."""
    from bot.core.engine import RuneClawEngine
    from bot.utils import website_sync

    eng = RuneClawEngine.__new__(RuneClawEngine)
    asked: list = []

    async def get_ex():
        return object()

    async def cached(exchange, symbol, timeframe, limit=100, ttl=120):
        asked.append(symbol)
        got = bars_for(symbol, limit)
        if isinstance(got, BaseException):
            raise got
        return got

    eng.scanner = SimpleNamespace(_get_exchange=get_ex, _get_futures_exchange=get_ex)
    eng._cached_ohlcv = cached

    def sync(rows):
        assert len(rows) <= so.RESEND_BATCH, "the website takes 500 rows a request"
        for r in rows:
            website[r["signal_key"]] = r["status"]
        return True
    monkeypatch.setattr(website_sync, "sync_signals", sync)
    monkeypatch.setattr("bot.core.engine.CONFIG", SimpleNamespace(
        limit_orders=SimpleNamespace(expire_seconds=WINDOW)))
    return eng, asked


def _pass(eng, now_ms):
    eng._signal_outcome_pass_at = None          # the throttle is wall-clock
    return asyncio.run(eng._resolve_signal_outcomes(now_ms=now_ms))


def _sigs(path):
    return json.loads(path.read_text())["signals"]


# ── the stream at ~727 calls a day ──────────────────────────────────────────

MARKETS = [f"C{i}/USDT" for i in range(30)]
DEAD = "DEAD/USDT"                 # its candles never read
NO_MARKET = "B T C"                # names no market at all


def _price(m: int, hours: float) -> float:
    return 100.0 + 4.0 * math.sin(hours / 2.5 + m)


def _bars(symbol: str, now_ms: int, limit: int):
    """Closed hourly bars up to ``now_ms``, the newest ``limit`` of them."""
    if symbol == DEAD:
        return TimeoutError("the exchange did not answer")
    m = MARKETS.index(symbol)
    closed = (now_ms - T0) // H            # bars [T0 - 300h, T0 + closed h) have closed
    out = []
    for h in range(max(-300, closed - limit), closed):
        o, c = _price(m, h), _price(m, h + 1)
        out.append([T0 + h * H, o, max(o, c) + 0.6, min(o, c) - 0.6, c, 1.0])
    return out


def test_seven_hundred_calls_a_day_all_reach_a_final_word(ledger, monkeypatch):
    # A short week and grace keep the simulation small; the schedule, the
    # market bound and the deadline are the production ones.
    monkeypatch.setattr(so, "HORIZON_S", 6 * 3600)
    monkeypatch.setattr(so, "TAIL_GRACE_S", 3 * 3600)
    deadline_s = WINDOW + so.HORIZON_S + so.TAIL_GRACE_S
    days = 2
    every_s = 86400 / 727
    calls = []
    t = 0.0
    while t < days * 86400:
        i = len(calls)
        created = T0 + int(t * 1000)
        if i % 97 == 0:
            sym, m = DEAD, None
        elif i % 211 == 0:
            sym, m = NO_MARKET, None
        else:
            m = i % len(MARKETS)
            sym = MARKETS[m]
        mid = _price(m, (created - T0) / H) if m is not None else 100.0
        direction = "LONG" if i % 2 else "SHORT"
        entry = mid - 0.3 if direction == "LONG" else mid + 0.3
        calls.append(_call(f"c{i}", sym, created, direction, entry, gap=2.5))
        t += every_s
    assert len(calls) >= 727 * days

    clock = {"now": T0}
    # `record_published` prunes on the wall clock; this stream runs on its own.
    monkeypatch.setattr(so, "time", SimpleNamespace(time=lambda: clock["now"] / 1000))
    website: dict = {}
    eng, asked = _engine(monkeypatch, lambda s, limit: _bars(s, clock["now"], limit), website)
    published = 0
    end = T0 + days * 86400 * 1000
    fetches_per_pass = []
    while clock["now"] <= end:
        now = clock["now"]
        fresh = []
        while published < len(calls) and so.parse_time_ms(calls[published]["created_at"]) <= now:
            fresh.append(calls[published])
            published += 1
        if fresh:
            so.record_published(fresh)
            website.update({r["signal_key"]: "NEW" for r in fresh})
        before = len(asked)
        _pass(eng, now)
        fetches_per_pass.append(len(asked) - before)
        clock["now"] += 15 * 60 * 1000

    sigs = _sigs(ledger)
    keys = {c["signal_key"] for c in calls[:published]}
    # Two days is inside the keep period: nothing has left, and in particular
    # no call left before its final word reached the website.
    assert set(sigs) == keys, "a call vanished from the ledger"
    assert max(fetches_per_pass) <= so.MAX_MARKETS_PER_PASS
    old = [c for c in calls[:published]
           if so.parse_time_ms(c["created_at"]) + deadline_s * 1000 <= end]
    assert len(old) > 1000
    for c in old:
        k = c["signal_key"]
        v = sigs[k]
        assert v["status"] in so.TERMINAL, (k, c["symbol"], v["status"])
        assert v["synced"] is True, k
        assert website[k] == v["status"], "the website does not have the final word"
    # Most calls reached a verdict a walk read off the bars, not a deadline.
    walked = [sigs[c["signal_key"]] for c in old if c["symbol"] in MARKETS]
    read = [v for v in walked if v["status"] != so.UNSCORED]
    assert len(read) == len(walked), "a readable market's call was closed unread"
    # The dead market's calls were closed UNSCORED at their deadline, never dropped.
    dead = [sigs[c["signal_key"]] for c in old if c["symbol"] == DEAD]
    assert dead and all(v["status"] == so.UNSCORED and v["why"] == so.AGED_OUT_WHY
                        for v in dead)
    nomarket = [sigs[c["signal_key"]] for c in old if c["symbol"] == NO_MARKET]
    assert nomarket and all(v["status"] == so.UNSCORED for v in nomarket)


# ── the cap ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("seed", range(6))
def test_the_cap_never_deletes_a_pending_or_unsynced_row(seed, monkeypatch):
    rng = random.Random(seed)
    monkeypatch.setattr(so, "MAX_ROWS", 20)
    now = T0 + 30 * 86400 * 1000
    sigs = {}
    for i in range(50):
        status = rng.choice(so.PENDING + so.TERMINAL)
        sigs[f"k{i}"] = {
            "status": status, "synced": rng.random() < 0.6,
            "resolved_ms": now - rng.randrange(0, 20) * 86400 * 1000
            if status in so.TERMINAL and rng.random() < 0.8 else None,
            "checked_ms": now - rng.randrange(0, 20) * 86400 * 1000,
            "row": _call(f"k{i}", "BTC/USDT", now - rng.randrange(1, 400) * H)}
    keep = {k for k, v in sigs.items()
            if v["status"] in so.PENDING or not v["synced"]}
    so._prune(sigs, now)
    assert keep <= set(sigs), "a row the website had not received was deleted"
    pending = [v for v in sigs.values() if v["status"] in so.PENDING]
    assert len(pending) <= so.MAX_ROWS
    if len(sigs) > so.MAX_ROWS:
        # Still over: only because rows that have not reached the website are
        # waiting to; nothing final and synced is left to evict.
        assert not [v for v in sigs.values() if v["status"] in so.TERMINAL and v["synced"]]
    # And a second prune with nothing synced in between closes nothing more.
    assert so._prune(sigs, now)[1] == 0


def test_publishing_over_the_cap_closes_the_oldest_pending_call_and_sends_it(ledger, monkeypatch):
    monkeypatch.setattr(so, "MAX_ROWS", 3)
    so.record_published([_call(f"p{i}", "BTC/USDT", T0 + i * H) for i in range(3)])
    so.record_published([_call("p3", "BTC/USDT", T0 + 3 * H)])
    sigs = _sigs(ledger)
    assert set(sigs) == {"p0", "p1", "p2", "p3"}
    assert sigs["p0"]["status"] == so.UNSCORED and sigs["p0"]["synced"] is False
    assert [sigs[k]["status"] for k in ("p1", "p2", "p3")] == [so.NEW] * 3
    # Later writes while p0 waits to sync close no further call.
    got = so.apply_outcomes([], T0 + 4 * H)
    assert got.closed == 0 and [k for k, _ in got.unsent] == ["p0"]
    assert [v["status"] for v in _sigs(ledger).values()].count(so.UNSCORED) == 1


# ── one write per pass equals one write per answer ──────────────────────────

@pytest.mark.parametrize("seed", range(5))
def test_the_batch_write_records_what_one_write_per_answer_records(seed, tmp_path):
    rng = random.Random(seed)
    words = so.PENDING + so.TERMINAL
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    so.record_published([_call(f"k{i}", MARKETS[i % 5], T0 + i * 60_000) for i in range(40)],
                        path=a)
    # Some rows already moved and some of those synced, before this pass.
    so.apply_outcomes([(f"k{i}", so.Resolution(rng.choice(words), "earlier"))
                       for i in range(0, 40, 3)], T0 + 2 * H, path=a)
    so.mark_outcomes_synced({f"k{i}": so.OPEN for i in range(0, 40, 2)}, path=a)
    shutil.copy(a, b)

    def answer():
        word = rng.choice(words)
        return so.Resolution(word, f"w{rng.random():.3f}",
                             r={so.TARGET: 2.0, so.STOP: -1.0}.get(word),
                             resolved_ms=T0 + rng.randrange(1, 9) * H,
                             retry=rng.random() < 0.2)
    results = [(f"k{rng.randrange(45)}", answer()) for _ in range(80)]   # repeats, unknowns
    now = T0 + 5 * H                                                     # before any deadline
    batch = so.apply_outcomes(results, now, path=a)
    one_by_one = [so.apply_outcomes([r], now, path=b) for r in results]
    assert json.loads(a.read_text()) == json.loads(b.read_text())
    assert batch.changed == sum(w.changed for w in one_by_one)
    assert [k for k, _ in batch.unsent] == [k for k, _ in one_by_one[-1].unsent]


def test_a_walk_answer_in_the_same_pass_beats_the_deadline(ledger):
    so.record_published([_call("a", "BTC/USDT", T0), _call("b", "BTC/USDT", T0)])
    deadline = T0 + (WINDOW + so.HORIZON_S + so.TAIL_GRACE_S) * 1000
    got = so.apply_outcomes([("a", so.Resolution(so.TARGET, "t", r=1.0, resolved_ms=T0 + 2 * H))],
                            deadline, window_s=WINDOW)
    sigs = _sigs(ledger)
    assert sigs["a"]["status"] == so.TARGET
    assert sigs["b"]["status"] == so.UNSCORED and sigs["b"]["why"] == so.AGED_OUT_WHY
    assert (got.changed, got.closed) == (1, 1)


# ── due time and order ──────────────────────────────────────────────────────

def test_due_is_the_first_bar_close_after_publication_then_after_each_check():
    created = T0 + 17 * 60_000                         # 00:17
    entry = {"row": _call("k", "BTC/USDT", created), "status": so.NEW}
    assert so.outcome_due_ms(entry) == T0 + 2 * H       # the 01:00 bar closes at 02:00
    entry["row"]["created_at"] = _iso(T0)              # on a boundary: its own bar
    assert so.outcome_due_ms(entry) == T0 + H
    entry["checked_ms"] = T0 + 5 * H + 20 * 60_000     # checked 05:20
    assert so.outcome_due_ms(entry) == T0 + 6 * H       # the 05:00 bar closes at 06:00
    entry["checked_ms"] = T0 + 6 * H                   # checked exactly at a close
    assert so.outcome_due_ms(entry) == T0 + 7 * H
    assert so.outcome_due_ms({"row": {"created_at": "not a time"}, "status": so.NEW}) == 0


def test_a_pass_takes_due_rows_oldest_first_bounded_by_markets(ledger):
    rows = [_call(f"m{i}", f"M{i}/USDT", T0 + i * 60_000) for i in range(45)]
    rows.append(_call("late", "M0/USDT", T0 + 3 * H))   # not due until 05:00
    rows.append(_call("m0b", "M0/USDT", T0 + 50 * 60_000))
    so.record_published(rows)
    plan = so.plan_outcome_pass(T0 + 2 * H)
    assert list(plan.markets) == [f"M{i}/USDT" for i in range(40)]
    assert [k for k, _ in plan.markets["M0/USDT"]] == ["m0", "m0b"]   # one fetch, both rows
    assert (plan.due, plan.deferred) == (46, 5)
    # A market in backoff is passed over without using a slot.
    plan = so.plan_outcome_pass(T0 + 2 * H, skip=frozenset({"M0/USDT"}))
    assert "M0/USDT" not in plan.markets and len(plan.markets) == 40
    assert plan.deferred == 2 + 4
    plan = so.plan_outcome_pass(T0 + 5 * H)
    assert [k for k, _ in plan.markets["M0/USDT"]] == ["m0", "m0b", "late"]


def test_the_engine_reads_each_market_once_for_all_its_due_rows(ledger, monkeypatch):
    so.record_published([_call(f"b{i}", "BTC/USDT", T0 + i * 60_000) for i in range(6)]
                        + [_call("e0", "ETH/USDT", T0)])
    quiet = [[T0 + h * H, 120.0, 121.0, 119.0, 120.0, 1.0] for h in range(3)]
    eng, asked = _engine(monkeypatch, lambda s, limit: quiet, {})
    _pass(eng, T0 + 3 * H)
    assert sorted(asked) == ["BTC/USDT", "ETH/USDT"]
    sigs = _sigs(ledger)
    assert all(v["checked_ms"] == T0 + 3 * H for v in sigs.values())
    # Nothing is due again before the next bar closes.
    _pass(eng, T0 + 3 * H + 59 * 60_000)
    assert len(asked) == 2


# ── the deadline ────────────────────────────────────────────────────────────

def test_a_call_no_walk_could_read_is_closed_unscored_at_its_deadline_and_sent(
        ledger, monkeypatch):
    so.record_published([_call("k", "BTC/USDT", T0)])
    deadline = T0 + (WINDOW + so.HORIZON_S + so.TAIL_GRACE_S) * 1000
    website = {"k": "NEW"}
    eng, asked = _engine(monkeypatch, lambda s, limit: TimeoutError("down"), website)
    _pass(eng, deadline - 1)
    assert _sigs(ledger)["k"]["status"] == so.NEW and website["k"] == "NEW"
    eng._signal_outcome_backoff = {}
    assert _pass(eng, deadline) == 1
    v = _sigs(ledger)["k"]
    assert (v["status"], v["why"], v["synced"]) == (so.UNSCORED, so.AGED_OUT_WHY, True)
    assert website["k"] == so.UNSCORED


def test_resends_go_in_batches_the_website_accepts_and_stop_when_it_refuses(
        ledger, monkeypatch):
    from bot.utils import website_sync
    rows = [_call(f"k{i}", "BTC/USDT", T0) for i in range(1200)]
    so.record_published(rows)
    so.apply_outcomes([(f"k{i}", so.Resolution(so.EXPIRED, "x", resolved_ms=T0 + 4 * H))
                       for i in range(1200)], T0 + 5 * H)
    batches = []
    answers = iter([True, False, True])

    def sync(batch):
        batches.append(len(batch))
        return next(answers)
    eng, _ = _engine(monkeypatch, lambda s, limit: [], {})
    monkeypatch.setattr(website_sync, "sync_signals", sync)
    assert _pass(eng, T0 + 5 * H + 1) == so.RESEND_BATCH
    # At most RESEND_MAX a pass, 500 a request; the refusal ends the pass.
    assert batches == [500, 500]
    assert sum(1 for v in _sigs(ledger).values() if not v["synced"]) == 700


# ── what a pass says about itself ───────────────────────────────────────────

def test_a_pass_logs_one_summary_line_and_publishes_its_counts(ledger, monkeypatch, caplog):
    import bot.web.dashboard_server as ds
    so.record_published([_call("k", "BTC/USDT", T0), _call("j", "ETH/USDT", T0 + 5 * H)])
    bars = [[T0 + h * H, 101.0, 106.0, 99.0, 105.0, 1.0] for h in range(3)]
    eng, _ = _engine(monkeypatch, lambda s, limit: bars, {})
    assert ds._signal_outcome_series(eng) == [], "no pass has run: no reading, not zero"
    with caplog.at_level(logging.INFO, logger="bot.core.engine"):
        assert _pass(eng, T0 + 3 * H) == 1
    lines = [r.getMessage() for r in caplog.records if "Signal outcomes pass" in r.getMessage()]
    assert lines == ["Signal outcomes pass: due=1 walked=1 markets=1 deferred=0 changed=1 "
                     "closed_unscored=0 landed=1 fetch_errors=0 ledger_rows=2 pending=1"]
    series = {name: value for name, value, _h, _t in ds._signal_outcome_series(eng)}
    assert series["runeclaw_signal_outcome_pending"] == 1.0
    assert series["runeclaw_signal_outcome_ledger_rows"] == 2.0
    assert series["runeclaw_signal_outcome_passes_total"] == 1.0
    assert series["runeclaw_signal_outcome_landed_total"] == 1.0
    assert series["runeclaw_signal_outcome_due"] == 1.0
    text = ds._render_prometheus(eng)
    assert "runeclaw_signal_outcome_pending 1.0" in text


def test_a_pass_whose_write_failed_does_not_report_the_ledger_it_could_not_write(
        ledger, monkeypatch, caplog):
    import bot.web.dashboard_server as ds
    so.record_published([_call("k", "BTC/USDT", T0)])
    eng, _ = _engine(monkeypatch, lambda s, limit: [], {})
    monkeypatch.setattr(so, "apply_outcomes", lambda *a, **k: None)
    with caplog.at_level(logging.INFO, logger="bot.core.engine"):
        assert _pass(eng, T0 + 3 * H) == 0
    assert any("NOT recorded" in r.getMessage() for r in caplog.records)
    names = {name for name, *_ in ds._signal_outcome_series(eng)}
    assert "runeclaw_signal_outcome_due" in names
    assert "runeclaw_signal_outcome_pending" not in names
    assert "runeclaw_signal_outcome_ledger_rows" not in names
