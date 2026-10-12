"""A resting entry order that fills between ticks is protected within seconds.

The tick reads positions once, after the scan and the analysis
(`RuneClawEngine._tick`). A resting limit that filled while the analysis ran
had no stop on the venue until the tick reached it: minutes, on the ticks of
8-10 October that ran the analysis near its 300s cap. Plan item D15.

The position guard (`RuneClawEngine._position_guard_loop`) asks, every
`position_guard_interval_sec`, about each executor's resting entry orders and
unread submissions (`LiveExecutor.guard_pass`), and books and protects a fill
as soon as it reads one. It does nothing else: the drift rule, the expiry and
the stale-row close stay on the tick, because sampled every few seconds a
brief spike past the drift line would cancel or chase a limit the tick would
have left resting. Every positions pass, the tick's and the guard's, runs
under one lock (`RuneClawEngine._positions_lock`).

Driven: the executor's pass against a planted venue, beside the tick's pass on
the same order (both arms), the engine's pass with real executors, the lock,
the status line and the shutdown hook.
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from bot.config import CONFIG
from bot.core import engine as engine_mod
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor, LivePosition
from bot.core.venues import get_venue
from bot.formatters.rich_cards import position_guard_line

UTC = timezone.utc
LIMIT = 3990.0


@pytest.fixture
def limit_cfg():
    """A 4h expiry and a 2% drift cancel with the market fallback off, so a
    drifted limit is cancelled rather than chased. The config is frozen, so
    it is replaced and handed back in a finally."""
    old = CONFIG.limit_orders
    cfg = dataclasses.replace(old, expire_seconds=14400,
                              price_drift_cancel_pct=2.0, drift_market_fallback=False)
    object.__setattr__(CONFIG, "limit_orders", cfg)
    try:
        yield cfg
    finally:
        object.__setattr__(CONFIG, "limit_orders", old)


class monitoring:
    """Replace `CONFIG.monitoring` fields for one block and hand the object
    back after it. The config is frozen, so monkeypatch cannot write it."""

    def __init__(self, **kw):
        self.kw = kw

    def __enter__(self):
        self.old = CONFIG.monitoring
        object.__setattr__(CONFIG, "monitoring", dataclasses.replace(self.old, **self.kw))

    def __exit__(self, *exc):
        object.__setattr__(CONFIG, "monitoring", self.old)


class Venue:
    """A venue whose order ``o9`` answers ``order``; its ticker answers ``last``."""

    def __init__(self, order, last: float = LIMIT):
        self.order = order if isinstance(order, BaseException) else dict(order)
        self.last = last
        self.calls: list[str] = []
        self.cancelled: list[str] = []

    async def fetch_order(self, oid, symbol=None, params=None, **k):
        self.calls.append("fetch_order")
        if isinstance(self.order, BaseException):
            raise self.order
        return dict(self.order)

    async def cancel_order(self, oid, symbol=None, params=None, **k):
        self.calls.append("cancel_order")
        self.cancelled.append(oid)
        self.order = dict(self.order, status="canceled")
        return {"id": oid}

    async def fetch_ticker(self, symbol, *a, **k):
        self.calls.append("fetch_ticker")
        return {"last": self.last}


def _resting(filled=0.0):
    return {"id": "o9", "status": "open", "filled": filled, "average": None,
            "price": LIMIT, "amount": 0.05, "info": {}}


def _filled():
    return {"id": "o9", "status": "closed", "filled": 0.05, "average": 3989.0,
            "price": LIMIT, "amount": 0.05, "info": {}}


def _pending(age_min=10.0, trade_id="TI-L"):
    return LivePosition(
        trade_id=trade_id, symbol="ETH/USDT", direction="LONG", entry_price=LIMIT,
        quantity=0.05, cost_usd=40.0, stop_loss=LIMIT * 0.98, take_profit=LIMIT * 1.06,
        leverage=5, is_spot=False, order_type="limit", limit_order_id="o9",
        opened_at=datetime.now(UTC) - timedelta(minutes=age_min), status="pending_fill")


def _executor(tmp_path, venue, pos) -> LiveExecutor:
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._venue = get_venue("bitget")
    ex._hedge_mode = False
    ex._exchange = venue
    ex._get_exchange = AsyncMock(return_value=venue)
    ex._probe_hold_mode_if_unknown = AsyncMock()
    ex._positions[pos.trade_id] = pos
    return ex


def _run(coro):
    async def _sl_tp(*a, **k):
        return ("sl1", "tp1")

    async def _ladder(self_, exchange, pos, direction, qty, sl_id, tp_id, trade_id=None):
        return sl_id, tp_id, None

    with patch.object(type(CONFIG), "is_live", return_value=True), \
         patch.object(LiveExecutor, "_place_sl_tp", _sl_tp), \
         patch.object(LiveExecutor, "_reattempt_post_fill_sl", _ladder), \
         patch.object(LiveExecutor, "_guard_fill_leverage", AsyncMock(return_value=None)):
        return asyncio.run(coro)


# ── the executor's pass ─────────────────────────────────────────────────────


def test_a_fill_is_booked_and_protected_by_the_guard_pass(tmp_path, limit_cfg):
    venue = Venue(_filled())
    pos = _pending()
    ex = _executor(tmp_path, venue, pos)
    msgs = _run(ex.guard_pass())
    assert pos.status == "open" and pos.quantity == 0.05
    assert (pos.sl_order_id, pos.tp_order_id) == ("sl1", "tp1"), "protected at the fill"
    assert msgs and "FILLED" in msgs[0].upper()


def test_a_drifted_order_is_left_to_the_tick(tmp_path, limit_cfg):
    """5% away under a 2% drift line: the guard reads it and leaves it; the
    tick's pass cancels it, as before."""
    venue = Venue(_resting(), last=LIMIT * 1.05)
    pos = _pending()
    ex = _executor(tmp_path, venue, pos)
    assert _run(ex.guard_pass()) == []
    assert venue.cancelled == [] and pos.status == "pending_fill"
    assert "fetch_ticker" not in venue.calls, "the drift rule was not even read"
    # The other arm: the tick's pass on the same order.
    _run(ex._check_pending_limit(venue, pos.trade_id, pos))
    assert venue.cancelled == ["o9"]


