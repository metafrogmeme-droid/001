"""Open positions must be watched on every tick — including the ticks that end early.

`_check_open_positions` is the SL/TP monitor. Its call site carried the
comment "this is the SL/TP monitor on every tick", and it was not: on the
normal path it runs LAST, after scan and analyze, and two ordinary outcomes
reached the end of the tick without it.

  * the scan finding nothing — `if not signals: ... return`, a routine
    result rather than an error;
  * the analyze phase blowing its per-phase cap — `_phase(..., "analyze")`
    defaults to fatal=True, deliberately, and the re-raise unwound the rest
    of the tick with it.

Neither is a crash, so neither showed up as one. Both left money already at
risk unmonitored until the next cycle, and a persistently slow analyze phase
leaves it unmonitored for as long as the slowness lasts — which is exactly
when an exchange is struggling and a stop matters most.

These tests drive _tick_guarded, not _tick, because the guarantee lives in
the guard's `finally`. They fail against the old code.
"""
from __future__ import annotations

import asyncio

import pytest


class _Recorder:
    """A stand-in engine exposing only what _tick_guarded touches."""

    def __init__(self, tick):
        self.calls: list[str] = []
        self._tick_impl = tick
        self._positions_monitored_tick = False

    # -- the real methods under test, bound from the class ----------------
    from bot.core.engine import RuneClawEngine as _E
    _tick_guarded = _E._tick_guarded
    _backstop_position_monitor = _E._backstop_position_monitor
    del _E

    async def _tick(self):
        self._positions_monitored_tick = False
        return await self._tick_impl(self)

    async def _check_open_positions(self):
        self.calls.append("positions")
        # The real method records completion at its END; mirror that, since
        # the flag is what tells the backstop "already watched".
        self._positions_monitored_tick = True

    async def _phase(self, coro, what: str, fatal: bool = True):
        self.calls.append(f"phase:{what}")
        return await coro


@pytest.fixture(autouse=True)
def _no_hard_cap(monkeypatch):
    """Exercise the cap<=0 branch and the wait_for branch in separate tests."""
    from bot.config import CONFIG
    object.__setattr__(CONFIG.monitoring, "tick_hard_timeout_sec", 0.0)
    yield


def _run(eng):
    return asyncio.run(eng._tick_guarded())


class TestTicksThatEndEarly:
    def test_a_tick_that_returns_without_monitoring_gets_the_backstop(self):
        async def tick(self):
            return  # the "no signals" shape: return before the monitor

        eng = _Recorder(tick)
        _run(eng)
        assert "phase:positions (backstop)" in eng.calls, \
            "a tick that returned before its position check left stops unwatched"

    def test_a_tick_that_raises_gets_the_backstop(self):
        async def tick(self):
            raise asyncio.TimeoutError("analyze blew its cap")

        eng = _Recorder(tick)
        with pytest.raises(asyncio.TimeoutError):
            _run(eng)
        assert "phase:positions (backstop)" in eng.calls, \
            "a raised analyze phase unwound the tick past its position check"

    def test_the_backstop_does_not_double_check(self):
        async def tick(self):
            await self._phase(self._check_open_positions(), "positions")

        eng = _Recorder(tick)
        _run(eng)
        assert eng.calls.count("positions") == 1
        assert not any("backstop" in c for c in eng.calls), \
            "the monitor already ran; the backstop must stay out of the way"

    def test_the_backstop_never_takes_the_loop_down(self):
        async def tick(self):
            return

        eng = _Recorder(tick)

        async def _boom(coro, what, fatal=True):
            coro.close()
            raise RuntimeError("exchange unreachable")

        eng._phase = _boom  # type: ignore[method-assign]
        _run(eng)  # must not raise — a backstop that kills the loop is not one

    def test_a_cancelled_backstop_still_propagates(self):
        async def tick(self):
            return

        eng = _Recorder(tick)

        async def _cancel(coro, what, fatal=True):
            coro.close()
            raise asyncio.CancelledError()

        eng._phase = _cancel  # type: ignore[method-assign]
        with pytest.raises(asyncio.CancelledError):
            _run(eng)


class TestUnderTheHardCap:
    def test_backstop_runs_on_the_wait_for_path_too(self):
        from bot.config import CONFIG
        object.__setattr__(CONFIG.monitoring, "tick_hard_timeout_sec", 30.0)
        try:
            async def tick(self):
                return

            eng = _Recorder(tick)
            _run(eng)
            assert "phase:positions (backstop)" in eng.calls
        finally:
            object.__setattr__(CONFIG.monitoring, "tick_hard_timeout_sec", 0.0)
