"""`/latest_signal` stops waiting at its timeout; it does not cancel the scan.

With nothing pending, the command runs `engine.force_scan` under
`asyncio.wait_for`. `force_scan` auto-confirms what clears the bar, so it can
be inside `confirm_trade` -> `LiveExecutor.execute` when the timeout runs out,
and `wait_for` cancels whatever it is awaiting. `execute` awaits the venue
between submitting the entry order and recording the position (the fill
reads) and again before the stop is placed, so the cancel could land after an
order the venue had filled and before anything recorded or protected it.

The scan is shielded now: the tap stops waiting at the timeout and says so,
and the scan finishes in the background, its ideas pending for the next tap.
A scan that then fails has nobody awaiting it, so its exception is retrieved
and logged by class name rather than reported by asyncio at garbage
collection.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import CONFIG

ADMIN = 6307156912


def _update():
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = ADMIN
    update.effective_user.first_name = "TestUser"
    update.effective_chat = MagicMock()
    update.effective_chat.id = ADMIN
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.message.text = "/latest_signal"
    update.callback_query = None
    ctx = MagicMock()
    ctx.args = []
    return update, ctx


def _replies(update) -> str:
    return "\n".join(str(c.args[0]) for c in update.message.reply_text.call_args_list
                     if c.args)


@pytest.fixture
def short_timeout():
    old = CONFIG.interactive_scan_timeout_sec
    object.__setattr__(CONFIG, "interactive_scan_timeout_sec", 0.05)
    yield
    object.__setattr__(CONFIG, "interactive_scan_timeout_sec", old)


class _Scan:
    """A force_scan that parks until released, recording what happened to it."""

    def __init__(self, *, fail: bool = False):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.finished = False
        self.fail = fail
        self.task = None

    async def __call__(self, **kw):
        self.task = asyncio.current_task()
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.fail:
            raise RuntimeError("venue said https://api.example/?sign=SECRET")
        self.finished = True
        return {"signals": 0, "auto_confirmed": 0}


def _warnings(caplog) -> list[str]:
    """What the scan's outcome put on the log, from this module or asyncio."""
    return [r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING
            and r.name in ("bot.skills.trading_commands", "asyncio")]


def _handler(scan):
    from bot.core.engine import RuneClawEngine
    from bot.skills.telegram_handler import TelegramHandler

    engine = RuneClawEngine()
    handler = TelegramHandler(engine)
    handler.users.seed_admin(str(ADMIN))
    engine._pending_ideas.clear()
    engine._last_scan_time = 0.0            # no fresh background sweep
    engine.force_scan = scan
    return handler


class TestTheTimeout:
    @pytest.mark.asyncio
    async def test_the_tap_times_out_and_the_scan_keeps_running(self, short_timeout,
                                                                  caplog):
        scan = _Scan()
        handler = _handler(scan)
        update, ctx = _update()

        await asyncio.wait_for(handler._cmd_latest_signal(update, ctx), timeout=10)

        assert scan.entered.is_set()
        assert "taking longer than usual" in _replies(update)
        assert scan.cancelled is False, (
            "the tap's timeout cancelled a scan that may be placing an order")
        with caplog.at_level(logging.WARNING):
            scan.release.set()
            for _ in range(20):
                await asyncio.sleep(0)
        assert scan.finished is True
        assert _warnings(caplog) == [], "a scan that finished was logged as failed"

    @pytest.mark.asyncio
    async def test_a_scan_cancelled_after_the_tap_logs_nothing(self, short_timeout, caplog):
        # A cancelled task has no exception to retrieve: asking it for one
        # raises inside the done callback, which asyncio reports as an error.
        scan = _Scan()
        handler = _handler(scan)
        update, ctx = _update()
        await handler._cmd_latest_signal(update, ctx)

        with caplog.at_level(logging.WARNING):
            scan.task.cancel()
            for _ in range(20):
                await asyncio.sleep(0)
        assert scan.cancelled is True
        assert _warnings(caplog) == [], _warnings(caplog)

    @pytest.mark.asyncio
    async def test_a_scan_that_fails_after_the_tap_is_logged_by_class(
            self, short_timeout, caplog):
        scan = _Scan(fail=True)
        handler = _handler(scan)
        update, ctx = _update()
        await handler._cmd_latest_signal(update, ctx)

        with caplog.at_level(logging.WARNING, logger="bot.skills.trading_commands"):
            scan.release.set()
            for _ in range(20):
                await asyncio.sleep(0)
        lines = [r.getMessage() for r in caplog.records
                 if r.name == "bot.skills.trading_commands"]
        assert lines == ["Interactive scan failed: RuntimeError"], lines
        assert not any("SECRET" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_a_scan_that_answers_in_time_is_read_as_before(self, short_timeout,
                                                                   caplog):
        scan = _Scan()
        scan.release.set()                   # answers at once
        handler = _handler(scan)
        update, ctx = _update()
        with caplog.at_level(logging.WARNING):
            await handler._cmd_latest_signal(update, ctx)
            for _ in range(5):
                await asyncio.sleep(0)

        assert scan.finished is True
        assert _warnings(caplog) == [], "a scan that answered was logged as failed"
        assert "No trade setups above" in _replies(update)
