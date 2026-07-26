"""A backtest must never leave the live bot analysing without its learning flags.

BacktestEngine.__init__ forces confidence-calibration, setup-expectancy and
external sentiment OFF *process-wide* — they are fitted on the bot's entire
closed-trade history, which is future information relative to any replayed
historical bar (audit fix #25).

That override is global mutable state, and it used to live until someone
remembered to call cleanup() (or until GC ran __del__). Two ways that bit:

  * A caller that never calls cleanup() at all leaves the flags off forever.
    tests/test_backtest_preset_gates.py did exactly this, which is why every
    later test in the process saw CONFIDENCE_CALIBRATION_ENABLED reported OFF.
  * A run() that raises skips the caller's cleanup() line. In the long-lived
    Telegram process (/backtest → skill_registry) that turns a failed command
    into a silent, permanent live-trading behaviour change until restart.

The window is now exactly [ctor, end of run]: run() restores in a finally, so
no caller can extend it. These tests assert that behaviour — they fail against
the old restore-in-cleanup-only code.
"""
from __future__ import annotations

import asyncio

import pytest

from bot.config import CONFIG
from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig
from bot.backtest.portfolio_engine import PortfolioBacktester

FLAGS = (
    "confidence_calibration_enabled",
    "setup_expectancy_enabled",
    "external_sentiment_enabled",
)


def _snapshot() -> tuple:
    return tuple(getattr(CONFIG.analyzer, f) for f in FLAGS)


@pytest.fixture(autouse=True)
def _restore_operator_flags():
    """This file mutates global config on purpose; hand it back regardless."""
    saved = _snapshot()
    yield
    for name, val in zip(FLAGS, saved):
        object.__setattr__(CONFIG.analyzer, name, val)


def _engine() -> BacktestEngine:
    return BacktestEngine(BacktestConfig(symbol="BTC/USDT", timeframe="1h"))


class TestSingleEngine:
    def test_ctor_forces_the_lookahead_flags_off(self):
        # The safety property itself — assert it, so a later "fix" that stops
        # forcing them can't pass by making the restore tests vacuous.
        for name, val in zip(FLAGS, _snapshot()):
            object.__setattr__(CONFIG.analyzer, name, True)
        eng = _engine()
        try:
            assert _snapshot() == (False, False, False)
        finally:
            eng.cleanup()

    def test_run_restores_even_when_it_raises(self):
        for name in FLAGS:
            object.__setattr__(CONFIG.analyzer, name, True)
        eng = _engine()
        boom = RuntimeError("bars blew up mid-replay")

        async def _explode(_bars):
            raise boom

        eng._run = _explode  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            asyncio.run(eng.run([]))
        # The operator's settings are back BEFORE anyone calls cleanup().
        assert _snapshot() == (True, True, True), \
            "a backtest that raised left the live bot without its learning flags"
        eng.cleanup()

    def test_run_restores_on_success_without_cleanup(self):
        # The leak that reddened the suite: construct, run, never clean up.
        for name in FLAGS:
            object.__setattr__(CONFIG.analyzer, name, True)
        eng = _engine()

        async def _ok(_bars):
            return "result"

        eng._run = _ok  # type: ignore[method-assign]
        assert asyncio.run(eng.run([])) == "result"
        assert _snapshot() == (True, True, True), \
            "run() completed but the process-wide override outlived it"

    def test_context_manager_cleans_up(self):
        for name in FLAGS:
            object.__setattr__(CONFIG.analyzer, name, True)
        with _engine():
            assert _snapshot() == (False, False, False)
        assert _snapshot() == (True, True, True)

    def test_context_manager_cleans_up_on_exception(self):
        for name in FLAGS:
            object.__setattr__(CONFIG.analyzer, name, True)
        with pytest.raises(ValueError):
            with _engine():
                raise ValueError("caller blew up")
        assert _snapshot() == (True, True, True)

    def test_restore_is_idempotent(self):
        # A second restore must NOT re-apply stale values on top of a LATER
        # engine's override — that was the first-run-vs-second-run bug.
        for name in FLAGS:
            object.__setattr__(CONFIG.analyzer, name, True)
        first = _engine()
        first._restore_learning_flags()
        second = _engine()
        assert _snapshot() == (False, False, False)
        first._restore_learning_flags()   # late/duplicate restore
        first.cleanup()
        assert _snapshot() == (False, False, False), \
            "a stale restore re-enabled lookahead in the middle of another run"
        second.cleanup()
        assert _snapshot() == (True, True, True)


