"""Each container that grew for the life of the bot process now has a bound.

The bot runs for days under a 5.5 GB unit cap and its resident memory
climbed (about 4.4-4.7 GB on 22 November 2026). Every structure below lived
for the whole process and grew per tick, per idea, per alert, per symbol or
per user with nothing removing entries. Each test fills one past its bound
and checks what is left: the size, which entries went (the oldest, or the
least recently used), and that the entries the code still needs stayed. Each
also has the other arm: below the bound, nothing is dropped.
"""
from __future__ import annotations

import asyncio
import json
import tracemalloc
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from bot import config as bot_config
from bot.compat import UTC
from bot.utils.bounded import evict_oldest, touch
from tests._fresh_trace import run_traced

# ── the shared rule ─────────────────────────────────────────────────────────

def test_evict_oldest_drops_in_insertion_order_and_returns_what_went():
    d = {k: k * 10 for k in range(10)}
    gone = evict_oldest(d, 7)
    assert gone == [(0, 0), (1, 10), (2, 20)]
    assert list(d) == [3, 4, 5, 6, 7, 8, 9]


def test_evict_oldest_never_drops_a_kept_key():
    d = {k: None for k in range(10)}
    gone = evict_oldest(d, 7, keep=lambda k: k < 2)
    assert [k for k, _ in gone] == [2, 3, 4]
    assert list(d) == [0, 1, 5, 6, 7, 8, 9]
    # kept keys alone above the cap: the cap bounds only what may go
    d = {k: None for k in range(5)}
    assert evict_oldest(d, 2, keep=lambda k: True) == []
    assert len(d) == 5


def test_touch_moves_a_key_to_the_newest_end_so_it_goes_last():
    d = {k: k for k in range(5)}
    touch(d, 1)
    touch(d, "absent")                               # a no-op, not a KeyError
    assert list(d) == [0, 2, 3, 4, 1] and d[1] == 1
    assert [k for k, _ in evict_oldest(d, 2)] == [0, 2, 3]


def test_evict_oldest_under_the_cap_touches_nothing():
    d = {k: None for k in range(5)}
    assert evict_oldest(d, 5) == [] and list(d) == [0, 1, 2, 3, 4]
    with pytest.raises(ValueError):
        evict_oldest(d, -1)


# ── 1. black swan: one alert row per detection, never removed ──────────────

def _detector_that_always_alerts():
    from bot.core import black_swan as bs
    det = bs.BlackSwanDetector()
    n = {"i": 0}

    def _alert(symbol):
        n["i"] += 1
        return bs.AnomalyAlert(anomaly_type=bs.AnomalyType.VOLUME_COLLAPSE,
                               severity=0.1, symbol=symbol, description=f"n{n['i']}",
                               metric_value=1.0, threshold=1.0,
                               recommended_action="MONITOR")

    det._check_volume_collapse = _alert
    for name in ("_check_correlation_breakdown", "_check_price_acceleration",
                 "_check_volatility_explosion", "_check_spread_widening"):
        setattr(det, name, lambda symbol: None)
    return bs, det


def test_black_swan_keeps_the_newest_alerts_only():
    bs, det = _detector_that_always_alerts()
    cap = bs._MAX_ACTIVE_ALERTS
    for _ in range(cap + 50):
        det.update("BTC/USDT", price=100.0, volume=10.0)
    alerts = det.active_alerts
    assert len(alerts) == cap
    assert alerts[0].description == "n51"           # the 50 oldest went
    assert alerts[-1].description == f"n{cap + 50}"  # the newest stayed


def test_black_swan_below_the_cap_keeps_every_alert():
    _bs, det = _detector_that_always_alerts()
    for _ in range(10):
        det.update("BTC/USDT", price=100.0, volume=10.0)
    assert [a.description for a in det.active_alerts] == [f"n{i}" for i in range(1, 11)]


