"""A failing tick loop keeps watching the stops, and a shutdown does not trade.

Four defects in the engine's long-running loop, each driven here:

1. THE BACKOFF STOPPED THE SL/TP MONITOR. After a failed tick `run()` slept
   the whole backoff (up to 300s) with nothing watching the book. With the
   analyze phase timing out at its 300s cap every tick, the monitor ran once
   every 605s (the phase, the sleep, the scan). The backoff is cut into steps
   of the scan interval now and the monitor runs between them, so the longest
   gap is what a slow but successful tick already leaves: one scan interval
   plus the tick.

2. `base * 2**n` RAISED OverflowError once n reached 1024, out of the failure
   handler and out of `run()`. The exponent is bounded.

3. A SHUTDOWN RAN A FULL MONITOR PASS. `bot.main`'s SIGTERM handler cancels
   the engine task; `_tick_guarded`'s `finally` then ran the SL/TP monitor,
   which cancels stops and sends closes, while a supervisor counted down to
   SIGKILL. A cancelled tick gets no backstop now; a hard timeout still does.

4. THE MONITOR'S OWN WATCHDOG RAN ON SUCCESSFUL TICKS ONLY, so during a
   failure streak nothing checked that the proactive monitor was alive.

The loop is driven for real (`run`, `_tick_guarded`, `_phase`,
`_backstop_position_monitor`, `_sleep_watching_stops`) on a virtual clock:
the engine module's `asyncio.sleep` and `time.monotonic` advance a counter
instead of waiting.
"""

from __future__ import annotations

import asyncio
import time as _time
import types

import pytest

import bot.core.engine as eng_mod
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine

SCAN_S = 60.0          # the smart scan interval the stand-in reports
VCAP = 300.0           # the analyze cap, charged in virtual seconds
SCAN_COST = 5.0        # the scan, in virtual seconds


class _Clock:
    def __init__(self):
        self.now = 1_000_000.0
        self.sleeps: list[float] = []

    async def sleep(self, d, *a, **k):
        d = float(d or 0)
        if d > 0:
            self.sleeps.append(d)
            self.now += d
        await _REAL_SLEEP(0)


_REAL_SLEEP = asyncio.sleep


class _AsyncioView:
    """The engine's `asyncio`, with a virtual `sleep` and nothing else changed."""

    def __init__(self, clock):
        self.sleep = clock.sleep

    def __getattr__(self, name):
        return getattr(asyncio, name)


class _TimeView:
    def __init__(self, clock):
        self.monotonic = lambda: clock.now

    def __getattr__(self, name):
        return getattr(_time, name)


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(eng_mod, "asyncio", _AsyncioView(c))
    monkeypatch.setattr(eng_mod, "time", _TimeView(c))
    monkeypatch.setattr(eng_mod, "audit",
                        lambda _log, msg, **kw: c.__dict__.setdefault("audits", []).append((msg, kw)))
    return c


@pytest.fixture
def monitoring():
    """Set CONFIG.monitoring fields for one test and put them back."""
    saved: dict = {}

    def set_(**kw):
        for k, v in kw.items():
            saved.setdefault(k, getattr(CONFIG.monitoring, k))
            object.__setattr__(CONFIG.monitoring, k, v)
    yield set_
    for k, v in saved.items():
        object.__setattr__(CONFIG.monitoring, k, v)


def _engine(clock, *, tick, stop_after):
    """A RuneClawEngine with the loop's own methods and a stubbed world."""
    e = RuneClawEngine.__new__(RuneClawEngine)
    e.clock = clock
    e._running = True
    e._transition = lambda *a, **k: None
    e.ws_feed = types.SimpleNamespace(start=lambda: _REAL_SLEEP(0),
                                      subscribe=lambda *a: None)
    e.dashboard_pusher = None

    async def _refit():
        return []
    e._refit_stale_learned_curves = _refit
    e._compute_smart_scan_interval = lambda: SCAN_S

    async def _nop():
        return None
    for n in ("_maybe_ping_healthcheck", "_maybe_check_monitor_liveness",
              "_maybe_pull_web_credentials", "_maybe_flatten_web_requests",
              "_maybe_publish_proofofpnl", "_maybe_publish_user_leaderboards"):
        setattr(e, n, _nop)
    e._maybe_snapshot_board_season = lambda: None
    e.risk = types.SimpleNamespace(record_warning=lambda *a: None)
    e._record_phase_duration = lambda *a, **k: None
    e._record_position_watch = lambda *a, **k: None
    e.checks = []
    e.tick_starts = []
    e.tick_ends = []

    async def _check_open_positions():
        e.checks.append(clock.now)
        e._positions_monitored_tick = True
    e._check_open_positions = _check_open_positions

    async def _tick():
        e.tick_starts.append(clock.now)
        e._positions_monitored_tick = False
        try:
            await tick(e)
        finally:
            e.tick_ends.append(clock.now)
            if len(e.tick_starts) >= stop_after:
                e._running = False
    e._tick = _tick
    return e


