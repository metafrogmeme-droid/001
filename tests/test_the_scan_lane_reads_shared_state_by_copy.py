"""The scan lane's thread and the main loop share four pieces of state.

The analysis runs on the scan lane's own thread while the main loop runs the
tick and, now, the position guard between ticks. Each site below was a
Python-level loop over a container the other thread can change:

- ``LiveExecutor.open_positions`` / ``closed_positions`` iterated
  ``_positions`` while a positions pass adds and removes rows;
- ``RiskEngine._price_history_key`` iterated ``_price_history`` while the
  tick records prices;
- ``RuneClawEngine._outcome_regime`` iterated the analyzer's regimes while
  the analyzer adds symbols; its ``except`` turned the raise into a silent
  wrong tag (the risk engine's fallback regime);
- ``RiskEngine``'s warning deque was pruned by check-then-``popleft`` from
  both threads, so one thread could pop a warning the other had just
  recorded.

The fifth is not a container: the analysis' position-limit check read the
operator executor's ccxt client, which belongs to the main loop, from the
lane's thread. That read is now handed to the main loop
(``exchange_sync._on_the_clients_loop``).

A thread switch cannot be scheduled from a test, so each test runs the other
thread's write at the exact point the loop yields: inside a read the loop
makes (a ``status`` read, a ``normalize_symbol`` call, a deque index).
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

from bot.core import engine as engine_mod
from bot.core import exchange_sync as xs
from bot.core import live_executor as le
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine


class _Row:
    """A position row whose first ``status`` read runs ``on_read`` -- the
    other thread's write landing mid-loop. The properties read only
    ``status`` and ``trade_id``."""

    def __init__(self, trade_id, status, on_read=None):
        self.trade_id = trade_id
        self._status = status
        self._on_read = on_read

    @property
    def status(self):
        hook, self._on_read = self._on_read, None
        if hook is not None:
            hook()
        return self._status


def _executor_with(tmp_path, rows):
    ex = LiveExecutor(state_dir=str(tmp_path))
    ex._closed_trades = []
    for r in rows:
        ex._positions[r.trade_id] = r
    return ex


def test_open_positions_survives_a_row_added_while_it_reads(tmp_path):
    ex = _executor_with(tmp_path, [])
    added = _Row("t-new", "pending_fill")
    ex._positions["t1"] = _Row(
        "t1", "open", on_read=lambda: ex._positions.__setitem__("t-new", added))
    ex._positions["t2"] = _Row("t2", "closed")
    ex._positions["t3"] = _Row("t3", "pending_fill")

    ids = sorted(p.trade_id for p in ex.open_positions)

    # The book as it stood when the read began: open and resting rows in,
    # the closed row out, and the row added mid-read left to the next read.
    assert ids == ["t1", "t3"]
    assert "t-new" in ex._positions


def test_closed_positions_survives_a_row_removed_while_it_reads(tmp_path):
    ex = _executor_with(tmp_path, [])
    ex._positions["t1"] = _Row(
        "t1", "closed", on_read=lambda: ex._positions.pop("t3"))
    ex._positions["t2"] = _Row("t2", "open")
    ex._positions["t3"] = _Row("t3", "closed")

    ids = sorted(p.trade_id for p in ex.closed_positions)

    assert ids == ["t1", "t3"]
    assert "t3" not in ex._positions


def _risk(tmp_path) -> RiskEngine:
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0),
                      state_file=str(tmp_path / "risk_state.json"))


def test_price_history_lookup_survives_a_price_recorded_while_it_reads(tmp_path):
    eng = _risk(tmp_path)
    eng._price_history.clear()
    eng._price_history["ETH/USDT"] = [1.0]
    eng._price_history["BTC/USDT"] = [2.0]
    real = le.normalize_symbol
    fired = []

    def _normalize(sym):
        if fired == [] and sym != "BTC/USDT:USDT":
            fired.append(sym)
            eng._price_history["SOL/USDT"] = [3.0]   # the tick records a price
        return real(sym)

    with patch.object(le, "normalize_symbol", _normalize):
        key = eng._price_history_key("BTC/USDT:USDT")

    assert fired, "the fixture never ran the other thread's write"
    assert key == "BTC/USDT"
    # A symbol with no history keeps its own spelling.
    assert eng._price_history_key("DOGE/USDT:USDT") == "DOGE/USDT:USDT"


def test_a_closed_trade_is_tagged_with_the_analyzers_regime_while_it_adds_symbols():
    regimes = {"ETH/USDT": "RANGE", "BTC/USDT": SimpleNamespace(value="TREND_UP")}
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.analyzer = SimpleNamespace(_current_regimes=regimes)
    eng.risk = SimpleNamespace(_current_regime="UNKNOWN")
    real = engine_mod.normalize_symbol
    fired = []

    def _normalize(sym):
        if fired == [] and sym in regimes:
            fired.append(sym)
            regimes["SOL/USDT"] = "TREND_DOWN"     # the analyzer adds a symbol
        return real(sym)

    with patch.object(engine_mod, "normalize_symbol", _normalize):
        tag = eng._outcome_regime("BTC/USDT:USDT")

    assert fired, "the fixture never ran the other thread's write"
    # Not the fallback: a raise inside the lookup is swallowed into the risk
    # engine's regime, which would tag this trade UNKNOWN.
    assert tag == "TREND_UP"
    # And the fallback is still what an unknown symbol gets.
    assert eng._outcome_regime("XRP/USDT:USDT") == "UNKNOWN"


class _RacingDeque(deque):
    """A deque whose first index read starts ``other`` on a second thread and
    gives it ``wait`` seconds to run -- long enough to finish when nothing
    stops it, and to block when the deque's lock is held."""

    def __init__(self, *a, other=None, wait=0.5, **kw):
        super().__init__(*a, **kw)
        self.other = other
        self.wait = wait
        self.thread = None
        self.errors: list[BaseException] = []

    def __getitem__(self, i):
        value = super().__getitem__(i)
        other, self.other = self.other, None
        if other is not None:
            def _run():
                try:
                    other()
                except BaseException as exc:   # surfaced by the test
                    self.errors.append(exc)
            self.thread = threading.Thread(target=_run, daemon=True)
            self.thread.start()
            self.thread.join(self.wait)
        return value


