"""A bot that cannot start says why, not that its updater was not running.

Found running the SoloHost package (scripts/solohost/) with a token Telegram
rejects, the first thing a new self-hosted install can get wrong. `app.initialize()`
raised `InvalidToken`, and main.py's `finally` then called `app.updater.stop()`
on an updater that never started. PTB raises "This Updater is not running!"
from there, so the log ended on that second exception, the line an operator
reads first, and the rejected token sat above it as "During handling of the
above exception, another exception occurred".

`stop_telegram` stops each part only if it is running. Driven against a real
PTB `Application`, built and never initialised, which is exactly the state a
failed boot leaves it in, and against a recorder for the running case.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from telegram.error import InvalidToken
from telegram.ext import ApplicationBuilder

from bot.core.boot_health import REJECTED_TOKEN_LINE, run_boot, stop_telegram
from tests.source_scan import code_only

REPO = Path(__file__).resolve().parents[1]


def _never_started():
    # A well-formed token: building needs none of the network, and nothing
    # here initialises, so nothing is sent.
    return ApplicationBuilder().token("123456789:AAFakeTokenNeverSentAnywhere000").build()


@pytest.mark.asyncio
async def test_the_premise_ptb_refuses_to_stop_an_updater_that_never_ran():
    """The failure this file exists for, reproduced on the real object."""
    app = _never_started()
    with pytest.raises(RuntimeError, match="not running"):
        await app.updater.stop()


@pytest.mark.asyncio
async def test_a_boot_that_never_started_shuts_down_without_a_second_error():
    app = _never_started()
    await stop_telegram(app)          # must not raise
    assert not app.running and not app.updater.running


class _Part:
    def __init__(self, log, name, running):
        self.log, self.name, self.running = log, name, running

    async def stop(self):
        self.log.append(f"{self.name}.stop")
        self.running = False


class _App(_Part):
    def __init__(self, log, running):
        super().__init__(log, "app", running)
        self.updater = _Part(log, "updater", running)

    async def shutdown(self):
        self.log.append("app.shutdown")


@pytest.mark.asyncio
async def test_a_running_bot_is_still_stopped_in_order():
    """The other arm: a bot that did start must still stop its poll, or the
    next instance meets a 409 getUpdates conflict at Telegram."""
    log: list[str] = []
    await stop_telegram(_App(log, running=True))
    assert log == ["updater.stop", "app.stop", "app.shutdown"]


@pytest.mark.asyncio
async def test_a_bot_that_never_ran_is_only_shut_down():
    log: list[str] = []
    await stop_telegram(_App(log, running=False))
    assert log == ["app.shutdown"]


def test_main_stops_telegram_through_the_guarded_helper():
    src = code_only((REPO / "bot" / "main.py").read_text(encoding="utf-8"))
    assert src.count("stop_telegram(app)") == 1
    assert not re.search(r"await\s+app\.updater\.stop\(\)", src), \
        "main.py stops the updater unguarded again"


# ── and the error it exits with names the class, never the token ────────

SECRET = "123456789:AAThisWouldBeTheOperatorsRealToken00"


def _drive(coro_fn):
    printed: list[str] = []
    loop = asyncio.new_event_loop()
    try:
        rc = run_boot(loop, coro_fn(), out=printed.append)
    finally:
        loop.close()
    return rc, printed


def test_a_rejected_token_exits_2_without_printing_it():
    async def boot():
        raise InvalidToken(f"The token `{SECRET}` was rejected by the server.")
    rc, printed = _drive(boot)
    assert rc == 2
    assert printed == ["  ERROR: " + REJECTED_TOKEN_LINE]
    assert SECRET not in "".join(printed) and "InvalidToken" in printed[0]


def test_a_clean_run_exits_0_and_says_nothing():
    async def boot():
        return None
    assert _drive(boot) == (0, [])


def test_ctrl_c_still_says_it_is_shutting_down():
    async def boot():
        raise KeyboardInterrupt
    rc, printed = _drive(boot)
    assert rc == 0 and "Shutting down" in printed[0]


def test_any_other_failure_still_propagates():
    async def boot():
        raise ValueError("something else")
    with pytest.raises(ValueError):
        _drive(boot)


def test_main_exits_with_what_run_boot_returns():
    src = code_only((REPO / "bot" / "main.py").read_text(encoding="utf-8"))
    assert re.search(r"rc\s*=\s*run_boot\(loop,\s*_run_all\(\)\)", src)
    assert re.search(r"if rc:\s*raise SystemExit\(rc\)", src)