def test_an_expired_order_is_left_to_the_tick(tmp_path, limit_cfg):
    venue = Venue(_resting())
    pos = _pending(age_min=5 * 60)
    ex = _executor(tmp_path, venue, pos)
    assert _run(ex.guard_pass()) == []
    assert venue.cancelled == [] and pos.status == "pending_fill"
    _run(ex._check_pending_limit(venue, pos.trade_id, pos))
    assert venue.cancelled == ["o9"]


def test_a_halt_cancels_a_resting_order_on_the_guard_pass(tmp_path, limit_cfg):
    venue = Venue(_resting())
    pos = _pending()
    ex = _executor(tmp_path, venue, pos)
    _run(ex.guard_pass(entry_halt="circuit breaker"))
    assert venue.cancelled == ["o9"], "an order placed before a refusal is cancelled at once"


def test_an_unread_stale_order_is_left_to_the_tick(tmp_path, limit_cfg):
    """No order to read and past the hard timeout: the stale-row close is the
    tick's. The guard reads again next pass."""
    venue = Venue(RuntimeError("venue 5xx"))
    pos = _pending(age_min=9 * 60)
    ex = _executor(tmp_path, venue, pos)
    ex._force_close_stale_pending = AsyncMock(return_value="STALE CLOSED")
    assert _run(ex.guard_pass()) == []
    ex._force_close_stale_pending.assert_not_awaited()
    assert _run(ex._check_pending_limit(venue, pos.trade_id, pos)) == "STALE CLOSED"


def test_an_idle_book_asks_the_venue_nothing(tmp_path, limit_cfg):
    venue = Venue(_resting())
    pos = _pending()
    pos.status = "open"
    ex = _executor(tmp_path, venue, pos)
    assert ex.guard_has_work() is False
    assert _run(ex.guard_pass()) == []
    assert venue.calls == [] and ex._get_exchange.await_count == 0


def test_an_unread_submission_is_work_for_the_guard(tmp_path, limit_cfg):
    venue = Venue(_resting())
    pos = _pending()
    pos.status = "open"
    ex = _executor(tmp_path, venue, pos)
    ex._unverified_submissions = {"cid-1": {"symbol": "ETH/USDT"}}
    ex._reconcile_unverified_submissions = AsyncMock(return_value=["SUBMISSION: booked"])
    assert ex.guard_has_work() is True
    assert _run(ex.guard_pass()) == ["SUBMISSION: booked"]


