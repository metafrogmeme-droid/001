"""The watchdog carried the bug its sibling script was written to fix.

`scripts/verify_bot_alive.sh` treats a ZOMBIE as dead, and CLAUDE.md records
why: `kill -0` succeeds on a defunct process, and the script that spawned it is
the parent that has not reaped it — so the naive check passes on exactly the
failure the check exists to catch. `watchdog.sh` used the naive check.

Three more in the same file:

  * detection matched `python.*bot\\.main.*telegram` while the kill matched the
    broader `python.*bot\\.main`, so it SIGKILLed processes it had never looked
    for — another mode, a running test;
  * SIGKILL with no SIGTERM first, on a process whose logs/audit_chain.jsonl is
    a tamper-evident chain that a mid-append kill breaks unrecoverably;
  * `nohup python3 -m bot.main`, where deploy.sh:44-49 says the box's system
    python3 is 3.10 and the working interpreter is `.venv/bin/python`.

The zombie check is DRIVEN — a real defunct process is created and the real
predicate run against it — because that is the one where reading the source
tells you nothing: `kill -0 "$pid"` looks like a liveness test.
"""
from __future__ import annotations

import os
import re
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WATCHDOG = ROOT / "watchdog.sh"


def _src() -> str:
    return WATCHDOG.read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """Shell source with comment lines stripped.

    The file documents each of these bugs by NAMING the construct that caused
    it, so a raw scan cannot tell the explanation from the thing explained.
    """
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("#"))


# ── the zombie, driven ─────────────────────────────────────────────────────

@pytest.mark.skipif(os.name != "posix", reason="POSIX process states only")
def test_a_zombie_is_reported_dead_not_alive(tmp_path):
    """`kill -0` succeeds on a defunct process. That is the whole finding.

    A shell forks a child that exits immediately and does NOT wait for it, so
    the child becomes a zombie with the shell as its unreaping parent — the
    exact shape a crashed bot leaves behind. Then `pid_is_live` is asked about
    it.
    """
    # Forked here rather than in the shell probe: bash reaps its own background
    # children asynchronously, so `sleep 0 &` is gone before the next line runs
    # and no zombie ever exists to test against. Python's os.fork gives a child
    # this process deliberately does not waitpid() — which is precisely the
    # deploy-script-as-unreaping-parent situation CLAUDE.md describes.
    pid = os.fork()
    if pid == 0:                       # pragma: no cover - child never returns
        os._exit(0)

    probe = tmp_path / "probe.sh"
    probe.write_text(textwrap.dedent(f"""
        #!/bin/bash
        set -uo pipefail
        # Pull the predicate out of the real watchdog rather than restating it,
        # so this cannot pass against a watchdog that no longer has one.
        source <(sed -n '/^pid_is_live()/,/^}}/p' {WATCHDOG})
        ZPID="$1"
        state="$(ps -o state= -p "$ZPID" 2>/dev/null | tr -d ' ')"
        echo "state=$state"
        if kill -0 "$ZPID" 2>/dev/null; then echo "kill0=succeeds"; else echo "kill0=fails"; fi
        if pid_is_live "$ZPID"; then echo "verdict=ALIVE"; else echo "verdict=DEAD"; fi
    """).strip(), encoding="utf-8")
    probe.chmod(0o755)
    try:
        time.sleep(0.3)                # let the child reach defunct
        r = subprocess.run(["bash", str(probe), str(pid)],
                           capture_output=True, text=True, timeout=60)
        out = r.stdout
        if "state=Z" not in out:
            pytest.skip(f"could not produce a zombie on this platform: {out!r}")
        assert "kill0=succeeds" in out, (
            "kill -0 did not succeed on the zombie, so this platform cannot "
            "demonstrate the trap and the assertion below would prove nothing")
        assert "verdict=DEAD" in out, (
            "watchdog.sh reports a ZOMBIE as alive. kill -0 succeeds on a "
            "defunct process, and a defunct bot whose parent has not reaped it "
            "is exactly what this watchdog exists to restart — so it never "
            "would have.")
    finally:
        try:
            os.waitpid(pid, 0)         # reap it, or pytest inherits the zombie
        except ChildProcessError:
            pass