# ── 2. engine: the ATR and pyramid flag of ideas that were never queued ────

def _engine_with_side_maps(n_side: int, pending_ids: list[str]):
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine.__new__(RuneClawEngine)
    now = datetime.now(UTC)
    eng._pending_ideas = {i: SimpleNamespace(timestamp=now, asset="BTC/USDT")
                          for i in pending_ids}
    eng._manual_margin_override = {}
    keys = [*pending_ids, *(f"old{i}" for i in range(n_side))]
    eng._pending_atr = {k: 1.0 for k in keys}
    eng._pending_pyramid = {k: True for k in keys}
    return eng, keys


def test_the_idea_side_maps_are_bounded_and_a_pending_ideas_atr_stays():
    pending = [f"pending{i}" for i in range(5)]       # the OLDEST keys of all
    from bot.core.engine import _IDEA_SIDE_MAP_CAP as cap
    eng, _keys = _engine_with_side_maps(1200, pending)
    assert eng._expire_pending_ideas() == []           # nothing expired by age
    for side in (eng._pending_atr, eng._pending_pyramid):
        assert len(side) == cap
        assert all(p in side for p in pending), "a pending idea lost its ATR"
        evicted = 1205 - cap
        assert "old0" not in side and f"old{evicted - 1}" not in side
        assert f"old{evicted}" in side and "old1199" in side


def test_the_idea_side_maps_below_the_cap_are_untouched():
    eng, keys = _engine_with_side_maps(900, ["pending0"])
    eng._expire_pending_ideas()
    assert list(eng._pending_atr) == keys and list(eng._pending_pyramid) == keys


# ── 3. engine: one view-only LiveExecutor per user who opened a balance ────

class _Store:
    def __init__(self):
        self.creds: dict[str, dict] = {}

    def get(self, uid):
        return self.creds.setdefault(uid, {"api_key": f"k-{uid}", "secret": "s",
                                           "passphrase": "p"})

    def get_venue(self, _uid):
        return "bitget"


class _Client:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def _view_engine():
    from bot.core.engine import RuneClawEngine
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = object()
    eng.ws_feed = None
    eng._user_executors = {}
    eng._balance_view_executors = {}
    eng._user_store = None
    eng._BALANCE_VIEW_CLOSE_GRACE_S = 0.0
    return eng


async def _settle(eng):
    tasks = list(getattr(eng, "_view_closers", ()) or ())
    if tasks:
        await asyncio.gather(*tasks)


def test_balance_views_are_an_lru_and_what_leaves_is_closed():
    store = _Store()

    async def scenario():
        eng = _view_engine()
        cap = eng._BALANCE_VIEW_CAP
        clients: dict[str, _Client] = {}

        def view(uid):
            ex = eng.balance_view_executor(uid)
            if uid not in clients or ex._exchange is None:
                clients[uid] = _Client()
                ex._exchange = clients[uid]
            return ex

        with patch("bot.core.exchange_credentials.get_credential_store",
                   return_value=store):
            first = {f"u{i}": view(f"u{i}") for i in range(cap + 3)}
            await _settle(eng)
            assert list(eng._balance_view_executors) == [f"u{i}" for i in range(3, cap + 3)]
            assert [clients[f"u{i}"].closed for i in range(3)] == [True] * 3
            assert not any(clients[f"u{i}"].closed for i in range(3, cap + 3))

            # A look moves a view to the newest end: u3 survives, u4 goes.
            assert view("u3") is first["u3"]
            view("newcomer")
            await _settle(eng)
            assert "u3" in eng._balance_view_executors
            assert "u4" not in eng._balance_view_executors and clients["u4"].closed
            assert len(eng._balance_view_executors) == cap

            # A credential change replaces the view and closes the old client.
            old_client = clients["u3"]
            store.creds["u3"] = {"api_key": "rotated", "secret": "s2", "passphrase": "p2"}
            replaced = eng.balance_view_executor("u3")
            await _settle(eng)
            assert replaced is not first["u3"] and old_client.closed
            assert len(eng._balance_view_executors) == cap

    asyncio.run(scenario())


