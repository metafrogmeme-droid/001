#!/usr/bin/env python3
"""Lint ratchet for the DECLARED ruff config, which nothing was enforcing.

WHY THIS EXISTS
---------------
``pyproject.toml`` declares::

    [tool.ruff.lint]
    select = ["E", "F", "W", "I"]

and CI ran a strict subset of it: ``E9,F821,F811`` over ``bot/ tests/`` and
``F401,F541`` over ``bot/`` alone. Both of those pass. The declared config
reported **1,361** findings on the same tree.

That gap is the defect, not the 1,361. A config in ``pyproject.toml`` reads as a
statement about the codebase -- anyone running plain ``ruff check`` gets the
declared set -- and a statement nothing checks is one that drifts. This closes
the gap the way this repo closes every other one of its kind
(``known_failures.txt``, ``unreachable_baseline.txt``, the npm and cargo
advisory gates): record what is there, fail on what is NEW.

WHY NOT JUST FIX THEM
---------------------
113 were safe auto-fixes and are gone; this gate starts from what remains. The
rest must not be swept, and the two biggest categories say why:

``I001`` (607, import sorting)
    Ruff marks this fix UNSAFE, and in this repo that is not academic.
    ``bot/config.py`` runs ``load_dotenv`` and ``secrets_vault.seed_and_restore``
    at import; ``bot/api/auth_routes.py`` is mounted by an import in
    ``api_bridge.py``. Reordering imports reorders side effects. A 607-file
    mechanical rewrite of import order in a codebase whose imports DO things is
    a change that has to be made deliberately and tested, not applied by a
    linter in a lint-cleanup commit.

``F841`` (99, unused variables)
    CLAUDE.md rules this out in words: "Hot-path F841 is deliberately NOT
    auto-fixed: the roadmap flags some as dropped logic needing manual triage."
    An unused variable in a money path is as likely to be a missing use as a
    dead store, and only reading each one tells you which.

``E501`` (247, line length) and ``E402`` (93, import not at top) are the
remaining bulk. Neither is auto-fixable, both are cosmetic-to-deliberate here
(``bot/config.py`` already carries ``# noqa: E402`` where the late import is the
point), and a reflowing sweep would bury real changes in diff noise.

So the ratchet holds the line and each category gets cleared on purpose, by
someone who has read it -- which is what lowering a baseline number means.

USAGE
-----
    python3 scripts/ruff_gate.py             # gate (CI)
    python3 scripts/ruff_gate.py --update    # re-record, deliberately
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "ruff_baseline.json"

_CODE = re.compile(r"^[^:]+:\d+:\d+:\s+([A-Z]+[0-9]+)\s")



def _pinned(tool: str) -> str | None:
    """The version CI installs, read from requirements-ci.txt."""
    req = ROOT / "requirements-ci.txt"
    if not req.exists():
        return None
    m = re.search(rf"^{tool}==([0-9][^\s#]*)", req.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def _running(tool: str) -> str | None:
    proc = subprocess.run([tool, "--version"], capture_output=True, text=True, cwd=ROOT)
    m = re.search(r"([0-9]+\.[0-9]+\.[0-9]+)", (proc.stdout or "") + (proc.stderr or ""))
    return m.group(1) if m else None


def check_version(tool: str) -> None:
    """Refuse to compare counts produced by a different analyser.

    THE FIRST CI RUN OF THIS GATE FAILED HERE, AND THE FAILURE WAS A LIE.

    The baseline was recorded with mypy 1.19.1; CI pins 1.15.0. It reported
    654 errors against a baseline of 390 and named eleven classes as having
    grown -- union-attr 44 -> 195, no-any-return 58 -> 105, plus a `list-item`
    class that does not exist in the other version at all. Not one of those was
    a code change. Every number came from a different analyser looking at an
    identical tree.

    A count is only comparable to a count from the same tool version, so a
    mismatch is NOT a verdict -- it is the absence of one, and this repo has a
    rule about reporting those as verdicts. Exit 2, distinct from the 1 that
    means "something really did grow", so a launcher reading truthiness still
    fails closed while a human reading the message learns which of the two
    happened.
    """
    pinned, running = _pinned(tool), _running(tool)
    if pinned is None or running is None:
        print(f"WARNING: could not determine the {tool} version to compare "
              f"(pinned={pinned}, running={running}); counts may not be comparable")
        return
    if pinned != running:
        print(f"CANNOT CHECK: baseline counts were recorded with {tool} {pinned} "
              f"(the version requirements-ci.txt installs) and this is {tool} "
              f"{running}.", file=sys.stderr)
        print(f"  Different {tool} versions report different counts on identical "
              f"code, so any growth or shrinkage reported here would be an "
              f"artefact of the toolchain rather than a fact about the tree.",
              file=sys.stderr)
        print(f"  Install the pinned version, or re-record deliberately with "
              f"--update once requirements-ci.txt moves.", file=sys.stderr)
        raise SystemExit(2)

def current_counts() -> Counter:
    """Per-rule counts from the DECLARED config -- no --select override."""
    proc = subprocess.run(
        ["ruff", "check", ".", "--output-format=concise", "--no-fix"],
        capture_output=True, text=True, cwd=ROOT)
    # ruff exits 1 when it finds anything, which is the normal case here.
    if proc.returncode not in (0, 1):
        print(f"ruff failed to run (exit {proc.returncode}):\n{proc.stderr}",
              file=sys.stderr)
        raise SystemExit(2)
    counts: Counter = Counter()
    for line in proc.stdout.splitlines():
        m = _CODE.match(line)
        if m:
            counts[m.group(1)] += 1
    return counts


def _load_baseline() -> dict:
    if not BASELINE.exists():
        print(f"No {BASELINE}. Create it with: python3 scripts/ruff_gate.py --update",
              file=sys.stderr)
        raise SystemExit(2)
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def main() -> int:
    check_version("ruff")
    counts = current_counts()
    total = sum(counts.values())

    if "--update" in sys.argv:
        BASELINE.write_text(json.dumps({
            "_comment": "Per-rule ruff counts under the config declared in "
                        "pyproject.toml. A RATCHET: a rule may only go DOWN. "
                        "Regenerate with scripts/ruff_gate.py --update, and "
                        "only alongside the commit that actually lowered it.",
            "total": total,
            "counts": dict(sorted(counts.items())),
        }, indent=2) + "\n", encoding="utf-8")
        print(f"Baseline updated: {total} findings across {len(counts)} rules")
        return 0

    baseline = _load_baseline()
    base_counts = baseline.get("counts", {})

    grew = {r: (n, base_counts.get(r, 0))
            for r, n in counts.items() if n > base_counts.get(r, 0)}
    shrank = {r: (n, base_counts[r])
              for r, n in ((r, counts.get(r, 0)) for r in base_counts)
              if n < base_counts[r]}

    print(f"ruff (declared config): {total} findings across {len(counts)} rules")
    print(f"baseline:               {baseline.get('total')} findings")

    if grew:
        print("\nNEW lint findings -- this gate fails on growth, not on the backlog:")
        for rule, (now, was) in sorted(grew.items()):
            print(f"  {rule}: {was} -> {now}  (+{now - was})")
        print("\nFix them, or if the increase is deliberate, re-record with")
        print("  python3 scripts/ruff_gate.py --update")
        return 1

    if shrank:
        # Not a failure -- but say so, because a baseline that silently sits
        # above reality stops meaning anything, exactly as a stale
        # known_failures.txt entry does.
        print("\nThese improved; re-record the baseline in this commit:")
        for rule, (now, was) in sorted(shrank.items()):
            print(f"  {rule}: {was} -> {now}  (-{was - now})")
        print("\n  python3 scripts/ruff_gate.py --update")
        return 1

    print("\nNo new lint findings. (The baselined backlog above is still outstanding.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
