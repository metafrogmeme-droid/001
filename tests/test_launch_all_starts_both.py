"""The launcher starts BOTH processes, and cannot report success on one.

RUNECLAW runs two processes and only one was ever being started:

    python -m bot.main    Telegram + engine, and the GATEWAY on :8080
    python api_bridge.py  a separate uvicorn app on :8000

On 2026-08-25 the bridge was down for hours. Nothing had crashed — nothing had
ever started it. The bot restarted fine, the gateway recovered, the status page
called the system healthy, and the insight/patterns/lab panels returned 502
until an operator noticed broken pages.

A deploy that starts one of two processes and prints DEPLOY_DONE is the same
defect this repository spends most of its guard tests preventing: running a
subset and reporting it as the whole.

These tests read the TEMPLATE, because the live launcher lives outside the repo
by design (CLAUDE.md: "Keep the launcher outside the repo. Anything inside it is
one `git reset --hard` away from reverting."). The template is what an operator
copies, so the template is what must be right — and it is the only copy version
control can defend.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "scripts" / "launch_all.sh.template"


@pytest.fixture(scope="module")
def src() -> str:
    assert TEMPLATE.exists(), f"{TEMPLATE} is gone — the launcher template is the deliverable"
    return TEMPLATE.read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """Comment lines blanked, line count preserved.

    The template documents at length WHY each process is started, quoting the
    very commands it must contain. A scan that matched the prose would pass on a
    launcher that only talks about starting the bridge — the fifth instance of
    the family CLAUDE.md records.
    """
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        out.append("" if stripped.startswith("#") else line)
    return "\n".join(out)


# ── both processes, or it is not a launcher ──────────────────────────────────

def test_it_starts_the_bot(src: str) -> None:
    code = _code_only(src)
    assert re.search(r"nohup python -m bot\.main", code), "the launcher does not start the bot"


def test_it_starts_the_api_bridge(src: str) -> None:
    """The one that was missing, and the whole reason this file exists."""
    code = _code_only(src)
    assert re.search(r"nohup python api_bridge\.py", code), (
        "the launcher does not start api_bridge — insight, patterns and lab will "
        "502 while every other check reports the deploy healthy"
    )


def test_both_launches_are_smoke_tested(src: str) -> None:
    """Alive a moment later, not merely launched.

    The 2026-08-01 incident: ~15 consecutive deploys where the bot started,
    found no Telegram connection, and EXITED ZERO. Every one printed
    DEPLOY_DONE. None left a running process.
    """
    code = _code_only(src)
    checks = re.findall(r"verify_bot_alive\.sh --pid", code)
    assert len(checks) >= 2, (
        f"{len(checks)} smoke test(s) for 2 processes — one of them can die "
        "silently and the deploy still succeeds"
    )


def test_the_bot_mode_flag_is_explicit(src: str) -> None:
    """`--mode telegram` spelled out.

    The default was once `cli`, which finds no TTY and exits zero. It defaults
    to telegram now; passing it anyway survives the default changing back.
    """
    code = _code_only(src)
    assert "--mode telegram" in code


# ── the shell traps that have actually bitten ────────────────────────────────

def test_no_verify_call_shares_a_line_with_an_ampersand(src: str) -> None:
    """`&` binds looser than `&&`.

        cd ~/runeclaw && nohup python -m bot.main ... &  scripts/verify_bot_alive.sh

    parses as `(cd ... && nohup ...) & scripts/...` — the cd goes into the
    background subshell and the gate runs in whatever directory the shell
    started in, where the relative path does not resolve. Observed 2026-08-08:
    the gate never executed and the deploy was accepted on visual inspection.
    """
    code = _code_only(src)
    bad = [ln for ln in code.splitlines() if re.search(r"&\s+\S*verify_bot_alive", ln)]
    assert bad == [], "a smoke test shares a line with a background `&`:\n  " + "\n  ".join(bad)


def test_it_fails_loudly_rather_than_continuing(src: str) -> None:
    """`set -e` plus an explicit die on every gate.

    A launcher that carries on past a failed check reaches DEPLOY_DONE with
    nothing running, which is the outcome all of this exists to prevent.
    """
    code = _code_only(src)
    assert re.search(r"set -euo pipefail", code)
    assert code.count("|| die ") >= 4, (
        "not every gate refuses — a check whose failure is ignored is not a check"
    )


# ── DEPLOY_DONE is earned, not announced ─────────────────────────────────────

def test_deploy_done_comes_after_every_gate(src: str) -> None:
    code = _code_only(src)
    done = code.index("DEPLOY_DONE")
    for marker in ("verify_bot_alive.sh --pid", "check_port"):
        assert code.index(marker) < done, f"DEPLOY_DONE is printed before {marker} runs"


def test_both_ports_must_answer_not_merely_exist(src: str) -> None:
    """A process can be alive and not serving.

    Bound to the wrong interface, stuck importing, or listening on a port
    nobody configured — `kill -0` cannot tell any of those from healthy. The
    ports are what the web app talks to, so the ports are what get checked.
    """
    code = _code_only(src)
    assert "8080/gateway/health" in code, "the gateway port is never probed"
    assert "8000/health" in code, "the bridge port is never probed"
    assert code.count("check_port ") >= 2


def test_the_source_check_runs_before_anything_starts(src: str) -> None:
    """Wrong code is worse than no code.

    2026-08-20: a deploy reported success while landing 255 commits stale,
    because `origin` on that box is a mirror. Every other check passed — each
    was true of the stale tree. The only thing wrong was WHICH CODE, and
    nothing asked.
    """
    code = _code_only(src)
    # The INVOCATION, not the `if [ -x ... ]` guard that names the same script.
    # Matching the bare filename passed against a template whose actual call had
    # been deleted — the condition line kept the string alive and the test kept
    # crediting it. Anchor on the thing that refuses.
    m = re.search(r"scripts/verify_deploy_source\.sh\s*\|\|\s*die", code)
    assert m, (
        "the source check is never actually invoked with a refusal — a deploy "
        "can land on stale code and start it"
    )
    first_launch = code.index("nohup python")
    assert m.start() < first_launch, "the source check runs after a process has started"


# ── it stays outside the repo ────────────────────────────────────────────────

def test_it_ships_as_a_template_not_an_executable(src: str) -> None:
    """The filename carries the rule.

    A launcher tracked by the thing it deploys is one `git reset --hard` from
    reverting — which is precisely what happened across ~15 redeploys on
    2026-08-01, restoring a flagless launcher every time.
    """
    assert TEMPLATE.name.endswith(".template")
    assert not (TEMPLATE.parent / "launch_all.sh").exists(), (
        "an executable launch_all.sh is committed in the repo — copy it outside "
        "instead, or the next hard reset silently reverts the deploy procedure"
    )
    code = _code_only(src)
    assert "RUNECLAW_REPO" in code, "the template hardcodes a repo path"
