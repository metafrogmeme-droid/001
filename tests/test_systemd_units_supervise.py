"""The units restart the processes, and cannot be defeated by their own options.

RUNECLAW runs two processes and NOTHING was supervising either. Across the week
of 2026-08-25 the bot died repeatedly and every recovery began with a human
noticing; the bridge was down for hours on the 25th because nothing had ever
started it.

`scripts/launch_all.sh.template` is a DEPLOY-time gate — it refuses to print
DEPLOY_DONE unless both processes are alive and both ports answer — and it has
nothing to say about 03:00 on a Tuesday. These units are the other half.

These tests read the unit files because that is what an operator copies into
/etc/systemd/system, and because the options that matter here are the ones that
are silently wrong rather than the ones that crash: `Restart=on-failure` looks
correct and does not restart the failure that actually happened, and a bare
`python` looks correct on every box except this one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "scripts" / "systemd"
BOT = HERE / "runeclaw-bot.service"
BRIDGE = HERE / "runeclaw-bridge.service"
GATEWAY = ROOT / "scripts" / "cloudflared" / "runeclaw-gateway.service"

#: The two processes this file was written for.
UNITS = (BOT, BRIDGE)
#: Every unit in the repo. The gateway is included in the checks that are about
#: SUPERVISION rather than about these two processes — it had the start-limit
#: defect and would have kept it if the new units had been checked alone.
ALL_UNITS = (BOT, BRIDGE, GATEWAY)


def _code_only(text: str) -> str:
    """Comment lines blanked, line count preserved.

    The units document at length WHY each option is set, quoting the very
    options they must not use — `Restart=on-failure` and `verify_deploy_source`
    both appear in prose explaining their own absence. A scan that matched the
    explanation would pass on a unit that had the defect and fail on one that
    merely described it. Fifth instance of the family CLAUDE.md records, and
    the first one where the absent-string assertions below could not be written
    at all without it.
    """
    out = []
    for line in text.splitlines():
        out.append("" if line.lstrip().startswith("#") else line)
    return "\n".join(out)


@pytest.fixture(scope="module")
def code() -> dict[Path, str]:
    for u in ALL_UNITS:
        assert u.exists(), f"{u} is gone — the unit is the deliverable"
    return {u: _code_only(u.read_text(encoding="utf-8")) for u in ALL_UNITS}


# ── ask systemd, do not assert from memory ─────────────────────────────────

def test_systemd_accepts_every_directive() -> None:
    """The check that would have caught the start-limit bug on day one.

    Every source scan in this file encodes what I BELIEVE systemd does. This
    one asks it. `systemd-analyze verify` reports a directive in the wrong
    section as `Unknown key name ... ignoring`, which is the exact failure that
    sat in runeclaw-gateway.service for a year behind a correct-looking line.

    Path complaints are ignored deliberately: the units name /home/mulerun on
    the deploy box, which no CI checkout has. Filtering to `Unknown key name`
    keeps the assertion about the thing that is portable — the schema — rather
    than about the filesystem it happens to run on.

    Skipped where systemd is absent rather than passing quietly. A check that
    silently does nothing off-box is a green tick that means nothing, and this
    file exists because of a green tick that meant nothing.
    """
    import shutil
    import subprocess

    exe = shutil.which("systemd-analyze")
    if not exe:
        pytest.skip("systemd-analyze not available — the schema was NOT checked")

    for u in ALL_UNITS:
        proc = subprocess.run(
            [exe, "verify", str(u)],
            capture_output=True, text=True, timeout=60,
        )
        blob = f"{proc.stdout}\n{proc.stderr}"
        bad = [ln for ln in blob.splitlines() if "Unknown key name" in ln]
        assert bad == [], (
            f"systemd rejects directives in {u.name} — they are DISCARDED, not "
            "an error, so the unit runs with defaults:\n  " + "\n  ".join(bad)
        )


# ── both processes are supervised, not just the memorable one ───────────────

def test_there_is_a_unit_for_each_process(code: dict[Path, str]) -> None:
    """The bridge is the one that gets forgotten.

    It has no Telegram presence and no crash to notice: its absence shows up
    only as three dashboard panels returning 502 while every other check
    reports the system healthy.
    """
    assert "bot.main" in code[BOT]
    assert "api_bridge.py" in code[BRIDGE]


def test_both_restart_always_not_on_failure(code: dict[Path, str]) -> None:
    """`on-failure` would not have restarted the failure that happened.

    2026-08-01: `python -m bot.main` defaulted to `--mode cli`, found no TTY,
    and EXITED ZERO across ~15 consecutive deploys. A clean exit is not a
    failure, so `Restart=on-failure` leaves nothing running — the precise
    outcome these units exist to prevent, reached through an option that reads
    as the more careful choice.
    """
    for u in UNITS:
        assert re.search(r"^Restart=always$", code[u], re.M), (
            f"{u.name} does not set Restart=always"
        )
        assert not re.search(r"^Restart=on-failure", code[u], re.M), (
            f"{u.name} uses on-failure, which does not restart a clean exit"
        )


def _section_of(text: str, key: str) -> str | None:
    """Which [Section] a directive is written under, or None if absent.

    Placement is the whole point here: systemd does not reject a directive in
    the wrong section, it discards it with a warning nobody reads.
    """
    section = None
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            section = s[1:-1]
        elif s.startswith(f"{key}="):
            return section
    return None


def test_no_unit_gives_up(code: dict[Path, str]) -> None:
    """systemd's default start limit defeats the whole point.

    Five restarts in 10s and the unit is failed and left alone — a supervisor
    that stops supervising during exactly the incident it exists for.

    THE SECTION IS THE TEST. runeclaw-gateway.service carried this directive in
    [Service] from 2026-08-07 until this commit, where systemd 255 discards it:

        Unknown key name 'StartLimitIntervalSec' in section 'Service', ignoring.

    So the unit whose own comment said an unattended tunnel must never give up
    after five attempts gave up after five attempts, for a year, with the
    correct-looking line right there in the file. A directive in the wrong
    section is not a syntax error — it is a guard that is present and never
    reached, which is the failure this repository has recorded most often.
    """
    for u in ALL_UNITS:
        assert re.search(r"^StartLimitIntervalSec=0$", code[u], re.M), (
            f"{u.name} will stop retrying after systemd's default burst limit"
        )
        where = _section_of(code[u], "StartLimitIntervalSec")
        assert where == "Unit", (
            f"{u.name} puts StartLimitIntervalSec in [{where}] — systemd only "
            "honours it in [Unit] and silently ignores it anywhere else"
        )


def test_both_start_at_boot(code: dict[Path, str]) -> None:
    """A unit nobody enabled is a unit that does not come back from a reboot."""
    for u in UNITS:
        assert re.search(r"^WantedBy=multi-user\.target$", code[u], re.M)


# ── the two spellings that are wrong only on this box ──────────────────────

def test_the_bot_mode_flag_is_explicit(code: dict[Path, str]) -> None:
    """`--mode telegram` spelled out, for the same reason the launcher does it.

    The default is telegram now. Passing it anyway costs nothing and survives
    the default changing back — and under a supervisor the cost of that
    regression is higher than it was, because Restart=always would loop on it
    forever rather than failing one deploy.
    """
    assert "--mode telegram" in code[BOT]


def test_no_unit_invokes_a_bare_python(code: dict[Path, str]) -> None:
    """The box has no `python`, as scripts/verify_bot_alive.sh records.

    A unit whose ExecStart cannot resolve fails at start, and Restart=always
    then retries that failure every 15 seconds indefinitely.
    """
    for u in UNITS:
        m = re.search(r"^ExecStart=(\S+)", code[u], re.M)
        assert m, f"{u.name} has no ExecStart"
        interp = m.group(1)
        assert not interp.endswith("/python"), (
            f"{u.name} runs `{interp}`; the box has no bare `python` — use "
            "python3 or an explicit venv interpreter"
        )
        assert interp.startswith("/"), (
            f"{u.name} runs `{interp}`; systemd requires an absolute ExecStart"
        )


# ── what must NOT gate a restart ───────────────────────────────────────────

def test_the_source_check_does_not_gate_a_restart(code: dict[Path, str]) -> None:
    """Right code is a DEPLOY question, not a 3am one.

    verify_deploy_source.sh reads the network and exits 3 when it cannot reach
    it. Wiring it into the unit means a network blip stops the bot coming back
    — a supervisor that fails exactly when it is needed. The 2026-08-20 stale
    deploy is real; this is not its fix, and the launcher still runs it.

    Comments are blanked first: the README and both units discuss this by name.
    """
    for u in UNITS:
        assert "verify_deploy_source" not in code[u], (
            f"{u.name} gates a restart on a network-dependent source check"
        )


def test_the_units_do_not_depend_on_each_other(code: dict[Path, str]) -> None:
    """Independent processes, independent fates.

    Ordering them means one failing to start holds the other down, and the
    bridge's whole failure mode is having its fate tied to something else.
    """
    # The dependency name can appear ANYWHERE in the value — these directives
    # take a space-separated list. The first version of this assertion anchored
    # `runeclaw-` immediately after the `=`, so the mutation
    #
    #     After=network-online.target runeclaw-bot.service
    #
    # passed the whole file. That is the failure mode CLAUDE.md records for
    # absent-string assertions, arriving through an anchor that was too tight
    # rather than a match that was too loose: the test looked precise and
    # checked a case nobody would write by hand.
    for u in UNITS:
        for directive in ("Requires", "BindsTo", "PartOf", "After", "Wants"):
            for m in re.finditer(rf"^{directive}=(.*)$", code[u], re.M):
                others = [t for t in m.group(1).split()
                          if t.startswith("runeclaw-") and t != f"{u.stem}.service"]
                assert others == [], (
                    f"{u.name} has {directive}={' '.join(others)} — one unit "
                    "failing to start would hold the other down, and the "
                    "bridge's whole failure mode is a fate tied to something else"
                )


# ── started is not serving ─────────────────────────────────────────────────

def test_each_unit_checks_that_its_port_answers(code: dict[Path, str]) -> None:
    """Type=simple calls a unit started the moment the binary is exec'd.

    Bound to the wrong interface, stuck importing, or listening on a port
    nobody configured — none of those are distinguishable from healthy without
    asking the port. The ports are what the web app talks to.
    """
    assert "8080/gateway/health" in code[BOT], "the gateway port is never probed"
    assert "8000/health" in code[BRIDGE], "the bridge port is never probed"
    for u in UNITS:
        assert re.search(r"^ExecStartPost=.*wait_for_port\.sh", code[u], re.M)


def test_the_port_windows_are_generous(code: dict[Path, str]) -> None:
    """A tight health check under Restart=always is a self-inflicted outage.

    Too eager and a slow-but-healthy start is converted into a restart, which
    then loops forever. The asymmetry is the design: checking too patiently
    only delays recycling something already broken.
    """
    for u in UNITS:
        m = re.search(r"wait_for_port\.sh\s+\S+\s+(\d+)", code[u])
        assert m, f"{u.name} passes no explicit window to wait_for_port"
        assert int(m.group(1)) >= 90, (
            f"{u.name} allows only {m.group(1)}s for its port to answer"
        )


def test_the_bridge_gets_the_longer_window(code: dict[Path, str]) -> None:
    """uvicorn imports the whole application before it binds."""
    windows = {}
    for u in UNITS:
        windows[u] = int(re.search(r"wait_for_port\.sh\s+\S+\s+(\d+)", code[u]).group(1))
    assert windows[BRIDGE] > windows[BOT]


# ── the helpers the units call must exist and be runnable ──────────────────

def test_the_helpers_referenced_by_the_units_exist(code: dict[Path, str]) -> None:
    """A unit referencing a missing script fails every start, forever.

    Under Restart=always that is not a one-off error, it is a permanent loop —
    so the reference is checked here rather than discovered on the box.
    """
    for name in ("wait_for_port.sh", "runeclaw-status.sh"):
        p = HERE / name
        assert p.exists(), f"{name} is referenced but missing"
        import os
        assert os.access(p, os.X_OK), f"{name} is not executable"


def test_the_status_script_reads_the_restart_count() -> None:
    """The number that makes a crashloop legible.

    With StartLimitIntervalSec=0 a unit is `active (running)` fifteen seconds
    after every crash, so a process that has died 200 times today and one that
    has run untouched for a week look IDENTICAL to `systemctl status`. A green
    light that rules one cause out and names none is the exact shape CLAUDE.md
    warns about; NRestarts is what separates them.
    """
    src = _code_only((HERE / "runeclaw-status.sh").read_text(encoding="utf-8"))
    assert "NRestarts" in src, (
        "the status script cannot distinguish a crashloop from a healthy unit"
    )


def test_the_status_script_separates_unknown_from_down() -> None:
    """Three outcomes, not two.

    A box with no systemd, a unit that was never installed, and a healthy unit
    are three different situations. Reporting the middle one as merely
    "inactive" tells an operator their bot is supervised and currently stopped,
    when in truth nothing would ever restart it — the most expensive wrong
    answer this script could give.
    """
    src = _code_only((HERE / "runeclaw-status.sh").read_text(encoding="utf-8"))
    assert "NOT INSTALLED" in src
    assert "exit 3" in src, "there is no could-not-tell exit"
    assert re.search(r"systemctl is not available", src)
