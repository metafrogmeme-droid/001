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

The baseline stores no total: it is summed from the counts wherever it is
printed, and a baseline that still stores one is refused as CANNOT CHECK
(``scripts/ratchet_baseline.py`` says why).

USAGE
-----
    python3 scripts/ruff_gate.py             # gate (CI)
    python3 scripts/ruff_gate.py --update    # re-record, deliberately
    python3 scripts/rerecord.py --all        # every ratchet at once
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import toolchain
from ratchet_baseline import BaselineUnreadable, compare, derived_total, read_record

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "ruff_baseline.json"

_CODE = re.compile(r"^[^:]+:\d+:\d+:\s+([A-Z]+[0-9]+)\s")

# Ruff 0.11.13 paints concise lines even with NO_COLOR set, and `ruff check`
# rejects `--color`. Colour codes sit inside `path:line:col: CODE`, so a raw
# `_CODE` match counted zero findings on a tree that had them.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def check_version(tool: str) -> None:
    """Refuse to compare counts produced by a different analyser.

    The body moved to `scripts/toolchain.py`. `ruff_gate` and `mypy_gate` each
    carried a byte-identical copy of this and of the two version readings under
    it, and `preflight.py` needed the same reading a third time — to say up
    front that the ratchets cannot check on this box rather than leaving each
    gate to discover it one at a time. A second copy of a reading is a second
    answer. The name stays because other prose in this tree points at it.
    """
    toolchain.require_comparable(tool)


def counts_from_output(text: str) -> Counter:
    """Count rule codes in concise ruff output.

    A colored ``path:line:col: CODE`` never matches ``_CODE``, so the gate
    reads a real tree as zero findings and demands a baseline update for a
    parser bug. Strip the colour first.
    """
    counts: Counter = Counter()
    for line in text.splitlines():
        m = _CODE.match(_ANSI.sub("", line))
        if m:
            counts[m.group(1)] += 1
    return counts


def current_counts() -> Counter:
    """Per-rule counts from the DECLARED config -- no --select override.

    A ruff that cannot be started at all (not on PATH) is CANNOT CHECK, exit 2.
    It used to escape as a ``FileNotFoundError`` traceback, exit 1, which is
    this gate's "the code grew" -- and ``check_version`` does not stop it
    first, because a version it cannot read is only a warning there.
    """
    try:
        proc = subprocess.run(
            ["ruff", "check", ".", "--output-format=concise", "--no-fix"],
            capture_output=True, text=True, cwd=ROOT)
    except OSError as exc:
        print(f"CANNOT CHECK: ruff could not be run ({type(exc).__name__}); "
              "no count was read, so this says nothing about whether the code "
              "grew. Put the pinned ruff on PATH.", file=sys.stderr)
        raise SystemExit(2) from None
    # ruff exits 1 when it finds anything, which is the normal case here.
    if proc.returncode not in (0, 1):
        print(f"ruff failed to run (exit {proc.returncode}):\n{proc.stderr}",
              file=sys.stderr)
        raise SystemExit(2)
    return counts_from_output(proc.stdout)


def _load_baseline() -> dict:
    if not BASELINE.exists():
        print(f"No {BASELINE}. Create it with: python3 scripts/ruff_gate.py --update",
              file=sys.stderr)
        raise SystemExit(2)
    try:
        return read_record(BASELINE)
    except BaselineUnreadable as exc:
        print(f"CANNOT CHECK: {BASELINE.name}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


def record(counts: Counter) -> None:
    """Write the baseline: the counts, and no total (it is always derived)."""
    BASELINE.write_text(json.dumps({
        "_comment": "Per-rule ruff counts under the config declared in "
                    "pyproject.toml. A RATCHET: a rule may only go DOWN. The "
                    "total is the sum of the counts and is never stored. "
                    "Regenerate with scripts/ruff_gate.py --update (or "
                    "scripts/rerecord.py --all), and only alongside the "
                    "commit that actually lowered it.",
        "counts": dict(sorted(counts.items())),
    }, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    check_version("ruff")

    if "--update" in sys.argv:
        counts = current_counts()
        record(counts)
        print(f"Baseline updated: {sum(counts.values())} findings across "
              f"{len(counts)} rules")
        return 0

    baseline = _load_baseline()
    try:
        base_total = derived_total(baseline)
    except BaselineUnreadable as exc:
        # Exit 2, not 1: the baseline cannot be compared against, which says
        # nothing about whether the code grew. Checked before ruff runs.
        print(f"CANNOT CHECK: {BASELINE.name}: {exc}", file=sys.stderr)
        return 2
    counts = current_counts()
    total = sum(counts.values())
    grew, shrank = compare(counts, baseline["counts"])

    print(f"ruff (declared config): {total} findings across {len(counts)} rules")
    print(f"baseline:               {base_total} findings")

    if grew:
        print("\nNEW lint findings -- this gate fails on growth, not on the backlog:")
        for (rule,), was, now in grew:
            print(f"  {rule}: {was} -> {now}  (+{now - was})")
        print("\nFix them, or if the increase is deliberate, re-record with")
        print("  python3 scripts/ruff_gate.py --update")
        return 1

    if shrank:
        # Not a failure -- but say so, because a baseline that silently sits
        # above reality stops meaning anything, exactly as a stale
        # known_failures.txt entry does.
        print("\nThese improved; re-record the baseline in this commit:")
        for (rule,), was, now in shrank:
            print(f"  {rule}: {was} -> {now}  (-{was - now})")
        print("\n  python3 scripts/ruff_gate.py --update")
        return 1

    print("\nNo new lint findings. (The baselined backlog above is still outstanding.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
