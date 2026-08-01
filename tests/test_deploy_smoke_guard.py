"""Fifteen deploys reported DEPLOY_DONE and left no bot running.

From the operator's 2026-08-01 work report. `deploy_now.sh` launched the bot
without `--mode telegram`; `bot/main.py` defaulted to `cli`; `run_cli()` found
no TTY and EXITED ZERO. The launcher asked "did the process start?" when the
question that mattered was "is it still there a moment later?" -- and those
differ by exactly the interval in which a misconfigured process gives up.

`git reset --hard` restored the flagless launcher on every deploy, so the
same silent no-op happened about fifteen times in a row.

Two fixes, one for the cause and one for the class:

  bot/main.py now DEFAULTS to telegram. scripts/health_check.sh already
  assumed it (RUNECLAW_MODE:-telegram) and docker-compose.yml passed it
  explicitly -- two of three entry points already agreed, and the default was
  the odd one out that a bare invocation got.

  scripts/verify_bot_alive.sh fails a deploy when the process is gone after a
  wait, whatever the reason: a missing secret, an unreadable data dir, a bad
  venue credential. A clean early exit is the dangerous case precisely
  because nothing looks wrong.

    "DID IT START" AND "IS IT STILL RUNNING" ARE DIFFERENT QUESTIONS,
    AND ONLY THE SECOND ONE IS WORTH REPORTING.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_bot_alive.sh"


def _run(*args, wait="1", timeout=30):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True, text=True, timeout=timeout,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "WAIT_SECONDS": wait},
    )


class TestTheDefaultModeIsTelegram:
    def test_main_defaults_to_telegram(self):
        from tests.source_scan import code_only
        src = code_only((ROOT / "bot" / "main.py").read_text(encoding="utf-8"))
        assert 'default="telegram"' in src, (
            "a bare `python -m bot.main` used to run the CLI, find no TTY and "
            "exit zero — a deploy that reports success and leaves no bot"
        )

    def test_cli_is_still_reachable(self):
        from tests.source_scan import code_only
        src = code_only((ROOT / "bot" / "main.py").read_text(encoding="utf-8"))
        assert '"cli"' in src, "the REPL must remain selectable explicitly"

    def test_the_watchdog_and_the_default_now_agree(self):
        # They disagreed, and the deploy path got the wrong one.
        hc = (ROOT / "scripts" / "health_check.sh").read_text(encoding="utf-8")
        assert "RUNECLAW_MODE:-telegram" in hc


class TestTheSmokeTestCanActuallyFail:
    """The property a smoke test must have before any other property matters."""

    def test_it_exists_and_is_executable(self):
        assert SCRIPT.exists()
        assert SCRIPT.stat().st_mode & 0o111, "must be executable"

    def test_a_pid_that_never_existed_fails(self):
        r = _run("--pid", "999999")
        assert r.returncode == 1
        assert "not running one second after launch" in r.stderr

    def test_a_pattern_that_was_never_launched_fails(self):
        # THE SELF-MATCH CASE. `pgrep -f` sees this script's own command line,
        # which contains the pattern. The first draft reported SMOKE OK here —
        # a check that cannot fail, which would have rubber-stamped exactly
        # the deploys it was written to catch.
        r = _run("--pattern", "never_launched_sentinel_xyz")
        assert r.returncode == 1, (
            f"self-matched and passed: {r.stdout!r} {r.stderr!r}"
        )

    def test_a_bare_argument_is_treated_as_a_pattern(self):
        r = _run("never_launched_sentinel_abc")
        assert r.returncode == 1


class TestItRecognisesAHealthyProcess:
    def test_a_live_pid_passes(self):
        p = subprocess.Popen(["sleep", "20"])
        try:
            r = _run("--pid", str(p.pid), wait="1")
            assert r.returncode == 0, r.stderr
            assert "still running" in r.stdout
        finally:
            p.kill()
            p.wait()

    def test_a_process_that_exits_early_fails(self):
        # The actual incident shape: it started, then went away.
        p = subprocess.Popen(["sleep", "2"])
        try:
            r = _run("--pid", str(p.pid), wait="4")
            assert r.returncode == 1
            assert "started and exited" in r.stderr
        finally:
            p.poll() is None and p.kill()


class TestItsOwnOutputIsNotANuisance:
    def test_a_passing_run_prints_nothing_to_stderr(self):
        # An earlier draft printed "/proc/NNN/cmdline: No such file or
        # directory" on every successful check — an alarming-looking error on
        # a passing run is the false-signal problem this script exists to
        # remove, reproduced in its own output.
        p = subprocess.Popen(["sleep", "20"])
        try:
            r = _run("--pid", str(p.pid), wait="1")
            assert r.returncode == 0
            assert r.stderr.strip() == "", f"noise on stderr: {r.stderr!r}"
        finally:
            p.kill()
            p.wait()

    def test_a_failing_run_says_where_to_look(self):
        r = _run("--pid", "999999")
        assert "Check the launch command" in r.stderr

    @pytest.mark.parametrize("flag", ["--pid", "--pattern"])
    def test_a_flag_with_no_value_is_refused(self, flag):
        r = _run(flag)
        assert r.returncode == 1
        assert "needs a value" in r.stderr


class TestItIsFastEnoughToGateADeploy:
    def test_the_wait_is_configurable_and_respected(self):
        p = subprocess.Popen(["sleep", "20"])
        try:
            t0 = time.monotonic()
            _run("--pid", str(p.pid), wait="2")
            elapsed = time.monotonic() - t0
            assert 2.5 <= elapsed < 12, (
                f"took {elapsed:.1f}s — the 1s fail-fast plus a 2s wait"
            )
        finally:
            p.kill()
            p.wait()