def test_one_orders_fault_costs_only_that_order(tmp_path, limit_cfg):
    venue = Venue(_filled())
    ex = _executor(tmp_path, venue, _pending(trade_id="TI-A"))
    ex._positions["TI-B"] = _pending(trade_id="TI-B")
    real = ex._check_pending_limit
    seen: list[str] = []

    async def flaky(exchange, trade_id, pos, **kw):
        seen.append(trade_id)
        if trade_id == "TI-A":
            raise RuntimeError("boom")
        return await real(exchange, trade_id, pos, **kw)

    ex._check_pending_limit = flaky
    _run(ex.guard_pass())
    assert seen == ["TI-A", "TI-B"]
    assert ex._positions["TI-B"].status == "open"


# ── the engine's pass ───────────────────────────────────────────────────────


def _engine(executors) -> RuneClawEngine:
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._running = True
    eng.live_executor = executors[0]
    eng._user_executors = {str(i): ex for i, ex in enumerate(executors[1:])}
    eng._entry_halt_reason = lambda ex: None
    eng.routed = []

    async def _route(ex, msgs):
        eng.routed.append((ex, list(msgs)))

    eng._route_monitor_messages = _route
    return eng


def _guard_once(eng):
    with patch.object(type(CONFIG), "is_live", return_value=True):
        return asyncio.run(eng._position_guard_once())


def test_the_guard_asks_each_account_with_work_and_announces_what_it_read(tmp_path):
    a = LiveExecutor(state_dir=str(tmp_path / "a"))
    a._positions["TI-A"] = _pending(trade_id="TI-A")
    a.guard_pass = AsyncMock(return_value=["LIMIT FILLED: LONG ETH/USDT"])
    b = LiveExecutor(state_dir=str(tmp_path / "b"))       # nothing resting
    b.guard_pass = AsyncMock(return_value=[])
    eng = _engine([a, b])
    assert _guard_once(eng) == "ran"
    a.guard_pass.assert_awaited_once()
    b.guard_pass.assert_not_awaited()
    assert eng.routed == [(a, ["LIMIT FILLED: LONG ETH/USDT"])]
    assert eng._last_position_guard["outcome"] == "ran"
    assert eng._last_position_guard["orders"] == 1


def test_each_account_is_asked_under_its_own_entry_halt(tmp_path):
    a = LiveExecutor(state_dir=str(tmp_path / "a"))
    a._positions["TI-A"] = _pending(trade_id="TI-A")
    b = LiveExecutor(state_dir=str(tmp_path / "b"))
    b._positions["TI-B"] = _pending(trade_id="TI-B")
    asked: dict = {}

    def _recorder(name):
        async def _pass(entry_halt=None):
            asked[name] = entry_halt
            return []
        return _pass

    a.guard_pass = _recorder("a")
    b.guard_pass = _recorder("b")
    eng = _engine([a, b])
    eng._entry_halt_reason = lambda ex: "drawdown breaker" if ex is a else None
    assert _guard_once(eng) == "ran"
    assert asked == {"a": "drawdown breaker", "b": None}


def test_nothing_resting_is_idle_and_asks_nothing(tmp_path):
    a = LiveExecutor(state_dir=str(tmp_path))
    a.guard_pass = AsyncMock()
    eng = _engine([a])
    assert _guard_once(eng) == "idle"
    a.guard_pass.assert_not_awaited()
    assert eng._last_position_guard["outcome"] == "idle"


def test_a_pass_in_flight_makes_the_guard_wait_its_turn(tmp_path):
    a = LiveExecutor(state_dir=str(tmp_path))
    a._positions["TI-A"] = _pending(trade_id="TI-A")
    a.guard_pass = AsyncMock(return_value=[])
    eng = _engine([a])

    async def go():
        lock = eng._positions_lock()
        async with lock:
            # Bounded: a guard that waited for the lock here would wait for
            # this test, which is holding it.
            return await asyncio.wait_for(eng._position_guard_once(), 5)

    with patch.object(type(CONFIG), "is_live", return_value=True):
        assert asyncio.run(go()) == "busy"
    a.guard_pass.assert_not_awaited()
    assert getattr(eng, "_last_position_guard", None) is None, "busy replaces no reading"


