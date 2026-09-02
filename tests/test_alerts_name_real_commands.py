"""An alert that tells you to type a command the bot does not have.

Live, 2026-09-02. The engine-degraded alert ends:

    👉 /status — check engine state
    👉 /positions — verify SL/TP are in place

`/positions` was never registered. The operator typed it, mid-incident, on the
most safety-critical instruction in the product, and got back *"I don't have a
/positions command. Did you mean /open_positions?"*

`command_menu.suggest()` even lists that exact pair under "what people actually
mistype". It is not a mistype when we printed it.

Auditing the rest found a second, worse one: an alert recommending an
auto-close pointed at `/close {base}` — a command that does not exist, taking
an argument shape the nearest real command (`/liveclose TRADE_ID`) does not
accept. Wrong twice over, on the message telling someone to act.

Neither was findable by running the code: both live inside f-strings that
render perfectly. The only thing that distinguishes them from a working
pointer is whether the name on the other side is registered, and nothing
had ever asked.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: The call-to-action marker in an alert body: "👉 /command — do this".
#: Deliberately narrow. A bare "/foo" in prose could be a URL path, a file, or
#: a route (`/api/positions`, `/gateway/positions` both exist); this marker
#: only ever precedes something we are telling a human to type.
POINTER = re.compile(r'(?:\\U0001f449|\U0001f449)\s*/([a-z0-9_]+)')

#: Files whose strings reach an operator as instructions.
SURFACES = [
    "bot/core/proactive_monitor.py",
    "bot/core/engine.py",
    "bot/risk/risk_engine.py",
]


def registered_commands() -> set[str]:
    """Command names the handler registers, parsed from source so this needs
    no bot token — same approach as tests/test_command_menu.py."""
    text = (ROOT / "bot" / "skills" / "telegram_handler.py").read_text(encoding="utf-8")
    return set(re.findall(r'\(\s*"([a-z0-9_]+)"\s*,\s*(?:self\.)?_cmd', text))


def _pointers(rel: str) -> list[tuple[int, str]]:
    """Every (line number, command) an operator is told to type in `rel`."""
    out = []
    for i, line in enumerate(Path(ROOT / rel).read_text(encoding="utf-8").split("\n"), 1):
        # Strip whole-line comments: a comment quoting a dead command name
        # while explaining why it was removed is indistinguishable from the
        # code doing it, and that has produced four false failures in this
        # repo. The fix for /close leaves exactly such a comment behind.
        if line.lstrip().startswith("#"):
            continue
        for m in POINTER.finditer(line):
            out.append((i, m.group(1)))
    return out


@pytest.mark.parametrize("rel", SURFACES)
def test_every_command_an_alert_tells_you_to_type_exists(rel):
    known = registered_commands()
    dead = [(ln, c) for ln, c in _pointers(rel) if c not in known]
    assert not dead, (
        f"{rel} tells the operator to type a command that is not registered: "
        + ", ".join(f"/{c} (line {ln})" for ln, c in dead)
        + ". Register it in telegram_handler, or point at the one that exists."
    )


def test_the_scan_actually_finds_pointers():
    """Anti-vacuity. A regex that matches nothing passes the test above
    forever, which is the failure mode of every guard written from a fixed
    list of known sites."""
    found = _pointers("bot/core/proactive_monitor.py")
    assert len(found) >= 10, f"only {len(found)} pointers found — the scan has gone blind"


def test_the_two_that_were_dead_are_the_two_that_were_fixed():
    """Named rather than left to the sweep, because a regression here is
    silent: the alert still renders, and only someone mid-incident finds out."""
    known = registered_commands()
    assert "positions" in known, (
        "/positions is unregistered again — the degraded-loop alert points at it")
    assert "liveclose" in known
    body = (ROOT / "bot" / "core" / "proactive_monitor.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in body.split("\n") if not ln.lstrip().startswith("#"))
    assert "/close {base}" not in code, (
        "the auto-close alert points at /close again — it does not exist, and "
        "/liveclose takes a trade ID rather than a symbol")


def test_the_positions_alias_and_open_positions_are_the_same_handler():
    """An alias that drifts to a different handler is worse than none: the
    alert would work and show something else."""
    text = (ROOT / "bot" / "skills" / "telegram_handler.py").read_text(encoding="utf-8")
    pairs = dict(re.findall(r'\(\s*"([a-z0-9_]+)"\s*,\s*self\.(_cmd[a-z0-9_]+)', text))
    assert pairs.get("positions") == pairs.get("open_positions") == "_cmd_open_positions"
