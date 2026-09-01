#!/usr/bin/env python3
"""Every boolean env flag that DEFAULTS TO ON — i.e. every protection.

RC-2026-005. A flag defaulting True is a protection; setting it false removes
that protection. One absent from `.env.example` is a control an operator can
disable through a variable the file documenting configuration never names.

WHY THIS IS AST AND NOT GREP, and it is not a style preference. The audit that
raised this counted **110** default-ON flags with **90** undocumented. Four of
those 90 were `ENV_NAME`, `THING_ENABLED`, `WRAPPED_ENABLED` and
`RUNECLAW_TEST_SWITCH` — example strings inside
`tests/test_flag_prose_matches_default.py`'s own fixtures and one test's
monkeypatch. A literal scan cannot tell a flag from a string that looks like
one, which is the same defect that produced two of this audit's recorded false
positives (`WEB3_RPC_*` and `LLM_TIER_*_MODEL`, both read via constructed keys
a grep could not see).

Reading the call arguments means a name only counts when something actually
reads it, and `tests/` is excluded because a fixture is not a deployment.

FIVE READERS, NOT ONE — the other direction of the same error. A first pass
here scanned only `_env_bool` and missed `LLM_BACKGROUND_SCANS`, which is real
and defaults ON but is read by `_env_switch` (a separate helper, because
`_env_bool` reads "off" as True — see its docstring). `_env_flag` and `_env_on`
exist too. Sound numbers: **106 default-ON, 86 undocumented** at the time of
writing.

    python3 scripts/safety_flag_inventory.py            # report
    python3 scripts/safety_flag_inventory.py --section  # the .env.example block
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"

#: Helpers that turn an env var into a bool. All of them, or the inventory is
#: a subset reported as the whole.
READERS = ("_env_bool", "_env_switch", "_env_flag", "_env_on")

BEGIN = "# >>> BEGIN generated: default-ON safety flags (RC-2026-005) >>>"
END = "# <<< END generated: default-ON safety flags <<<"

#: The money-path subset, listed first because an operator skimming has to see
#: these before the tuning knobs. Named by the finding itself.
MONEY_PATH = {
    "UNPROTECTED_GUARD_ENABLED", "UNPROTECTED_ESCALATION_ENABLED",
    "SLIPPAGE_GUARD_ENABLED", "PER_STRATEGY_NOTIONAL_CAP_ENABLED",
    "LLM_DIRECTION_GUARD_ENABLED", "API_DEGRADE_REDUCE_ONLY",
    "GUARDIAN_FIREWALL_ENABLED", "GUARDIAN_ESCAPE_ENABLED",
    "GUARDIAN_RISK_SENTINEL_ENABLED", "GUARDIAN_DIGITAL_TWIN_ENABLED",
    "TRAILING_STOP_ENABLED", "TIME_STOP_ENABLED", "MTF_ALIGNMENT_GATE_ENABLED",
}


def default_on_flags() -> dict[str, str]:
    """{FLAG: "path:line"} for every boolean env flag defaulting True."""
    found: dict[str, tuple[str, bool]] = {}
    for f in sorted(ROOT.rglob("*.py")):
        rel = f.relative_to(ROOT).as_posix()
        if rel.startswith("tests/") or "node_modules" in rel:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id in READERS):
                continue
            if not n.args or not isinstance(n.args[0], ast.Constant):
                continue
            if not isinstance(n.args[0].value, str):
                continue
            name = n.args[0].value
            dflt = (n.args[1].value if len(n.args) > 1
                    and isinstance(n.args[1], ast.Constant) else False)
            prev = found.get(name)
            # Read in two places: default-ON anywhere makes it a protection.
            if prev is None or (dflt is True and prev[1] is not True):
                found[name] = (f"{rel}:{n.lineno}", dflt)
    return {k: v[0] for k, v in found.items() if v[1] is True}


def declared_in_env_example(path: pathlib.Path | None = None) -> set[str]:
    """Names `.env.example` mentions — commented-out counts as mentioned.

    `path` exists so a test can drive this against a planted file. Without
    that seam the whole detector could be replaced by `return []` and every
    assertion about it still passed — verified by mutation, and it is the
    defect this script exists to catch, one level up.
    """
    txt = (path or ENV_EXAMPLE).read_text(encoding="utf-8")
    return set(re.findall(r"^\s*#?\s*([A-Z][A-Z0-9_]+)\s*=", txt, re.M))


def undocumented(path: pathlib.Path | None = None) -> list[str]:
    declared = declared_in_env_example(path)
    return sorted(k for k in default_on_flags() if k not in declared)


def section() -> str:
    """The generated block: every default-ON flag, commented at its default."""
    on = default_on_flags()
    names = sorted(on)
    money = [n for n in names if n in MONEY_PATH]
    rest = [n for n in names if n not in MONEY_PATH]
    out = [
        BEGIN,
        "#",
        "# Every boolean flag below DEFAULTS TO ON. Each one is a protection,",
        "# and setting it false removes that protection. They are listed here",
        "# COMMENTED OUT at their real defaults: nothing changes by their",
        "# presence, but an operator can no longer disable a control through a",
        "# variable this file never named.",
        "#",
        "# Generated by scripts/safety_flag_inventory.py --section and pinned by",
        "# tests/test_safety_flags_are_documented.py. Do not hand-edit: add the",
        "# flag in code and regenerate, or the two will disagree.",
        "#",
        f"# ── money path ({len(money)}) — read these before the rest ──",
    ]
    for n in money:
        out += [f"# {on[n]}", f"#{n}=true"]
    out += ["#", f"# ── everything else ({len(rest)}) ──"]
    for n in rest:
        out += [f"# {on[n]}", f"#{n}=true"]
    out.append(END)
    return "\n".join(out) + "\n"


def main() -> int:
    # `... | head` closes the pipe and a bare print then raises. A reporting
    # tool that tracebacks when you page it is a tool people stop running.
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (ImportError, AttributeError, ValueError):
        pass
    if "--section" in sys.argv:
        sys.stdout.write(section())
        return 0
    on = default_on_flags()
    miss = undocumented()
    print(f"default-ON flags : {len(on)}")
    print(f"undocumented     : {len(miss)}")
    for n in miss:
        mark = "  [money path]" if n in MONEY_PATH else ""
        print(f"  {n:42} {on[n]}{mark}")
    return 1 if miss else 0


if __name__ == "__main__":
    raise SystemExit(main())
