"""TICK_STALL self-diagnosis — when the engine loop hangs, the alert now
carries WHERE it is parked.

PRODUCTION INCIDENT (2026-07-28, live account). The tick hung for the full
900s hard timeout and the "stack diagnosis" attached to the alert was ONE
frame: "engine.py:2226 in run" — the line `await self._tick_guarded()`, i.e.
the outermost suspension point. It restated that the tick was hung and said
nothing about where. Task.get_stack() does not descend into nested awaited
coroutines, so the one thing the diagnosis existed to provide was the one
thing it could not provide. The chain is now walked explicitly through
cr_await. Two companion defects from the same incident are covered below:
repeat pages for a single hang, and /status being unable to answer "is the
loop alive?" at all.

The monitor shares the engine's event loop, so a fired TICK_STALL proves the
loop is alive and the hang is a suspended await; Task.get_stack() can read
the parked frames in place. The engine task is identified by its OUTERMOST
frame being Engine.run in engine.py (so a stuck post-tick maintenance await
still matches). Everything is fail-open: no diagnosis ever blocks the alert.
"""

import time
from types import SimpleNamespace

from bot.core import proactive_monitor as pm_mod
from bot.core.proactive_monitor import ProactiveMonitor


def _frame(filename, lineno, name):
    return SimpleNamespace(
        f_code=SimpleNamespace(co_filename=filename, co_name=name),
        f_lineno=lineno)


def _stalled_pm():
    stale = time.monotonic() - ProactiveMonitor.TICK_STALL_THRESHOLD_S - 60
    return ProactiveMonitor(SimpleNamespace(_last_tick_started_ts=stale))


class TestFrameSummaries:
    def test_formats_basename_line_and_name(self):
        frames = [
            _frame("/srv/bot/core/engine.py", 2182, "run"),
            _frame("/srv/bot/core/live_executor.py", 440, "_sync"),
        ]
        assert ProactiveMonitor._frame_summaries(frames) == [
            "engine.py:2182 in run",
            "live_executor.py:440 in _sync",
        ]

    def test_broken_frames_are_skipped_not_fatal(self):
        frames = [object(), _frame("/x/engine.py", 1, "run")]
        assert ProactiveMonitor._frame_summaries(frames) == ["engine.py:1 in run"]


class TestStallDiagnosis:
    def test_finds_the_engine_run_task_and_reads_its_parked_frames(self, monkeypatch):
        engine_task = SimpleNamespace(get_stack=lambda: [
            _frame("/srv/bot/core/engine.py", 2093, "run"),
            _frame("/srv/bot/core/engine.py", 2629, "_tick"),
            _frame("/usr/lib/aiohttp/client.py", 512, "_request"),
        ])
        other_task = SimpleNamespace(get_stack=lambda: [
            _frame("/srv/tests/test_x.py", 9, "run"),   # right name, wrong file
        ])
        monkeypatch.setattr(pm_mod.asyncio, "all_tasks",
                            lambda: [other_task, engine_task])
        lines = _stalled_pm()._stall_diagnosis()
        assert lines[0] == "engine.py:2093 in run"
        assert lines[-1] == "client.py:512 in _request"   # innermost = the culprit

    def test_no_running_loop_is_fail_open_empty(self):
        # Outside any event loop asyncio.all_tasks() raises — diagnosis
        # swallows it and returns nothing.
        assert _stalled_pm()._stall_diagnosis() == []


class TestAlertCarriesTheDiagnosis:
    def test_hung_await_lands_in_the_alert_body(self):
        pm = _stalled_pm()
        pm._stall_diagnosis = lambda: [
            "engine.py:2093 in run", "client.py:512 in _request"]
        alerts = pm._check_engine_tick_stale()
        assert len(alerts) == 1
        assert "Hung awaiting: client.py:512 in _request" in alerts[0].body
        # The original operator guidance is intact.
        assert "consider a restart" in alerts[0].body

    def test_without_diagnosis_the_body_is_unchanged(self):
        pm = _stalled_pm()
        pm._stall_diagnosis = lambda: []
        alerts = pm._check_engine_tick_stale()
        assert len(alerts) == 1
        assert "Hung awaiting" not in alerts[0].body


