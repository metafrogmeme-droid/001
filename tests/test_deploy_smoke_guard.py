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

import os
import re
import signal
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
        # 2, not 1. This USED to be 1, which made "you called me wrong"
        # indistinguishable from "the bot is dead" to a `||` launcher -- a
        # verdict about a process nobody looked at. Still non-zero, so every
        # existing launcher still fails closed on it.
        assert r.returncode == 2, "a usage error is not a claim about the bot"
        assert "needs a value" in r.stderr
        assert "Nothing was checked" in r.stderr


class TestAnInterruptedCheckIsNotAVerdict:
    """Exit 144, reported 2026-08-17.

    The gate was "exiting 144 in one-liners", so it was routed around in
    favour of an out-of-repo restart script -- i.e. the deploy stopped being
    verified at all, which is the exact outcome this file exists to prevent.

    144 is 128+16: killed by SIGSTKFLT. No path inside the script produces it;
    something in the calling environment signalled it mid-wait. Unhandled, a
    launcher reads any non-zero as "the bot died" and reports a failure for a
    bot that is running fine. Three false alarms are enough to retire a gate.
    """

    @staticmethod
    def _await_trap_installed(gate, timeout=30.0):
        """Block until the gate is provably past its `trap` lines.

        THIS REPLACED `time.sleep(2.0)`, WHICH WAS THE 1-IN-8760 FLAKE.

        The traps are installed early in the script -- but "early" is a
        position in the file, not a promise about wall-clock. A fixed sleep
        assumes bash has started, parsed ~125 lines and reached its wait within
        2s, and under full-suite load on a shared runner that is a race rather
        than a fact. Lose it and the signal lands on a process with the DEFAULT
        disposition: SIGINT and SIGTERM terminate, giving 128+n -- exactly the
        number this test exists to prove the script never returns. So the
        failure looked like the defect had come back, on a run where the only
        thing that changed was how busy the machine was.

        `sleep` is an external binary, so the moment bash reaches line 220 it
        forks a child. A child under the gate's pid is therefore proof that
        parsing got past the traps -- observable from outside, and independent
        of how slow the box is.

        That inference holds ONLY while the traps precede the first `sleep` in
        the script, which nothing in the script itself guarantees. Reverse them
        and this readiness check starts firing early and silently restores the
        race it was written to remove, so the ordering is asserted rather than
        assumed -- see test_the_readiness_signal_still_means_what_it_says.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if gate.poll() is not None:
                raise AssertionError(
                    f"the gate exited (code {gate.returncode}) before it ever "
                    f"reached its wait; nothing was interrupted")
            children = subprocess.run(["pgrep", "-P", str(gate.pid)],
                                      capture_output=True, text=True)
            if children.stdout.strip():
                return
            time.sleep(0.05)
        raise AssertionError(
            f"gate {gate.pid} never forked its `sleep` child within {timeout}s")

    def _interrupt_with(self, signum):
        """Launch a live target, start the check, signal the CHECK mid-wait."""
        target = subprocess.Popen(["sleep", "30"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            gate = subprocess.Popen(
                [str(SCRIPT), "--pid", str(target.pid)],
                env={**os.environ, "WAIT_SECONDS": "8"},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self._await_trap_installed(gate)
            gate.send_signal(signum)
            out, err = gate.communicate(timeout=60)
            return gate.returncode, err
        finally:
            target.kill()
            target.wait()

    @pytest.mark.parametrize("signum", [signal.SIGSTKFLT, signal.SIGTERM, signal.SIGINT])
    def test_a_signalled_check_reports_no_verdict(self, signum):
        code, err = self._interrupt_with(signum)
        assert code == 3, (
            f"SIG{signum} gave {code}; 128+n is indistinguishable from a "
            f"verdict to a launcher doing `|| echo DEPLOY FAILED`")
        assert "SMOKE UNKNOWN" in err
        assert "NOT 'the bot died'" in err, (
            "the operator must be told this is not a claim about the bot -- "
            "the number alone is what got the gate abandoned")

    def test_the_readiness_signal_still_means_what_it_says(self):
        """The premise _await_trap_installed rests on, pinned.

        It treats "the gate has forked a child" as "the gate is past its
        traps". That is only true while every `trap` line comes BEFORE the
        first `sleep`. If a future edit puts a sleep earlier -- a retry, a
        settle, a backoff -- the readiness check would return while the traps
        are still uninstalled, the signal would hit the default disposition,
        and the suite would go back to failing once every few thousand runs
        with a number that looks like the original defect.
        """
        # `trap` is not at the start of its line -- it sits inside an
        # `if trap ... || trap ...`. Anchoring to ^ found nothing and the
        # assertion below then failed on the CORRECT script, which is the same
        # class of mistake as the thing being guarded: a check that cannot see
        # what it is checking.
        code = [ln.split("#", 1)[0] for ln in SCRIPT.read_text(encoding="utf-8").splitlines()]
        traps = [i for i, ln in enumerate(code) if re.search(r"(^|\s|\|\|)\s*trap\s", ln)]
        sleeps = [i for i, ln in enumerate(code) if re.search(r"(^|;|\s)sleep\s", ln)]
        assert traps, "no `trap` lines found -- the script stopped trapping signals"
        assert sleeps, "no `sleep` lines found -- the script shape changed"
        assert max(traps) < min(sleeps), (
            f"a `sleep` at line {min(sleeps) + 1} now precedes a `trap` at line "
            f"{max(traps) + 1}. _await_trap_installed infers 'traps installed' "
            f"from 'a child was forked', and that inference is now wrong -- it "
            f"would signal too early and reintroduce the flake.")

    def test_the_four_codes_are_distinct(self):
        """0/1/2/3 must not collapse: each names a different thing, and the
        collapse is the defect."""
        alive = subprocess.Popen(["sleep", "20"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            assert _run("--pid", str(alive.pid), wait="1").returncode == 0
        finally:
            alive.kill(); alive.wait()
        assert _run("--pid", "999999", wait="1").returncode == 1
        assert _run("--pid").returncode == 2
        assert self._interrupt_with(signal.SIGSTKFLT)[0] == 3

    def test_every_code_is_documented_in_the_script(self):
        src = SCRIPT.read_text(encoding="utf-8")
        block = src[src.index("# EXIT CODES"):src.index("set -uo pipefail")]
        for code in ("0", "1", "2", "3"):
            assert re.search(rf"^#\s+{code}\s+\S", block, re.M), f"code {code} undocumented"
        # And a caller that only checks truthiness still fails closed.
        assert "still fails a" in block


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