async def _analyze_times_out(e):
    """The tick the 2026-09-16 incident had: scan, then analyze hits its cap."""
    e.clock.now += SCAN_COST

    async def parked():
        await _REAL_SLEEP(3600)
    try:
        await e._phase(parked(), "analyze")
    finally:
        e.clock.now += VCAP        # what the cap cost, in virtual time


class TestTheBackoffWatchesTheStops:
    @pytest.mark.asyncio
    async def test_the_longest_unwatched_gap_is_one_scan_plus_the_tick(self, clock, monitoring):
        monitoring(tick_phase_timeout_sec=0.02, tick_hard_timeout_sec=0.0)
        e = _engine(clock, tick=_analyze_times_out, stop_after=8)
        await e.run()

        gaps = [b - a for a, b in zip(e.checks, e.checks[1:])]
        assert len(e.tick_starts) == 8
        assert gaps, "the monitor never ran twice"
        assert max(gaps) <= SCAN_S + SCAN_COST + VCAP, (
            f"the stops went {max(gaps):.0f}s unwatched during the backoff: {gaps}")

    @pytest.mark.asyncio
    async def test_the_backoff_itself_is_not_shortened(self, clock, monitoring):
        # The passes run BETWEEN the steps; the wait between two ticks is still
        # the whole backoff, so a persistent failure is still spared the venue.
        monitoring(tick_phase_timeout_sec=0.02, tick_hard_timeout_sec=0.0)
        e = _engine(clock, tick=_analyze_times_out, stop_after=6)
        await e.run()

        waits = [s - end for end, s in zip(e.tick_ends, e.tick_starts[1:])]
        expected = [min(SCAN_S * 2 ** n, 300.0) for n in range(1, 6)]
        assert waits == expected, waits

    @pytest.mark.asyncio
    async def test_a_thousand_failures_do_not_crash_the_loop(self, clock, monitoring):
        monitoring(tick_phase_timeout_sec=0.0, tick_hard_timeout_sec=0.0)

        async def fails(e):
            raise RuntimeError("venue down")
        e = _engine(clock, tick=fails, stop_after=1030)
        await e.run()          # 2**1024 as a float raised OverflowError here
        assert len(e.tick_starts) == 1030
        waits = [s - end for end, s in zip(e.tick_ends, e.tick_starts[1:])]
        assert waits[-1] == 300.0


class TestTheStepsAreStamped:
    @pytest.mark.asyncio
    async def test_the_watchdog_is_told_about_every_step_and_every_pass(self, clock, monitoring):
        # The stall watchdog reads `_next_tick_due_ts`. A pass takes up to the
        # per-phase cap, so a declared wait that a bounded pass overruns would
        # page TICK_STALL over a loop that is doing its job.
        monitoring(tick_phase_timeout_sec=300.0)
        cap = float(CONFIG.monitoring.tick_phase_timeout_sec)
        e = RuneClawEngine.__new__(RuneClawEngine)
        e._next_tick_due_ts = None
        overruns: list = []
        real = clock.sleep

        async def watched_sleep(d, *a, **k):
            if e._next_tick_due_ts is None or clock.now + d > e._next_tick_due_ts:
                overruns.append(("sleep", clock.now, d, e._next_tick_due_ts))
            await real(d)
        eng_mod.asyncio.sleep = watched_sleep
        passes = []

        async def slow_pass(*, during_backoff=False):
            assert during_backoff is True
            passes.append(clock.now)
            clock.now += cap - 1           # a pass that nearly uses its cap
            if clock.now > e._next_tick_due_ts:
                overruns.append(("pass", clock.now, e._next_tick_due_ts))
        e._backstop_position_monitor = slow_pass

        await e._sleep_watching_stops(300.0, 60.0)

        assert overruns == []
        assert len(passes) == 4, passes
        assert sum(clock.sleeps) == 300.0

    # 0 is the one step the product can hand over (a scan interval of 0); a
    # step as long as the wait takes the loop, which makes one sleep and no
    # pass. Both are one plain sleep.
    @pytest.mark.parametrize("step", [0, 300.0, 500.0])
    @pytest.mark.asyncio
    async def test_no_usable_step_is_one_plain_sleep(self, clock, step):
        e = RuneClawEngine.__new__(RuneClawEngine)
        passes = []

        async def pass_(**kw):
            passes.append(kw)
        e._backstop_position_monitor = pass_
        await e._sleep_watching_stops(300.0, step)
        assert clock.sleeps == [300.0]
        assert passes == []
        assert e._next_tick_due_ts == pytest.approx(clock.now)


def _watch_engine(clock, *, finishes: bool):
    e = RuneClawEngine.__new__(RuneClawEngine)
    e.names = []
    e.watched = []
    e._record_phase_duration = lambda what, *a, **k: e.names.append(what)
    e._record_position_watch = lambda outcome: e.watched.append(outcome)

    async def _check():
        if finishes:
            e._positions_monitored_tick = True
    e._check_open_positions = _check
    return e


