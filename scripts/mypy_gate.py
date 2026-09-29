#!/usr/bin/env python3
"""Type ratchet for the WHOLE bot/ tree, alongside the strict per-module gate.

TWO GATES, DELIBERATELY
-----------------------
CI already runs::

    mypy bot/risk bot/compliance bot/utils/trailing.py bot/core/bitget_v3_client.py
         bot/core/position_telemetry.py bot/core/live_executor.py

That one is a FLOOR: those modules are clean and must stay clean, so it fails on
a single new error. It is the right gate for the money path and nothing here
changes it.

What it cannot say anything about is the other 272 modules. ``mypy bot/``
reports 390 errors across 76 files, and until now nothing looked at that number
-- so a module could acquire fifty new type errors and no check would notice,
right up until someone widened the strict list and found them all at once.

This is the second gate: per-error-class counts over the whole tree, failing on
GROWTH. A rule may only go down. Same shape as ``ruff_gate.py``,
``cargo_audit_gate.py`` and the npm advisory ratchets, for the same reason --
a permanently red check is not a control, and a backlog nobody measures is not
a backlog, it is a surprise.

WHY THE 390 ARE NOT BEING "FIXED"
---------------------------------
They were sampled during the 2026-08-27 audit, taking the two classes most
likely to be real bugs -- ``operator`` (52) and ``union-attr`` (44), the ones
that read as ``None`` arithmetic. Every case examined was a mypy NARROWING
false positive, and the code was correct:

  ``formatters/rich_cards.py:903``   guarded by ``if _unread:`` on the branch
                                     above; mypy cannot correlate the flag with
                                     the value.
  ``formatters/rich_cards.py:1010``  guarded in the same expression by
                                     ``_dp_known``; narrowing does not survive
                                     an intermediate ``bool`` without TypeIs.
  ``learning/model_compare.py:98``   the list was filtered on
                                     ``final_paper_result is not None`` one line
                                     up; a comprehension filter does not narrow
                                     the element type.

Rewriting correct code to satisfy an analyser is how a real defect gets buried
in the diff of a hundred cosmetic ones. The honest move is to measure it, stop
it growing, and let each module be cleaned when someone is working in it -- at
which point it graduates to the strict list above, which is the direction that
already has a ratchet.

RECORD THE BASELINE IN A ``requirements-ci.txt`` ENVIRONMENT
-----------------------------------------------------------
The counts depend on the INSTALLED DEPENDENCY SET as well as on the analyser
version, and the first CI run of this gate proved both halves the hard way.

The baseline was first recorded with mypy 1.19.1 in a working environment that
had accumulated extra packages during an audit. CI pins 1.15.0 and installs
exactly ``requirements-ci.txt``, and reported 654 errors against a baseline of
390 -- eleven classes "grown", including a ``list-item`` class the other version
does not emit at all. Not one was a code change.

Chasing it down: pinning the version alone gave 485, not 654. ``--no-site-packages``
changed nothing. A cold cache changed nothing. A clean venv holding exactly
``requirements-ci.txt`` gave 654, matching CI to the error. The missing packages
were the rest of it -- with ``ignore_missing_imports = true`` an unresolvable
import becomes ``Any`` and its errors simply vanish, so a FULLER environment
reports MORE.

So::

    python3 -m venv /tmp/cienv
    /tmp/cienv/bin/pip install -r requirements-ci.txt
    PATH=/tmp/cienv/bin:$PATH python3 scripts/mypy_gate.py --update

``check_version`` below catches the version half and refuses to render a
verdict on it. It cannot see the dependency half, which is why this note
exists and why the growth message names the environment as a suspect.

The baseline stores no total: it is summed from the counts wherever it is
printed, and a baseline that still stores one is refused as CANNOT CHECK
(``scripts/ratchet_baseline.py`` says why).

USAGE
-----
    python3 scripts/mypy_gate.py             # gate (CI)
    python3 scripts/mypy_gate.py --update    # re-record, deliberately
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
BASELINE = ROOT / "tests" / "mypy_baseline.json"
TARGET = "bot/"

_CLASS = re.compile(r"\[([a-z-]+)\]\s*$")
_SUMMARY = re.compile(r"Found (\d+) errors? in (\d+) files?")



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

def current_counts() -> tuple[Counter, int]:
    proc = subprocess.run(["mypy", TARGET], capture_output=True, text=True, cwd=ROOT)
    if proc.returncode not in (0, 1):
        print(f"mypy failed to run (exit {proc.returncode}):\n"
              f"{proc.stdout[-2000:]}{proc.stderr[-2000:]}", file=sys.stderr)
        raise SystemExit(2)
    counts: Counter = Counter()
    files = 0
    for line in proc.stdout.splitlines():
        m = _CLASS.search(line)
        if m:
            counts[m.group(1)] += 1
        s = _SUMMARY.search(line)
        if s:
            files = int(s.group(2))
    return counts, files


def record(counts: Counter, files: int) -> None:
    """Write the baseline: the counts, and no total (it is always derived)."""
    BASELINE.write_text(json.dumps({
        "_comment": "Per-error-class mypy counts over the whole bot/ tree. "
                    "A RATCHET: a class may only go DOWN. This is NOT the "
                    "strict per-module gate in ci.yml, which fails on any "
                    "error at all for the money modules. The total is the sum "
                    "of the counts and is never stored. Regenerate with "
                    "scripts/mypy_gate.py --update (or scripts/rerecord.py "
                    "--all), and only alongside the commit that actually "
                    "lowered it.",
        "files": files,
        "counts": dict(sorted(counts.items())),
    }, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    check_version("mypy")

    if "--update" in sys.argv:
        counts, files = current_counts()
        record(counts, files)
        print(f"Baseline updated: {sum(counts.values())} errors in {files} "
              f"files, {len(counts)} classes")
        return 0

    if not BASELINE.exists():
        print(f"No {BASELINE}. Create it with: python3 scripts/mypy_gate.py --update",
              file=sys.stderr)
        return 2
    try:
        baseline = read_record(BASELINE)
        base_total = derived_total(baseline)
    except BaselineUnreadable as exc:
        # Exit 2, not 1: the baseline cannot be compared against, which says
        # nothing about whether the code grew. Checked before mypy runs, so a
        # refusal does not cost the whole-tree analysis first.
        print(f"CANNOT CHECK: {BASELINE.name}: {exc}", file=sys.stderr)
        return 2
    counts, files = current_counts()
    total = sum(counts.values())
    grew, shrank = compare(counts, baseline["counts"])

    print(f"mypy {TARGET}: {total} errors in {files} files, {len(counts)} classes")
    print(f"baseline:    {base_total} errors in "
          f"{baseline.get('files')} files")

    if grew:
        print("\nNEW type errors -- this gate fails on growth, not on the backlog:")
        for (cls,), was, now in grew:
            print(f"  {cls}: {was} -> {now}  (+{now - was})")
        print("\nBEFORE assuming these are real: mypy counts depend on the")
        print("INSTALLED DEPENDENCY SET, not just on the code. Confirm in a")
        print("clean venv holding exactly requirements-ci.txt -- a fuller or")
        print("emptier environment moves these numbers on identical source.")
        print("\nIf they are real, fix them; if the increase is deliberate,")
        print("re-record with  python3 scripts/mypy_gate.py --update")
        return 1

    if shrank:
        print("\nThese improved; re-record the baseline in this commit:")
        for (cls,), was, now in shrank:
            print(f"  {cls}: {was} -> {now}  (-{was - now})")
        print("\n  python3 scripts/mypy_gate.py --update")
        return 1

    print("\nNo new type errors. (The baselined backlog above is still outstanding.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
