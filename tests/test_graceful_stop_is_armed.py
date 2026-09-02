"""The graceful shutdown existed and no supervisor could reach it.

`bot/main.py` ends its run in a `finally` that stops the alert monitor, cleans
up the dashboard runner, stops the Telegram updater and application, and calls
`engine.stop()`. Every line of it was correct. Nothing could get there.

No SIGTERM handler existed anywhere in the tree — `grep -rn signal.signal` over
`bot/` found one *send* (singleton enforcement in main.py) and no install — and
Python's default action for SIGTERM terminates the process outright: no
exception raised, so no `finally` runs. `scripts/systemd/runeclaw-bot.service`
sets no `KillSignal`, so `systemctl restart` sends exactly that. The shutdown
path had therefore never run in production, on a bot holding live positions.

The same fault explains the failure the poller watchdog exists to recover from.
That watchdog's own comment names "a 409 getUpdates conflict from two instances
overlapping on a redeploy" — which is what happens when the outgoing process
never calls `updater.stop()` and leaves its long poll open at Telegram. The
conflict is a symptom; this is the cause.

`run_polling()` would have installed handlers itself, which is presumably why
nobody noticed. main.py does not use it: it drives `app.initialize()`,
`app.start()` and `app.updater.start_polling()` by hand, and PTB installs its
stop signals only inside `run_polling`/`run_webhook`.
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot.core.boot_health import (
    STOP_SIGNAL_NAMES,
    engine_should_restart,
    format_stop_handlers,
    install_stop_handlers,
    poller_should_restart,
)

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# The behaviour, driven for real.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_real_sigterm_reaches_the_handler():
    """Raise an actual SIGTERM at this process and prove it is caught.

    The kill is guarded on `armed` for a reason: if the handler regresses to
    not being installed, an unguarded `os.kill` would terminate the test
    runner rather than fail a test. The assertion fires first, so a regression
    is a red test and never a dead process.
    """
    loop = asyncio.get_running_loop()
    seen: list[str] = []
    armed = install_stop_handlers(loop, seen.append)
    assert "SIGTERM" in armed, "SIGTERM was not armed — not raising it"
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        for _ in range(100):
            if seen:
                break
            await asyncio.sleep(0.01)
        assert seen == ["SIGTERM"], "SIGTERM did not reach the handler"
    finally:
        for name in armed:
            loop.remove_signal_handler(getattr(signal, name))


@pytest.mark.asyncio
async def test_the_cleanup_actually_runs_on_a_stop_signal():
    """The whole point: a stop signal must land in the `finally`, not kill us.

    This mirrors main.py's shape — a supervised task, a stop request that
    cancels it, and cleanup in a `finally` — and asserts the cleanup ran to
    completion. Before the fix the process would simply have died here.
    """
    loop = asyncio.get_running_loop()
    state = {"stopping": False}
    cleaned: list[str] = []
    engine_task = asyncio.create_task(asyncio.sleep(30))

    def _request_stop(_name: str) -> None:
        state["stopping"] = True
        engine_task.cancel()

    armed = install_stop_handlers(loop, _request_stop)
    assert "SIGTERM" in armed
    try:
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            while True:
                try:
                    await engine_task
                    if not engine_should_restart(state["stopping"]):
                        break
                except asyncio.CancelledError:
                    break
        finally:
            # Every one of these is an `await` in main.py's finally. They must
            # all complete: a cancellation that interrupted cleanup halfway
            # would leave the updater running, which is the 409 all over again.
            for step in ("stop_monitor", "dashboard_cleanup", "updater_stop",
                         "app_stop", "app_shutdown", "engine_stop"):
                await asyncio.sleep(0)
                cleaned.append(step)
    finally:
        for name in armed:
            loop.remove_signal_handler(getattr(signal, name))

    assert state["stopping"] is True
    assert cleaned == ["stop_monitor", "dashboard_cleanup", "updater_stop",
                       "app_stop", "app_shutdown", "engine_stop"]


# --------------------------------------------------------------------------
# Arming reports what it armed — an unarmed stop is not a quiet one.
# --------------------------------------------------------------------------

class _RefusingLoop:
    def __init__(self, refuse=(), exc=NotImplementedError):
        self.refuse, self.exc, self.armed = refuse, exc, []

    def add_signal_handler(self, signum, cb, *a):
        if signum in self.refuse:
            raise self.exc("no signal handlers here")
        self.armed.append(signum)


def test_it_returns_the_signals_it_armed():
    loop = _RefusingLoop()
    assert install_stop_handlers(loop, lambda _n: None) == list(STOP_SIGNAL_NAMES)


def test_one_refusal_does_not_cost_the_others():
    """Each signal is armed independently — SIGINT failing must not lose SIGTERM."""
    loop = _RefusingLoop(refuse=(signal.SIGINT,))
    assert install_stop_handlers(loop, lambda _n: None) == ["SIGTERM"]


@pytest.mark.parametrize("exc", [NotImplementedError, RuntimeError, ValueError, OSError])
def test_a_platform_that_refuses_reports_nothing_armed(exc):
    """Windows and off-main-thread loops refuse. That is an answer, not a pass."""
    loop = _RefusingLoop(refuse=(signal.SIGTERM, signal.SIGINT), exc=exc)
    assert install_stop_handlers(loop, lambda _n: None) == []


def test_an_unarmed_stop_says_so_loudly():
    """The line an operator reads must never imply a stop that is not armed."""
    line = format_stop_handlers([])
    assert "NOT ARMED" in line
    assert "kill this process without running shutdown" in line
    # ...and it must not read like a success.
    assert "armed for: " not in line


def test_an_armed_stop_names_the_signals():
    line = format_stop_handlers(["SIGTERM", "SIGINT"])
    assert "SIGTERM" in line and "SIGINT" in line
    assert "NOT ARMED" not in line


def test_a_missing_signal_name_is_skipped_not_crashed():
    """A platform without SIGTERM must yield [], never an AttributeError."""
    loop = _RefusingLoop()
    fake = SimpleNamespace()          # no SIGTERM, no SIGINT
    assert install_stop_handlers(loop, lambda _n: None, signal_module=fake) == []


# --------------------------------------------------------------------------
# The supervise loop must not fight the shutdown.
# --------------------------------------------------------------------------

def test_the_engine_is_not_respawned_during_a_stop():
    assert engine_should_restart(stopping=False) is True
    assert engine_should_restart(stopping=True) is False


def test_it_matches_the_polling_predicate_it_mirrors():
    """Same question, same answer shape — a stopping supervisor restarts nothing."""
    assert poller_should_restart(running=False, stopping=True) is False
    assert engine_should_restart(stopping=True) is False


# --------------------------------------------------------------------------
# Wiring: the seam has to be REACHED, which no unit test can show.
# --------------------------------------------------------------------------

def _main_src() -> str:
    from tests.source_scan import code_only
    return code_only((REPO / "bot" / "main.py").read_text(encoding="utf-8"))


def test_main_arms_the_handlers():
    src = _main_src()
    assert "install_stop_handlers" in src, \
        "main.py no longer arms stop handlers — the finally is unreachable again"
    assert re.search(r"install_stop_handlers\s*\(\s*loop\s*,", src), \
        "the handlers must be armed on the running loop"


def test_main_reports_whether_they_were_armed():
    """Arming silently would hide the Windows/threaded case from the operator.

    Anchored on the CALL, not the bare name: the name also appears in the
    import line, so `"format_stop_handlers" in src` stayed true with the call
    deleted — an assertion satisfied by the import of the thing it checks.
    """
    src = _main_src()
    # Both the console line and the AUDIT record must carry it. Asserting only
    # the presence of the call left the audit deletable — and the audit entry
    # is the one an operator greps after the fact, when the console is gone.
    assert src.count("format_stop_handlers(") == 2, \
        "the arm status must reach both the console and the audit chain"
    assert re.search(r'result\s*=\s*"ARMED"\s*if\s*_armed\s*else\s*"NOT_ARMED"', src), \
        "the audit result must distinguish an armed stop from an unarmed one"


def test_main_guards_both_restart_branches():
    """A clean exit AND a crash during shutdown must both stop, not respawn.

    Counting the bare name counted the import too, so dropping one of the two
    guards still scored 2. Count call sites.
    """
    src = _main_src()
    assert src.count("engine_should_restart(") == 2, \
        "both the clean-exit and crash branches must check the stopping flag"


def test_run_polling_is_still_not_used():
    """The reason PTB's own handlers are absent — pin it so a future switch to
    run_polling() is a deliberate change and not a silent double-arming."""
    src = _main_src()
    assert "start_polling" in src
    assert "run_polling" not in src.replace("start_polling", "")


def test_the_systemd_unit_still_stops_with_the_signal_we_arm():
    """If the unit ever sets KillSignal to something we do not arm, the fix is
    silently undone — the process would again die without cleanup."""
    unit = (REPO / "scripts" / "systemd" / "runeclaw-bot.service").read_text(encoding="utf-8")
    m = re.search(r"^KillSignal\s*=\s*(\S+)", unit, re.M)
    if m:
        assert m.group(1).lstrip("+").upper().removeprefix("SIG") in \
            {n.removeprefix("SIG") for n in STOP_SIGNAL_NAMES}, \
            f"unit stops with {m.group(1)}, which install_stop_handlers does not arm"
    # No KillSignal means systemd's default, SIGTERM — which we do arm.
    assert "SIGTERM" in STOP_SIGNAL_NAMES