class TestTheBackoffPassSaysWhatItIs:
    @pytest.mark.asyncio
    async def test_a_stale_tick_flag_does_not_stand_in_for_the_pass(self, clock, monitoring):
        monitoring(tick_phase_timeout_sec=5.0)
        e = _watch_engine(clock, finishes=False)
        e._positions_monitored_tick = True     # left over from the failed tick
        await e._backstop_position_monitor(during_backoff=True)

        assert e.names == ["positions (backoff)"], "the pass never ran"
        assert e.watched == ["incomplete"]
        (msg, kw), = clock.audits
        assert kw["result"] == "INCOMPLETE"
        assert "backing off after a failed tick" in msg
        assert "unwatched for this pass" in msg

    @pytest.mark.asyncio
    async def test_a_finished_pass_is_recorded_as_a_backstop(self, clock, monitoring):
        monitoring(tick_phase_timeout_sec=5.0)
        e = _watch_engine(clock, finishes=True)
        e._positions_monitored_tick = False
        await e._backstop_position_monitor(during_backoff=True)

        assert e.watched == ["backstop"]
        (msg, kw), = clock.audits
        assert kw["result"] == "RAN" and "backing off" in msg

    @pytest.mark.asyncio
    async def test_the_tick_path_is_unchanged(self, clock, monitoring):
        monitoring(tick_phase_timeout_sec=5.0)
        e = _watch_engine(clock, finishes=True)
        e._positions_monitored_tick = True
        await e._backstop_position_monitor()
        assert e.names == [] and e.watched == ["tick"]

        e2 = _watch_engine(clock, finishes=True)
        e2._positions_monitored_tick = False
        await e2._backstop_position_monitor()
        assert e2.names == ["positions (backstop)"]
        assert "Tick ended before its position check" in clock.audits[-1][0]


class _Guarded:
    _tick_guarded = RuneClawEngine._tick_guarded

    def __init__(self, tick):
        self.calls: list[str] = []
        self._tick_body = tick

    async def _tick(self):
        await self._tick_body(self)

    async def _backstop_position_monitor(self, **kw):
        self.calls.append("backstop")


async def _parked(self):
    await _REAL_SLEEP(3600)


class TestAShutdownDoesNotTrade:
    @pytest.mark.parametrize("cap", [0.0, 900.0])
    @pytest.mark.asyncio
    async def test_a_cancelled_tick_runs_no_backstop(self, monitoring, cap):
        monitoring(tick_hard_timeout_sec=cap)
        g = _Guarded(_parked)
        task = asyncio.ensure_future(g._tick_guarded())
        await _REAL_SLEEP(0.01)
        task.cancel()                      # what SIGTERM does
        with pytest.raises(asyncio.CancelledError):
            await task
        assert g.calls == [], "a stop request sent the book through the SL/TP monitor"

    @pytest.mark.asyncio
    async def test_a_hard_timeout_still_runs_the_backstop(self, monitoring, monkeypatch):
        monkeypatch.setattr(eng_mod, "audit", lambda *a, **k: None)
        monitoring(tick_hard_timeout_sec=0.02)
        g = _Guarded(_parked)
        with pytest.raises(asyncio.TimeoutError):
            await g._tick_guarded()
        assert g.calls == ["backstop"]

    @pytest.mark.parametrize("cap", [0.0, 900.0])
    @pytest.mark.asyncio
    async def test_a_failed_tick_still_runs_the_backstop(self, monitoring, cap):
        monitoring(tick_hard_timeout_sec=cap)

        async def boom(self):
            raise RuntimeError("scan failed")
        g = _Guarded(boom)
        with pytest.raises(RuntimeError):
            await g._tick_guarded()
        assert g.calls == ["backstop"]

    @pytest.mark.parametrize("cap", [0.0, 900.0])
    @pytest.mark.asyncio
    async def test_a_good_tick_still_runs_it(self, monitoring, cap):
        monitoring(tick_hard_timeout_sec=cap)

        async def ok(self):
            return None
        g = _Guarded(ok)
        await g._tick_guarded()
        assert g.calls == ["backstop"]


class TestTheMonitorIsWatchedWhileTicksFail:
    @pytest.mark.asyncio
    async def test_a_dead_monitor_is_reported_during_a_failure_streak(self, clock, monitoring):
        monitoring(tick_phase_timeout_sec=0.0, tick_hard_timeout_sec=0.0,
                   monitor_liveness_timeout_sec=300.0)

        async def fails(e):
            raise RuntimeError("venue down")
        e = _engine(clock, tick=fails, stop_after=3)
        e._maybe_check_monitor_liveness = (
            RuneClawEngine._maybe_check_monitor_liveness.__get__(e))
        e._proactive_monitor = types.SimpleNamespace(last_loop_ts=clock.now - 10_000)
        e._last_monitor_liveness_check = None
        told: list = []

        async def cb(age):
            told.append(age)
        e._monitor_stale_callback = cb
        pinged: list = []

        async def ping():
            pinged.append(clock.now)
        e._maybe_ping_healthcheck = ping

        await e.run()

        assert told, "a stalled monitor went unreported while every tick failed"
        assert told[0] == 10_000
        # Throttled to one report per window, on the failure path as on the
        # success path.
        assert all(b - a >= 300.0 for a, b in zip(told, told[1:])), told
        assert pinged == [], (
            "the external dead-man's switch was fed by failing ticks")