@pytest.mark.parametrize("off", ["switch", "paper"])
def test_the_guard_is_off_when_switched_off_or_not_live(tmp_path, monkeypatch, off):
    a = LiveExecutor(state_dir=str(tmp_path))
    a._positions["TI-A"] = _pending(trade_id="TI-A")
    a.guard_pass = AsyncMock()
    eng = _engine([a])
    if off == "switch":
        with monitoring(position_guard_enabled=False):
            out = _guard_once(eng)
    else:
        with patch.object(type(CONFIG), "is_live", return_value=False):
            out = asyncio.run(eng._position_guard_once())
    assert out == "off"
    a.guard_pass.assert_not_awaited()


def test_one_accounts_failure_is_said_and_the_next_account_is_still_asked(tmp_path, monkeypatch):
    a = LiveExecutor(state_dir=str(tmp_path / "a"))
    a._positions["TI-A"] = _pending(trade_id="TI-A")
    a.guard_pass = AsyncMock(side_effect=RuntimeError("venue down"))
    b = LiveExecutor(state_dir=str(tmp_path / "b"))
    b._positions["TI-B"] = _pending(trade_id="TI-B")
    b.guard_pass = AsyncMock(return_value=["LIMIT FILLED: LONG ETH/USDT"])
    eng = _engine([a, b])
    said: list = []
    monkeypatch.setattr(engine_mod, "audit", lambda log, msg, **kw: said.append((msg, kw)))
    assert _guard_once(eng) == "error"
    b.guard_pass.assert_awaited_once()
    assert eng.routed == [(b, ["LIMIT FILLED: LONG ETH/USDT"])]
    assert [kw["result"] for _m, kw in said] == ["ERROR"]
    assert "RuntimeError" in said[0][0] and "venue down" not in said[0][0], "the class, never the text"


def test_a_pass_past_its_cap_is_cancelled_and_said(tmp_path, monkeypatch):
    a = LiveExecutor(state_dir=str(tmp_path))
    a._positions["TI-A"] = _pending(trade_id="TI-A")

    async def hang(entry_halt=None):
        # Long against the cap, short against the suite: an uncapped pass
        # returns, and the outcome then reads "ran" rather than hanging.
        await asyncio.sleep(5)
        return []

    a.guard_pass = hang
    eng = _engine([a])
    said: list = []
    monkeypatch.setattr(engine_mod, "audit", lambda log, msg, **kw: said.append(kw.get("result")))
    with monitoring(position_guard_pass_cap_sec=0.01):
        assert _guard_once(eng) == "timeout"
    assert said == ["TIMEOUT"] and eng._last_position_guard["outcome"] == "timeout"


# ── one lock for every pass ────────────────────────────────────────────────


def test_the_ticks_pass_waits_for_a_guard_pass_in_flight(tmp_path):
    order: list[str] = []
    a = LiveExecutor(state_dir=str(tmp_path))
    a._positions["TI-A"] = _pending(trade_id="TI-A")
    release = None

    async def slow_guard(entry_halt=None):
        order.append("guard start")
        await release.wait()
        order.append("guard end")
        return []

    a.guard_pass = slow_guard
    eng = _engine([a])

    async def tick_body():
        order.append("tick pass")

    eng._run_positions_pass = tick_body

    async def go():
        nonlocal release
        release = asyncio.Event()
        guard = asyncio.create_task(eng._position_guard_once())
        await asyncio.sleep(0)
        tick = asyncio.create_task(eng._check_open_positions())
        await asyncio.sleep(0)
        assert order == ["guard start"], "the tick's pass is waiting on the lock"
        release.set()
        await asyncio.gather(guard, tick)

    with patch.object(type(CONFIG), "is_live", return_value=True):
        asyncio.run(go())
    assert order == ["guard start", "guard end", "tick pass"]


# ── the loop and its end ───────────────────────────────────────────────────


