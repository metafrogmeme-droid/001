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

USAGE
-----
    python3 scripts/mypy_gate.py             # gate (CI)
    python3 scripts/mypy_gate.py --update    # re-record, deliberately
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "mypy_baseline.json"
TARGET = "bot/"

_CLASS = re.compile(r"\[([a-z-]+)\]\s*$")
_SUMMARY = re.compile(r"Found (\d+) errors? in (\d+) files?")


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


def main() -> int:
    counts, files = current_counts()
    total = sum(counts.values())

    if "--update" in sys.argv:
        BASELINE.write_text(json.dumps({
            "_comment": "Per-error-class mypy counts over the whole bot/ tree. "
                        "A RATCHET: a class may only go DOWN. This is NOT the "
                        "strict per-module gate in ci.yml, which fails on any "
                        "error at all for the money modules. Regenerate with "
                        "scripts/mypy_gate.py --update, and only alongside the "
                        "commit that actually lowered it.",
            "total": total,
            "files": files,
            "counts": dict(sorted(counts.items())),
        }, indent=2) + "\n", encoding="utf-8")
        print(f"Baseline updated: {total} errors in {files} files, "
              f"{len(counts)} classes")
        return 0

    if not BASELINE.exists():
        print(f"No {BASELINE}. Create it with: python3 scripts/mypy_gate.py --update",
              file=sys.stderr)
        return 2
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    base_counts = baseline.get("counts", {})

    grew = {c: (n, base_counts.get(c, 0))
            for c, n in counts.items() if n > base_counts.get(c, 0)}
    shrank = {c: (n, base_counts[c])
              for c, n in ((c, counts.get(c, 0)) for c in base_counts)
              if n < base_counts[c]}

    print(f"mypy {TARGET}: {total} errors in {files} files, {len(counts)} classes")
    print(f"baseline:    {baseline.get('total')} errors in "
          f"{baseline.get('files')} files")

    if grew:
        print("\nNEW type errors -- this gate fails on growth, not on the backlog:")
        for cls, (now, was) in sorted(grew.items()):
            print(f"  {cls}: {was} -> {now}  (+{now - was})")
        print("\nFix them, or if the increase is deliberate, re-record with")
        print("  python3 scripts/mypy_gate.py --update")
        return 1

    if shrank:
        print("\nThese improved; re-record the baseline in this commit:")
        for cls, (now, was) in sorted(shrank.items()):
            print(f"  {cls}: {was} -> {now}  (-{was - now})")
        print("\n  python3 scripts/mypy_gate.py --update")
        return 1

    print("\nNo new type errors. (The baselined backlog above is still outstanding.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