@pytest.mark.skipif(os.name != "posix", reason="POSIX process states only")
def test_a_genuinely_running_process_is_reported_alive(tmp_path):
    """The other direction. A check that always says DEAD restarts a healthy
    bot every minute, which is worse than the bug it replaced."""
    probe = tmp_path / "probe.sh"
    probe.write_text(textwrap.dedent(f"""
        #!/bin/bash
        set -uo pipefail
        source <(sed -n '/^pid_is_live()/,/^}}/p' {WATCHDOG})
        sleep 30 &
        LIVE=$!
        if pid_is_live "$LIVE"; then echo "verdict=ALIVE"; else echo "verdict=DEAD"; fi
        kill "$LIVE" 2>/dev/null
    """).strip(), encoding="utf-8")
    probe.chmod(0o755)
    r = subprocess.run(["bash", str(probe)], capture_output=True, text=True, timeout=60)
    assert "verdict=ALIVE" in r.stdout, (
        f"a running process was reported dead: {r.stdout!r}{r.stderr}")


def test_an_absent_pid_is_reported_dead(tmp_path):
    probe = tmp_path / "probe.sh"
    probe.write_text(textwrap.dedent(f"""
        #!/bin/bash
        set -uo pipefail
        source <(sed -n '/^pid_is_live()/,/^}}/p' {WATCHDOG})
        if pid_is_live ""; then echo "empty=ALIVE"; else echo "empty=DEAD"; fi
        if pid_is_live 999999; then echo "absent=ALIVE"; else echo "absent=DEAD"; fi
    """).strip(), encoding="utf-8")
    probe.chmod(0o755)
    r = subprocess.run(["bash", str(probe)], capture_output=True, text=True, timeout=60)
    assert "empty=DEAD" in r.stdout and "absent=DEAD" in r.stdout, r.stdout


# ── structure ──────────────────────────────────────────────────────────────

def test_the_kill_and_the_check_use_the_same_pattern():
    """It killed more than it checked for."""
    code = _code_only(_src())
    patterns = set(re.findall(r'pkill[^\n]*-f\s+"([^"]+)"', code))
    patterns |= set(re.findall(r'pgrep[^\n]*-f\s+"([^"]+)"', code))
    assert patterns, "no pgrep/pkill patterns found — has the file changed shape?"
    assert patterns == {"$BOT_PATTERN"}, (
        f"pgrep/pkill use differing patterns {sorted(patterns)}. Detection and "
        f"termination must ask about the same set, or the watchdog SIGKILLs "
        f"processes it never looked for.")


def test_sigterm_is_sent_before_sigkill():
    """audit_chain.jsonl is tamper-evident; a SIGKILL mid-append breaks it."""
    code = _code_only(_src())
    assert "pkill -TERM" in code, (
        "no SIGTERM before the kill. deploy.sh:168-171 calls audit_chain.jsonl "
        "unrecoverable and indistinguishable from tampering if its continuity "
        "is lost, which is what killing the writer mid-append does.")
    term = code.index("pkill -TERM")
    kill9 = code.index("pkill -9") if "pkill -9" in code else len(code)
    assert term < kill9, "SIGKILL is sent before SIGTERM"


def test_the_venv_interpreter_is_preferred():
    """deploy.sh:44-49 — system python3 is 3.10 on the box, the venv is 3.11."""
    code = _code_only(_src())
    assert ".venv/bin/python" in code, (
        "the restart uses whatever `python3` PATH resolves. deploy.sh refuses "
        "a deploy on that interpreter; restarting on it produces a numpy error "
        "whose message argues for downgrading a correct pin.")


def test_the_restart_is_verified_not_just_launched():
    """`python -m bot.main` used to default to --mode cli, find no TTY, and exit
    ZERO — so reporting the PID it spawned reported success for a dead process."""
    code = _code_only(_src())
    assert "verify_bot_alive.sh" in code, (
        "the watchdog reports a restart without checking the process survived. "
        "Starting is not running; that distinction cost ~15 consecutive "
        "deploys on 2026-08-01.")


def test_the_log_does_not_live_in_a_world_writable_tmp():
    code = _code_only(_src())
    assert not re.search(r'LOGFILE=.*"?/tmp/', code), (
        "the watchdog log is in /tmp: it does not survive a reboot, and on a "
        "shared host a pre-created symlink at a predictable path redirects "
        "everything this appends")