class TestPortfolioEngine:
    def test_sub_engines_do_not_restore_behind_the_orchestrator(self):
        # Each sub-engine ctor would otherwise save the ALREADY-forced False
        # values and restore those, clobbering the operator's real settings.
        for name in FLAGS:
            object.__setattr__(CONFIG.analyzer, name, True)
        pb = PortfolioBacktester(
            BacktestConfig(symbol="BTC/USDT", timeframe="1h"),
            symbols=["BTC/USDT", "ETH/USDT"],
        )
        try:
            assert _snapshot() == (False, False, False)
            for eng in pb._engines.values():
                eng.cleanup()
            assert _snapshot() == (False, False, False), \
                "a sub-engine restored the forced-off values as if they were the operator's"
        finally:
            pb.cleanup()
        assert _snapshot() == (True, True, True)

    def test_run_restores_even_when_it_raises(self):
        for name in FLAGS:
            object.__setattr__(CONFIG.analyzer, name, True)
        pb = PortfolioBacktester(
            BacktestConfig(symbol="BTC/USDT", timeframe="1h"), symbols=["BTC/USDT"])

        async def _explode(_data):
            raise RuntimeError("fold blew up")

        pb._run = _explode  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            asyncio.run(pb.run({}))
        assert _snapshot() == (True, True, True)
        pb.cleanup()

    def test_context_manager_cleans_up_on_exception(self):
        for name in FLAGS:
            object.__setattr__(CONFIG.analyzer, name, True)
        with pytest.raises(ValueError):
            with PortfolioBacktester(
                    BacktestConfig(symbol="BTC/USDT", timeframe="1h"),
                    symbols=["BTC/USDT"]):
                raise ValueError("fold blew up")
        assert _snapshot() == (True, True, True)


class TestCallersCleanUpWhenTheBacktestRaises:
    """Drive the real call sites with an engine that blows up.

    run() now restores the flags itself, so the caller no longer controls that.
    What the caller still controls is cleanup(): the temp state dir, and the
    restore for an engine that never reached run(). A grep for `try:` would pass
    on a try that wraps the wrong lines; these call the real functions with an
    engine that raises and assert cleanup() actually ran.
    """

    def _boom_engine(self, seen: list):
        """A stand-in that records cleanup() and raises out of run().

        Deliberately NOT a context manager: the call sites must keep working
        with a plain duck-typed double, so the fix cannot quietly demand that
        every test stub grow __enter__/__exit__.
        """
        class _BoomEngine:
            _recorded_order_flow = None

            def __init__(self, config, *a, **kw):
                self.config = config

            async def run(self, _bars):
                raise RuntimeError("replay blew up")

            def cleanup(self):
                seen.append("cleanup")
        return _BoomEngine

    def test_skill_registry_cleans_up_when_the_backtest_raises(self, monkeypatch):
        # The long-lived Telegram process — the one that mattered.
        import bot.backtest.engine as eng_mod
        from bot.skills import skill_registry as sr
        seen: list = []
        # skill_registry imports BacktestEngine inside the function, so the
        # substitution has to land on the defining module.
        monkeypatch.setattr(eng_mod, "BacktestEngine", self._boom_engine(seen))
        skill = sr.RunBacktestSkill()
        # No `symbol` kwarg → skips the frozen-dataset branch and lands on the
        # synthetic path, which is the one that builds an engine here.
        with pytest.raises(RuntimeError):
            asyncio.run(skill.execute(engine=None, bars=200))
        assert seen == ["cleanup"], \
            "a /backtest that raised skipped cleanup inside the live bot process"

    def test_runner_cleans_up_when_the_backtest_raises(self, monkeypatch):
        from bot.backtest import runner
        from bot.backtest.data_loader import DataLoader
        seen: list = []
        monkeypatch.setattr(runner, "BacktestEngine", self._boom_engine(seen))

        async def _stub_load(_args, _cfg):
            # >=110 bars, else _run_backtest aborts before building an engine.
            return DataLoader.generate_synthetic(bars=200, seed=1), False, "stub"

        monkeypatch.setattr(runner, "_load_bars", _stub_load)
        args = runner.build_parser().parse_args([])
        with pytest.raises(RuntimeError):
            asyncio.run(runner._run_backtest(args))
        assert seen == ["cleanup"], \
            "runner skipped cleanup when the backtest raised"