def test_a_warning_recorded_while_the_lane_prunes_is_kept(tmp_path):
    eng = _risk(tmp_path)
    stale = eng._now() - eng._warning_rate_window - 10.0
    eng._warning_rate_tripped = True
    eng._warning_rate_trip_key = "x"
    # The scan lane prunes (inside evaluate) while the main loop records "y".
    events = _RacingDeque([(stale, "x")], maxlen=500,
                          other=lambda: eng.record_warning("y"))
    eng._warning_events = events

    eng._refresh_warning_rate_breaker()
    events.thread.join(5)

    assert not events.thread.is_alive()
    assert events.errors == []
    # The stale event is gone and the fresh one survived both prunes.
    assert [k for _, k in eng._warning_events] == ["y"]
    assert eng.warning_rate_summary() == {"y": 1}


def test_two_prunes_of_the_last_stale_warning_do_not_raise(tmp_path):
    eng = _risk(tmp_path)
    stale = eng._now() - eng._warning_rate_window - 10.0
    eng._warning_rate_tripped = True
    eng._warning_rate_trip_key = "x"
    events = _RacingDeque([(stale, "x")], maxlen=500,
                          other=eng._refresh_warning_rate_breaker)
    eng._warning_events = events

    eng._refresh_warning_rate_breaker()
    events.thread.join(5)

    assert events.errors == []
    assert list(eng._warning_events) == []
    assert eng._warning_rate_tripped is False


def test_recording_a_warning_never_waits_on_an_evaluate_in_progress(tmp_path):
    eng = _risk(tmp_path)
    held, release = threading.Event(), threading.Event()

    def _evaluate_in_progress():
        with eng._lock:
            held.set()
            release.wait(10)

    lane = threading.Thread(target=_evaluate_in_progress, daemon=True)
    lane.start()
    assert held.wait(5)
    try:
        done = threading.Event()
        main = threading.Thread(
            target=lambda: (eng.record_warning("z"), done.set()), daemon=True)
        main.start()
        assert done.wait(5), "record_warning blocked on evaluate's lock"
        assert eng.warning_rate_summary().get("z") == 1
    finally:
        release.set()
        lane.join(5)


# ── the venue read the analysis makes ───────────────────────────────────────

def test_the_lanes_position_count_is_read_on_the_loop_that_owns_the_client(monkeypatch):
    seen = []

    async def _fetch(engine):
        seen.append((threading.get_ident(), asyncio.get_running_loop()))
        return SimpleNamespace(rows=[object(), object()], unreadable=[])

    monkeypatch.setattr(xs, "_fetch_exchange_positions", _fetch)
    engine = SimpleNamespace()
    lane = {}

    async def _main():
        engine._main_loop = asyncio.get_running_loop()

        def _lane_thread():
            lane["thread"] = threading.get_ident()
            lane["count"] = asyncio.run(xs.get_exchange_position_count(engine))

        t = threading.Thread(target=_lane_thread, daemon=True)
        t.start()
        deadline = time.monotonic() + 10
        while t.is_alive() and time.monotonic() < deadline:
            await asyncio.sleep(0.01)    # the main loop stays free to serve it
        assert not t.is_alive(), "the lane's read never came back"
        return threading.get_ident(), engine._main_loop

    xs.invalidate_position_count_cache()
    try:
        main_thread, main_loop = asyncio.run(_main())
    finally:
        xs.invalidate_position_count_cache()

    assert lane["count"] == 2
    assert lane["thread"] != main_thread
    # Fetched on the main loop's thread, through the main loop.
    assert seen == [(main_thread, main_loop)]


def test_a_read_with_no_other_loop_to_hand_to_runs_where_it_is():
    async def _where():
        return asyncio.get_running_loop()

    stopped = asyncio.new_event_loop()
    stopped.close()

    async def _main():
        here = asyncio.get_running_loop()
        # On the owning loop itself.
        assert await xs._on_the_clients_loop(SimpleNamespace(_main_loop=here), _where()) is here
        # No owning loop recorded (paper, a CLI, a test).
        assert await xs._on_the_clients_loop(SimpleNamespace(), _where()) is here
        # The owning loop has stopped: handing to it would never come back.
        assert await xs._on_the_clients_loop(
            SimpleNamespace(_main_loop=stopped), _where()) is here

    asyncio.run(_main())


def test_the_engine_records_the_loop_it_hands_the_lane_from(monkeypatch):
    from bot.core import scan_lane as lane_mod

    async def _run(factory):
        return "batch done"

    monkeypatch.setattr(lane_mod.scan_lane, "run", _run)
    eng = RuneClawEngine.__new__(RuneClawEngine)

    async def _main():
        result = await eng._off_gateway_loop(lambda: None)
        return result, asyncio.get_running_loop()

    result, loop = asyncio.run(_main())
    assert result == "batch done"
    assert eng._main_loop is loop