def test_balance_views_below_the_cap_are_all_kept_and_none_closed():
    store = _Store()

    async def scenario():
        eng = _view_engine()
        clients = []
        with patch("bot.core.exchange_credentials.get_credential_store",
                   return_value=store):
            for i in range(5):
                ex = eng.balance_view_executor(f"u{i}")
                ex._exchange = _Client()
                clients.append(ex._exchange)
            await _settle(eng)
        assert list(eng._balance_view_executors) == [f"u{i}" for i in range(5)]
        assert not any(c.closed for c in clients)

    asyncio.run(scenario())


# ── 4. sentiment: three per-symbol dicts, no symbol ever dropped ───────────

def test_sentiment_keeps_the_most_recently_updated_symbols():
    from bot.core import sentiment as st
    eng = st.SentimentEngine()
    cap = st._MAX_SYMBOLS
    for i in range(cap):
        eng.update(f"S{i}", price=1.0, volume=1.0)
    eng.update("S0", price=1.0, volume=2.0)          # S0 is now the newest
    for i in range(cap, cap + 5):
        eng.update(f"S{i}", price=1.0, volume=1.0)
    for store in (eng._history, eng._price_returns, eng._volume_history):
        assert len(store) == cap
        assert "S0" in store, "a recently updated symbol was evicted"
        assert all(f"S{i}" not in store for i in range(1, 6))
        assert f"S{cap + 4}" in store
    assert set(eng._history) == set(eng._price_returns) == set(eng._volume_history)
    assert eng.latest.timestamp == eng._history[f"S{cap + 4}"][-1].timestamp
    assert len(eng._history["S0"]) == 2              # its own history kept


def test_sentiment_below_the_cap_keeps_every_symbol():
    from bot.core import sentiment as st
    eng = st.SentimentEngine()
    for i in range(10):
        eng.update(f"S{i}", price=1.0, volume=1.0)
    assert len(eng._history) == 10


# ── 5. smart money: the squeeze history's prune had no caller ──────────────

def test_the_squeeze_history_is_pruned_by_analyze():
    from bot.core.order_flow import OrderFlowSignal
    from bot.core.smart_money import SmartMoneyEngine
    eng = SmartMoneyEngine()
    for i in range(320):
        eng.analyze(OrderFlowSignal(symbol=f"P{i}", funding_rate=0.0001))
    assert len(eng.squeeze._history) == 300
    assert "P0" not in eng.squeeze._history and "P319" in eng.squeeze._history


def test_the_squeeze_history_below_its_cap_is_whole():
    from bot.core.order_flow import OrderFlowSignal
    from bot.core.smart_money import SmartMoneyEngine
    eng = SmartMoneyEngine()
    for i in range(20):
        eng.analyze(OrderFlowSignal(symbol=f"P{i}", funding_rate=0.0001))
    assert len(eng.squeeze._history) == 20


# ── 6/7. the learning store's decision file, read whole on every ask ───────

def _write_decisions(path, n, symbols=("BTC/USDT", "ETH/USDT"), corrupt_every=0):
    from bot.learning.models import DecisionMemory
    rows = []
    with open(path, "w", encoding="utf-8") as f:
        for i in range(n):
            if corrupt_every and i and i % corrupt_every == 0:
                f.write("{not json\n")
            d = DecisionMemory(audit_id=f"A{i:06d}", symbol=symbols[i % len(symbols)],
                               direction="LONG" if i % 3 else "SHORT",
                               market_regime="TREND" if i % 2 else "RANGE",
                               pnl_result=(1.0 if i % 4 else None))
            f.write(d.model_dump_json() + "\n")
            rows.append(d)
    return rows


def _store(tmp_path):
    from bot.learning.store import LearningStore
    return LearningStore(data_dir=str(tmp_path))


