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


class TestTheBackstopDoesNotClaimItRan:
    """Returning is not evidence the monitor ran.

    `_phase(..., fatal=False)` is what keeps this call from taking the tick
    loop down, and it buys that by RETURNING None on a timeout rather than
    raising. So a backstop cancelled at its cap — having watched nothing —
    came back through the same path as one that completed, and the original
    `else:` branch audited it as RAN.

    That is a failed read rendered as a success, on the guard that exists
    because the stops were already missed once. `_check_open_positions` sets
    the flag at its END, so it is the only thing in the process that can tell
    the two apart.
    """

    @staticmethod
    def _capture(monkeypatch):
        seen: list[dict] = []
        import bot.core.engine as eng_mod
        monkeypatch.setattr(
            eng_mod, "audit",
            lambda *a, **k: seen.append({"msg": a[1] if len(a) > 1 else "", **k}))
        return seen

    @staticmethod
    def _tick_that_ends_early():
        async def tick(self):
            return
        return tick

    def test_a_timed_out_backstop_is_not_reported_as_having_run(self, monkeypatch):
        seen = self._capture(monkeypatch)
        eng = _Recorder(self._tick_that_ends_early())

        async def _timed_out(coro, what, fatal=True):
            # Precisely what _phase(fatal=False) does when it blows its cap:
            # the coroutine is cancelled and None is returned, not raised.
            coro.close()
            return None

        eng._phase = _timed_out  # type: ignore[method-assign]
        _run(eng)

        results = [a.get("result") for a in seen]
        assert "RAN" not in results, (
            "a backstop that was cancelled at its cap reported that it ran — "
            "the stops were not watched and the log says they were"
        )
        assert "INCOMPLETE" in results

    def test_the_incomplete_line_says_positions_are_unwatched(self, monkeypatch):
        seen = self._capture(monkeypatch)
        eng = _Recorder(self._tick_that_ends_early())

        async def _timed_out(coro, what, fatal=True):
            coro.close()
            return None

        eng._phase = _timed_out  # type: ignore[method-assign]
        _run(eng)

        line = next(a["msg"] for a in seen if a.get("result") == "INCOMPLETE")
        assert "unwatched" in line, f"the operator is not told what it means: {line!r}"

    def test_a_completed_backstop_still_reports_that_it_ran(self, monkeypatch):
        """The honest fix must not blank the good news."""
        seen = self._capture(monkeypatch)
        eng = _Recorder(self._tick_that_ends_early())
        _run(eng)

        results = [a.get("result") for a in seen]
        assert "RAN" in results
        assert "INCOMPLETE" not in results

    def test_a_failed_backstop_reports_only_the_error(self, monkeypatch):
        seen = self._capture(monkeypatch)
        eng = _Recorder(self._tick_that_ends_early())

        async def _boom(coro, what, fatal=True):
            coro.close()
            raise RuntimeError("exchange unreachable")

        eng._phase = _boom  # type: ignore[method-assign]
        _run(eng)

        results = [a.get("result") for a in seen]
        assert results.count("ERROR") == 1
        assert "RAN" not in results and "INCOMPLETE" not in results, (
            "an error must not also emit a completion verdict"
        )