def test_the_loop_outlives_a_pass_that_raises(monkeypatch):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._running = True
    calls: list[int] = []

    async def once():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("first pass broke")
        if len(calls) == 3:
            eng._running = False
        return "ran"

    eng._position_guard_once = once
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(engine_mod.asyncio, "sleep", fake_sleep)
    with monitoring(position_guard_interval_sec=5.0):
        asyncio.run(eng._position_guard_loop())
    assert len(calls) == 3 and slept == [5.0, 5.0, 5.0]


def test_the_guard_ends_when_the_run_loop_ends():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._running = True

    async def go():
        eng._position_guard_once = AsyncMock(return_value="idle")
        eng._start_position_guard()
        guard = eng._position_guard_task

        async def run_loop():
            eng._end_guard_with(asyncio.current_task())
            await asyncio.sleep(3600)

        loop_task = asyncio.create_task(run_loop())
        await asyncio.sleep(0)
        loop_task.cancel()                  # bot.main's SIGTERM path
        with pytest.raises(asyncio.CancelledError):
            await loop_task
        for _ in range(10):
            if guard.done():
                break
            await asyncio.sleep(0)
        # Asked here, inside the loop: `asyncio.run` cancels whatever is left
        # when it returns, so a guard nothing ended would read cancelled after.
        return guard.cancelled()

    assert asyncio.run(go()) is True


def test_stop_cancels_and_awaits_the_guard_before_anything_closes():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._running = True

    async def go():
        eng._position_guard_once = AsyncMock(return_value="idle")
        eng._start_position_guard()
        guard = eng._position_guard_task
        await eng._stop_position_guard()
        return guard

    guard = asyncio.run(go())
    assert guard.done() and eng._position_guard_task is None


def test_stop_ends_the_guard_before_any_session_closes(monkeypatch):
    from types import SimpleNamespace

    from bot.core import scan_lane as lane_mod

    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._running = True
    order: list[str] = []

    def _step(name):
        async def _f(*a, **k):
            order.append(name)
        return _f

    eng._stop_position_guard = _step("guard")
    eng.ws_feed = SimpleNamespace(stop=_step("feed"))
    eng.scanner = SimpleNamespace(close=_step("scanner"))
    monkeypatch.setattr(lane_mod.scan_lane, "close_scanner", _step("lane"))
    eng.dashboard_pusher = None
    eng.live_executor = SimpleNamespace(close=_step("executor"))
    eng._user_executors = {"7": SimpleNamespace(close=_step("user executor"))}
    eng._transition = lambda *a, **k: None
    asyncio.run(eng.stop())
    assert order[0] == "guard"
    assert order.index("executor") > 0 and order.index("user executor") > 0


# ── the reading and the line ────────────────────────────────────────────────


def test_the_reading_says_unknown_until_a_pass_is_recorded():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    with patch.object(type(CONFIG), "is_live", return_value=True):
        r = eng.position_guard()
        assert (r["outcome"], r["age_s"], r["live"]) == (None, None, True)
        eng._record_guard("ran", orders=2)
        r = eng.position_guard()
    assert r["outcome"] == "ran" and r["orders"] == 2 and 0 <= r["age_s"] < 5


@pytest.mark.parametrize("outcome", ["busy", "off", "", None, 3])
def test_an_outcome_the_reading_does_not_know_is_not_a_pass(outcome):
    import time as _time
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._last_position_guard = {"outcome": outcome, "at": _time.monotonic(), "orders": 1}
    with patch.object(type(CONFIG), "is_live", return_value=True):
        r = eng.position_guard()
    assert (r["outcome"], r["age_s"], r["orders"]) == (None, None, None)