@pytest.mark.parametrize("symbol, limit", [(None, 10), ("ETH/USDT", 7), (None, 0),
                                           ("BTC/USDT", 100000), (None, -3)])
def test_get_decisions_answers_exactly_what_the_whole_read_did(tmp_path, symbol, limit):
    store = _store(tmp_path)
    rows = _write_decisions(store._files["decision"], 120, corrupt_every=17)
    whole = [r for r in rows if not symbol or r.symbol == symbol]
    expect = [r.audit_id for r in whole[-limit:]]
    got = [r.audit_id for r in store.get_decisions(symbol=symbol, limit=limit)]
    assert got == expect


def test_stats_counts_without_changing_the_count(tmp_path):
    from bot.learning.models import DecisionMemory
    store = _store(tmp_path)
    _write_decisions(store._files["decision"], 50, corrupt_every=9)
    assert store.stats()["decisions"] == 50
    assert store.stats()["decisions"] == len(store._read_jsonl("decision", DecisionMemory))


def _peak_of(fn) -> int:
    tracemalloc.start(1)
    try:
        tracemalloc.reset_peak()
        fn()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_a_tail_read_holds_the_tail_not_the_file():
    """The memory claim itself, measured: asking for the last 10 of 3000 rows
    peaks at a small fraction of parsing the whole file into a list. Measured
    in a fresh interpreter (`tests/_fresh_trace.py`), so no tracer already on
    in the test process can change either peak."""
    out = run_traced("""
import json
import tempfile
from pathlib import Path
from bot.learning.models import DecisionMemory
from tests.test_unbounded_containers_are_capped import _peak_of, _store, _write_decisions
with tempfile.TemporaryDirectory() as d:
    store = _store(Path(d))
    _write_decisions(store._files["decision"], 3000)
    whole = _peak_of(lambda: store._read_jsonl("decision", DecisionMemory))
    tail = _peak_of(lambda: store.get_decisions(limit=10))
print(json.dumps([tail, whole]))
""")
    tail, whole = json.loads(out)
    assert whole > 0, "the whole read must have been measured"
    assert tail * 5 < whole, (tail, whole)


def test_the_decision_index_keeps_what_a_question_can_read(tmp_path):
    store = _store(tmp_path)
    idx = store._similar
    idx.KEEP = 50                                    # a fixture-sized depth
    path = store._files["decision"]
    _write_decisions(path, 400, symbols=("BTC/USDT",))

    def reference(window, limit):
        last = store.get_decisions(symbol="BTC/USDT", limit=window)
        return [r.audit_id for r in last if r.pnl_result is not None][-limit:]

    got = [r.audit_id for r in store.similar_decisions("BTC/USDT", "", "", window=40, limit=8)]
    assert got == reference(40, 8)
    assert idx.KEEP <= len(idx._by_symbol["BTC/USDT"]) <= 2 * idx.KEEP
    assert len(idx._all) <= 2 * idx.KEEP

    # Rows appended later are indexed and trimmed the same way.
    from bot.learning.models import DecisionMemory
    with open(path, "a", encoding="utf-8") as f:
        for i in range(400, 520):
            f.write(DecisionMemory(audit_id=f"A{i:06d}", symbol="BTC/USDT", direction="LONG",
                                   pnl_result=1.0).model_dump_json() + "\n")
    got = [r.audit_id for r in store.similar_decisions("BTC/USDT", "", "", window=40, limit=8)]
    assert got == reference(40, 8) and got[-1] == "A000519"
    assert len(idx._by_symbol["BTC/USDT"]) <= 2 * idx.KEEP

    with pytest.raises(ValueError):
        store.similar_decisions("BTC/USDT", "", "", window=idx.KEEP + 1, limit=8)


