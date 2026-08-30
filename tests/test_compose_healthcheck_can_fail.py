"""A healthcheck that cannot fail is worse than no healthcheck.

`docker-compose.yml` gated the bot container on

    test: ["CMD-SHELL", "pgrep -f 'bot.main' > /dev/null"]

CMD-SHELL runs that as `/bin/sh -c "pgrep -f 'bot.main' > /dev/null"`, and that
shell's own /proc/<pid>/cmdline contains the string `bot.main`. So pgrep found
ITSELF. Reproduced on a machine with no bot running at all: the check matched
its own wrapper and reported healthy. `docker ps` showed green on a dead
live-trading bot forever, and api_bridge's `depends_on: service_healthy` was
being satisfied by a shell looking at its own command line.

CLAUDE.md already records this exact trap:

    Prefer `--pid`: the launcher knows what it started, and `pgrep -f` matching
    a *pattern* also matches the checking script's own command line. The first
    draft of that script reported OK for a process that had never existed.

It was found and fixed in scripts/verify_bot_alive.sh, and never carried across
to Compose. This drives the healthcheck rather than reading it, because the
whole defect was that reading it looks fine.

WHY NOT AN HTTP PROBE

The obvious replacement is `curl -sf localhost:8080/health`. It is wrong here:
bot/main.py:417-440 starts the dashboard best-effort inside a try/except that
prints "Dashboard server skipped" and carries on. A curl probe would report a
perfectly healthy bot as unhealthy whenever that optional server failed to
bind — trading one un-failable check for one that fails on a working bot.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def _bot_healthcheck_cmd() -> str:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    test = compose["services"]["bot"]["healthcheck"]["test"]
    assert test[0] == "CMD-SHELL", (
        f"bot healthcheck is no longer CMD-SHELL ({test[0]!r}); this test drives "
        f"the shell form and needs updating alongside it")
    return test[1]


def _run(cmd: str, cmdline_file: str) -> int:
    """Run the healthcheck with /proc/1/cmdline swapped for a fixture file.

    The check is written against PID 1 because the checker's own shell can
    never BE PID 1 — that is the property that makes it un-self-matchable. To
    exercise both outcomes without a container, point the same shell pipeline
    at a crafted cmdline file instead.
    """
    return subprocess.run(
        ["sh", "-c", cmd.replace("/proc/1/cmdline", cmdline_file)],
        capture_output=True, timeout=30).returncode


@pytest.fixture()
def cmdlines(tmp_path):
    bot = tmp_path / "bot_cmdline"
    bot.write_bytes(b"python\x00-m\x00bot.main\x00--mode\x00telegram\x00")
    other = tmp_path / "other_cmdline"
    other.write_bytes(b"/sbin/init\x00")
    return str(bot), str(other)


def test_the_healthcheck_reports_healthy_when_the_bot_is_pid_1(cmdlines):
    bot, _ = cmdlines
    assert _run(_bot_healthcheck_cmd(), bot) == 0, (
        "the healthcheck did not recognise a running bot — a check that fails "
        "on a healthy bot gets removed, and then there is no check")


def test_the_healthcheck_reports_unhealthy_when_the_bot_is_gone(cmdlines):
    """The direction that was broken. This is the whole point of the file."""
    _, other = cmdlines
    assert _run(_bot_healthcheck_cmd(), other) != 0, (
        "the healthcheck passed with no bot running. That is the original "
        "defect: `pgrep -f 'bot.main'` matched the CMD-SHELL wrapper's own "
        "command line, so a dead live-trading bot reported healthy forever.")


def test_the_healthcheck_does_not_ask_pgrep_about_a_pattern_it_contains():
    """The specific shape, pinned so it cannot come back wearing a new name.

    Any `pgrep -f <pattern>` where the check's own text contains <pattern>
    self-matches. Rather than guess at patterns, forbid pgrep -f in this
    healthcheck outright: the process it wants to ask about is PID 1, and
    /proc/1/cmdline answers that question without a pattern search.
    """
    cmd = _bot_healthcheck_cmd()
    assert not re.search(r"\bpgrep\b.*-f", cmd), (
        f"bot healthcheck uses `pgrep -f`: {cmd!r}. Under CMD-SHELL the check "
        f"runs inside a shell whose cmdline IS this string, so any pattern "
        f"drawn from it matches the checker. Read /proc/1/cmdline instead — "
        f"the checker is never PID 1.")


def test_every_compose_healthcheck_avoids_the_same_trap():
    """Ask which OTHER surface makes the same claim, before calling it done."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    offenders = []
    for name, svc in (compose.get("services") or {}).items():
        hc = (svc.get("healthcheck") or {}).get("test")
        if not isinstance(hc, list) or not hc or hc[0] != "CMD-SHELL":
            continue
        body = " ".join(hc[1:])
        m = re.search(r"pgrep\s+(?:-\w+\s+)*-\w*f\w*\s+['\"]?([^'\"\s|>]+)", body)
        if m and m.group(1).strip("'\"") in body:
            offenders.append(f"{name}: {body}")
    assert not offenders, (
        "these CMD-SHELL healthchecks pgrep for a pattern their own command "
        "line contains, so they match themselves and cannot fail:\n  "
        + "\n  ".join(offenders))


def test_the_bot_is_pid_1_so_the_check_is_asking_about_the_bot():
    """The premise the fix rests on. If `command:` ever gains a wrapper —
    a shell, an entrypoint script, `sh -c` — the bot stops being PID 1 and
    reading /proc/1/cmdline silently starts answering about the wrapper."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    cmd = compose["services"]["bot"]["command"]
    cmd_s = cmd if isinstance(cmd, str) else " ".join(cmd)
    assert "bot.main" in cmd_s, f"bot command no longer runs bot.main: {cmd_s!r}"
    assert not re.match(r"\s*(sh|bash|/bin/sh|/bin/bash)\b", cmd_s), (
        f"bot command is wrapped in a shell ({cmd_s!r}), so PID 1 is the shell "
        f"and the healthcheck now reports on the wrapper rather than the bot. "
        f"Use `exec` in the wrapper, or change the healthcheck with it.")
    assert os.path.exists("/proc/1/cmdline"), (
        "no /proc on this platform — the healthcheck's mechanism cannot be "
        "verified here")