class TestDeepAwaitChain:
    """The regression that shipped: only the OUTERMOST frame was reported."""

    def test_walk_reaches_the_innermost_parked_frame(self):
        import asyncio

        async def inner(ev):
            await ev.wait()                 # the real parked await

        async def middle(ev):
            await inner(ev)

        async def run(ev):                  # stands in for Engine.run
            await middle(ev)

        async def scenario():
            ev = asyncio.Event()
            task = asyncio.ensure_future(run(ev))
            await asyncio.sleep(0)          # let it park
            mon = ProactiveMonitor.__new__(ProactiveMonitor)
            frames = mon._await_chain_frames(task)
            names = [f.f_code.co_name for f in frames]
            ev.set()
            await task
            return names

        names = asyncio.run(scenario())
        assert names[0] == "run", "the chain starts at the outermost coroutine"
        assert "middle" in names and "inner" in names, \
            "it descends through every nested await — the whole point"
        assert names[-1] in ("inner", "wait"), \
            "the LAST frame is where it is actually parked"

    def test_cycle_and_depth_safe(self):
        """A diagnosis must never hang the monitor diagnosing a hang."""
        mon = ProactiveMonitor.__new__(ProactiveMonitor)

        class Loopy:
            cr_frame = None
            @property
            def cr_await(self):
                return self
        class SelfAwaiting:
            def get_coro(self):
                return Loopy()
        assert mon._await_chain_frames(SelfAwaiting()) == []

        class Deep:
            def __init__(self, n=0):
                self.n = n
                self.cr_frame = None
            @property
            def cr_await(self):
                return Deep(self.n + 1)
        class Endless:
            def get_coro(self):
                return Deep()
        assert mon._await_chain_frames(Endless(), max_depth=5) == []

    def test_never_raises_into_the_alert_path(self):
        mon = ProactiveMonitor.__new__(ProactiveMonitor)

        class Boom:
            def get_coro(self):
                raise RuntimeError("nope")
        assert mon._await_chain_frames(Boom()) == []

    def test_shallow_stack_is_still_the_fallback(self, monkeypatch):
        """A task the walk cannot read still yields get_stack()'s frames."""
        engine_task = SimpleNamespace(get_stack=lambda: [
            _frame("/srv/bot/core/engine.py", 2226, "run")])
        monkeypatch.setattr(pm_mod.asyncio, "all_tasks", lambda: [engine_task])
        assert _stalled_pm()._stall_diagnosis() == ["engine.py:2226 in run"]


class TestOneHangOnePage:
    """The operator got two CRITICAL pages 13s apart for ONE stall."""

    def test_a_flapping_predicate_does_not_re_page_the_same_hang(self):
        pm = _stalled_pm()
        pm._stall_diagnosis = lambda: []
        stale = pm.engine._last_tick_started_ts

        assert len(pm._check_engine_tick_stale()) == 1, "the stall pages once"

        # Predicate flaps healthy (a moving next_due), then stalls again —
        # the SAME hang: _last_tick_started_ts never moved.
        pm.engine._next_tick_due_ts = time.monotonic() + 300.0
        assert pm._check_engine_tick_stale() == []
        pm.engine._next_tick_due_ts = None
        assert pm._check_engine_tick_stale() == [], \
            "the same hang must not page again — the tick never moved"

        # A genuinely NEW stall (tick moved, then hung again) does page.
        pm.engine._last_tick_started_ts = stale + 1.0
        assert len(pm._check_engine_tick_stale()) == 1, "a new stall is a new page"


class TestStatusReportsLoopLiveness:
    """/status could not answer 'is the loop alive?' during a real hang: the
    FSM state and the cached equity FREEZE while a tick is parked."""

    def test_card_shows_a_healthy_age(self):
        from bot.formatters.rich_cards import render_status_card
        out = render_status_card(
            mode="LIVE", active=True, equity=495.69, open_positions=0,
            daily_pnl=0.2, drawdown=0.0, max_drawdown=5.0,
            market_bias="Normal", pending_ideas=0, tick_age_s=12.0)
        assert "Last tick" in out and "12s ago" in out

    def test_card_flags_a_stalled_loop_beside_a_green_active(self):
        from bot.formatters.rich_cards import render_status_card
        out = render_status_card(
            mode="LIVE", active=True, equity=495.69, open_positions=0,
            daily_pnl=0.2, drawdown=0.0, max_drawdown=5.0,
            market_bias="Normal", pending_ideas=0, tick_age_s=640.0)
        assert "11m ago" in out and "stalled" in out

    def test_never_ticked_omits_the_line_rather_than_printing_zero(self):
        from bot.formatters.rich_cards import render_status_card
        out = render_status_card(
            mode="LIVE", active=True, equity=495.69, open_positions=0,
            daily_pnl=0.2, drawdown=0.0, max_drawdown=5.0,
            market_bias="Normal", pending_ideas=0, tick_age_s=None)
        assert "Last tick" not in out

    def test_handler_age_helper_is_real_and_fail_safe(self):
        from bot.skills.telegram_handler import TelegramHandler
        age = TelegramHandler._tick_age_s(
            SimpleNamespace(_last_tick_started_ts=time.monotonic() - 5.0))
        assert age is not None and 4.0 <= age <= 12.0
        assert TelegramHandler._tick_age_s(
            SimpleNamespace(_last_tick_started_ts=None)) is None

        class Hostile:
            @property
            def _last_tick_started_ts(self):
                raise RuntimeError("boom")
        assert TelegramHandler._tick_age_s(Hostile()) is None