def test_the_decision_index_below_its_depth_keeps_every_key(tmp_path):
    store = _store(tmp_path)
    _write_decisions(store._files["decision"], 30, symbols=("BTC/USDT",))
    store.similar_decisions("BTC/USDT", "", "", window=500, limit=5)
    assert len(store._similar._by_symbol["BTC/USDT"]) == 30


# ── 8/9. the proactive monitor's black-swan repeat map and arb day set ─────

def _bs_alert(key, severity="WARNING"):
    from bot.core.proactive_monitor import Alert
    return Alert(alert_type="BLACK_SWAN", severity=severity, title="t", body="b",
                 dedup_key=key)


def test_expired_black_swan_repeats_are_dropped_and_live_ones_still_suppress():
    import time

    from bot.core.proactive_monitor import ProactiveMonitor
    mon = ProactiveMonitor.__new__(ProactiveMonitor)
    now = time.time()
    horizon = max(mon.BLACK_SWAN_REPEAT, mon.BLACK_SWAN_SEVERE_REPEAT)
    mon._bs_last = {f"dead{i}": (now - horizon - 5, 0) for i in range(1200)}
    mon._bs_last.update({f"live{i}": (now - 10, 0) for i in range(10)})

    assert mon._bs_is_news(_bs_alert("fresh")) is True
    assert set(mon._bs_last) == {*(f"live{i}" for i in range(10)), "fresh"}
    # a live condition is still suppressed, a dead one would have been sent
    assert mon._bs_is_news(_bs_alert("live3")) is False
    assert mon._bs_is_news(_bs_alert("dead7")) is True


def test_black_swan_repeats_below_the_threshold_are_kept_even_when_expired():
    import time

    from bot.core.proactive_monitor import ProactiveMonitor
    mon = ProactiveMonitor.__new__(ProactiveMonitor)
    now = time.time()
    mon._bs_last = {f"dead{i}": (now - 100_000, 0) for i in range(20)}
    assert mon._bs_is_news(_bs_alert("fresh")) is True
    assert len(mon._bs_last) == 21


def test_arb_keys_from_earlier_days_are_dropped():
    from bot.core.proactive_monitor import ProactiveMonitor
    seen = {"arb_BTC_2026-11-20", "arb_ETH_2026-11-21", "arb_SOL_2026-11-22"}
    assert ProactiveMonitor._remember_arb(seen, "arb_DOGE_2026-11-22", "2026-11-22") is True
    assert seen == {"arb_SOL_2026-11-22", "arb_DOGE_2026-11-22"}
    assert ProactiveMonitor._remember_arb(seen, "arb_SOL_2026-11-22", "2026-11-22") is False
    assert seen == {"arb_SOL_2026-11-22", "arb_DOGE_2026-11-22"}


# ── 10. config: one unread-knob tuple per runtime read ─────────────────────

def test_an_unread_knob_is_recorded_once(monkeypatch):
    monkeypatch.setattr(bot_config, "ENV_UNREAD", [])
    for _ in range(50):
        bot_config._note_env_unread("BAR_FRESHNESS_MAX_PERIODS", "not finite", 2.0)
    assert bot_config.ENV_UNREAD == [("BAR_FRESHNESS_MAX_PERIODS", "not finite", 2.0)]
    bot_config._note_env_unread("BAR_FRESHNESS_MAX_PERIODS", "not a number", 2.0)
    assert len(bot_config.ENV_UNREAD) == 2


def test_the_runtime_reader_no_longer_grows_the_list(monkeypatch):
    from bot.core import bar_freshness
    monkeypatch.setattr(bot_config, "ENV_UNREAD", [])
    monkeypatch.setenv("BAR_FRESHNESS_MAX_PERIODS", "nan")
    for _ in range(20):
        bar_freshness.bar_freshness_max_periods()
    assert len(bot_config.ENV_UNREAD) == 1, bot_config.ENV_UNREAD
    assert json.dumps(bot_config.ENV_UNREAD)        # tuples of plain values