@pytest.mark.parametrize("guard,expect", [
    (None, ""),
    ({"live": False, "enabled": True}, ""),
    ({"live": True, "enabled": False}, "⚪ Position guard: off"),
    ({"live": True, "enabled": True, "interval_s": 10, "outcome": None},
     "⚪ Position guard: not run yet (every 10s)"),
    ({"live": True, "enabled": True, "interval_s": 10, "outcome": "ran", "age_s": 4, "orders": 1}, ""),
    ({"live": True, "enabled": True, "interval_s": 10, "outcome": "idle", "age_s": 4}, ""),
    ({"live": True, "enabled": True, "interval_s": 10, "outcome": "ran", "age_s": 95},
     "⚠️ Position guard: no recent pass (95s ago, every 10s)"),
    ({"live": True, "enabled": True, "interval_s": 10, "outcome": "timeout", "age_s": 5},
     "⚠️ Position guard: last pass timed out"),
    ({"live": True, "enabled": True, "interval_s": 10, "outcome": "error", "age_s": 5},
     "⚠️ Position guard: last pass failed"),
    ({"live": True, "enabled": True, "interval_s": 10, "outcome": "new-verdict"},
     "⚪ Position guard: not recorded"),
])
def test_the_status_line_names_every_state(guard, expect):
    line = position_guard_line(guard)
    if expect:
        assert line.startswith(expect), line
    else:
        assert line == ""


@pytest.mark.parametrize("has_reader", [True, False])
def test_status_hands_the_engines_guard_reading_to_the_card(monkeypatch, has_reader):
    from types import SimpleNamespace

    from bot.skills import start_commands as sc

    class _Rendered(Exception):
        pass

    seen: dict = {}

    def _render(**kw):
        seen.update(kw)
        raise _Rendered

    monkeypatch.setattr(sc, "render_status_card", _render)
    monkeypatch.setattr(sc, "entry_gate", lambda eng, uid: {"reasons": [], "blocked": False})
    monkeypatch.setattr(sc, "caller_risk", lambda eng, uid: None)
    reading = {"enabled": True, "live": True, "interval_s": 10.0, "outcome": "timeout"}
    snap = SimpleNamespace(equity_usd=1000.0, open_positions=0, daily_pnl=0.0,
                           max_drawdown_pct=0.0)
    engine = SimpleNamespace(user_portfolios={"u": SimpleNamespace(snapshot=lambda: snap)},
                             live_view=lambda uid: {"executor": None}, pending_ideas=[])
    if has_reader:
        engine.position_guard = lambda: reading
    handler = sc.StartCommands.__new__(sc.StartCommands)
    handler.engine = engine
    handler._status_market_bias = lambda: "Neutral"
    handler._tick_liveness = lambda e: (False, None)
    handler._tick_age_s = lambda e: None
    saved = CONFIG.simulation_mode
    object.__setattr__(CONFIG, "simulation_mode", True)    # the paper book: fewest reads
    try:
        with pytest.raises(_Rendered):
            asyncio.run(handler.status_card_text("u"))
    finally:
        object.__setattr__(CONFIG, "simulation_mode", saved)
    assert seen["position_guard"] == (reading if has_reader else None)


def test_the_status_card_carries_the_guard_line_and_leaves_a_healthy_one_out():
    from bot.formatters.rich_cards import render_status_card

    def card(guard):
        return render_status_card(
            mode="LIVE", active=True, equity=1000.0, open_positions=1, daily_pnl=0.5,
            drawdown=0.01, max_drawdown=0.1, market_bias="Neutral",
            position_watch={"outcome": "tick", "unwatched_streak": 0, "age_s": 9},
            position_guard=guard)

    off = card({"live": True, "enabled": False})
    assert "- \u26aa Position guard: off" in off
    healthy = card({"live": True, "enabled": True, "interval_s": 10.0,
                    "outcome": "ran", "age_s": 3, "orders": 1})
    assert "Position guard" not in healthy


@pytest.mark.parametrize("age", [-3, True, None, "5"])
def test_an_age_that_is_no_reading_is_left_out_not_printed(age):
    line = position_guard_line({"live": True, "enabled": True, "interval_s": 10,
                                "outcome": "timeout", "age_s": age})
    assert line == ("\u26a0\ufe0f Position guard: last pass timed out "
                    "\u2014 the tick still reads resting orders")


def test_an_interval_of_nothing_is_not_said_as_every_0s():
    line = position_guard_line({"live": True, "enabled": True, "interval_s": 0, "outcome": None})
    assert line == "\u26aa Position guard: not run yet"


def test_a_healthy_guard_is_said_when_asked():
    line = position_guard_line({"live": True, "enabled": True, "interval_s": 10,
                                "outcome": "ran", "age_s": 4, "orders": 2}, verbose=True)
    assert line == "✅ Position guard: 2 resting order(s) read (every 10s, 4s ago)"
