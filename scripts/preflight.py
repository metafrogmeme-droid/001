#!/usr/bin/env python3
"""Run locally what CI runs, by READING what CI runs.

Written after pushing a change that passed pytest and guard_lint and then
failed CI on `ruff F541`. The interesting part was not the stray f-prefix; it
was that "my checks pass" had been standing in for "CI will pass" while
covering a fraction of it. The baseline gate runs six commands. I was running
none of them — a raw pytest that approximates the last one, plus guard_lint
from a different job.

That is the same defect as most of what this codebase was fixing that week: a
check whose coverage is narrower than the confidence placed in it.

So this does not RESTATE the commands — restating them is how the copy drifts
from the original, silently, exactly once, at the least convenient moment. It
parses .github/workflows/ci.yml and runs what is written there. A new CI step
is a new preflight step for free, and there is no second list to forget.

Usage:
    python3 scripts/preflight.py              # everything, in CI order
    python3 scripts/preflight.py --fast       # skip the slow/network gates
    python3 scripts/preflight.py --list       # show what would run
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - trivially environment-dependent
    print("preflight: PyYAML is required (pip install pyyaml)", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

#: Jobs this can genuinely run on a dev box. Everything else is named in the
#: summary rather than silently omitted — "all gates green" while quietly
#: covering half of them is the exact defect this script exists to prevent,
#: and the first version of it printed precisely that.
#:
#: Token tooling is excluded on purpose despite being node: one of its steps
#: curl-pipes a Solana validator installer, and a preflight that installs a
#: toolchain behind your back is not a preflight.
LOCAL_JOBS = ("Lint + tests (baseline gate)", "Web app (express)")
PYTHON_JOBS = LOCAL_JOBS          # back-compat alias

#: Steps to skip: setup, and anything whose name matches. Substring match on
#: the step's `name`, case-insensitive.
ALWAYS_SKIP = ("install", "checkout", "set up", "setup", "cache")

#: Skipped by --fast. Both reach the network and neither can fail from an edit
#: you just made, so they are the right things to drop from a tight loop —
#: and the wrong things to drop before pushing.
FAST_SKIP = ("pip-audit", "sca —", "sca -")

#: Steps run by other jobs that still work here. Kept explicit because they
#: are the exception, not the rule. (name, command, working directory)
EXTRA = [("Guard reachability (structural)", "python3 scripts/guard_lint.py", ".")]


def _flatten(cmd: str) -> str:
    """DISPLAY ONLY. Never execute the flattened form.

    The original docstring claimed folded scalars "arrive with newlines; CI
    runs it as one line" — wrong on both counts. YAML folds `>` scalars into
    spaces AT PARSE, so by the time this sees the string there is nothing
    left to flatten; and a literal `|` block is a multi-line SCRIPT whose
    newlines are semantic (if/fi, loops). Space-joining one produces a
    command that cannot run — which surfaced the moment the web-app test
    step grew an if-block. Execution uses the raw string; the shell handles
    newlines exactly as the CI runner does."""
    return " ".join(cmd.split())


def steps(fast: bool) -> list[tuple[str, str, str]]:
    """(display name, command, working directory) in CI order."""
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    out: list[tuple[str, str, str]] = []
    for job in wf.get("jobs", {}).values():
        if job.get("name") not in LOCAL_JOBS:
            continue
        for step in job.get("steps", []):
            run = step.get("run")
            name = step.get("name") or "(unnamed)"
            if not run:
                continue
            low = name.lower()
            if any(s in low for s in ALWAYS_SKIP):
                continue
            if fast and any(s in low for s in FAST_SKIP):
                continue
            out.append((f"{job['name']} — {name}", run,
                        step.get("working-directory") or "."))
    out.extend(EXTRA)
    return out


def uncovered() -> list[str]:
    """CI jobs this cannot run. Named in the summary, never left implied."""
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return sorted(j.get("name") for j in wf.get("jobs", {}).values()
                  if j.get("name") not in LOCAL_JOBS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fast", action="store_true",
                    help="skip the network-dependent gates (not before pushing)")
    ap.add_argument("--list", action="store_true", help="print the plan and exit")
    args = ap.parse_args()

    plan = steps(args.fast)
    if not plan:
        # An empty plan would exit 0 and read as success. It means the workflow
        # was renamed or restructured, which is a failure to report, not pass.
        print("preflight: no steps found — has ci.yml changed shape?", file=sys.stderr)
        return 2

    if args.list:
        for name, cmd, wd in plan:
            print(f"{name}\n    ({wd}) {_flatten(cmd)}\n")
        print("NOT covered locally: " + ", ".join(uncovered()))
        return 0

    results: list[tuple[str, bool, float, bool]] = []
    SHELL_BUILTINS = ("set", "if", "for", "while", "cd", "export", "true")
    for name, cmd, wd in plan:
        tool = cmd.split()[0]
        # A multi-line `run: |` script's first token is usually a shell
        # builtin (`set -e`…), which which() cannot find — that must not
        # read as "tool not installed". The script's own commands fail
        # loudly under the shell if genuinely missing.
        if tool in SHELL_BUILTINS or "\n" in cmd.strip():
            pass
        elif shutil.which(tool) is None and tool not in ("python", "python3"):
            # Absent is not passing. Say so and keep going, so one missing
            # tool does not hide the state of everything after it.
            print(f"\n\033[33m— SKIP\033[0m {name}\n  {tool!r} is not installed")
            results.append((name, False, 0.0, True))
            continue
        print(f"\n\033[36m▶ {name}\033[0m\n  $ ({wd}) {_flatten(cmd)}")
        t0 = time.monotonic()
        # bash, not /bin/sh: the GitHub runner executes `run:` blocks with
        # bash, and CI steps legitimately use bashisms (PIPESTATUS). Running
        # them under dash would fail a step CI passes — the exact class of
        # false signal this script exists to remove.
        _env = {**os.environ, "PATH": "/home/mulerun/bin:" + os.environ.get("PATH", "")}
        rc = subprocess.call(cmd, shell=True, cwd=ROOT / wd,
                             executable="/bin/bash", env=_env)
        results.append((name, rc == 0, time.monotonic() - t0, False))

    print("\n" + "─" * 68)
    failed = [r for r in results if not r[1] and not r[3]]
    skipped = [r for r in results if r[3]]
    for name, ok, secs, skip in results:
        mark = "\033[33m?\033[0m" if skip else ("\033[32m✓\033[0m" if ok else "\033[31m✗\033[0m")
        print(f"{mark} {name}  ({secs:.1f}s)")
    if args.fast:
        print("\n\033[33m--fast omitted the network gates; run without it before pushing.\033[0m")
    if skipped:
        print(f"\n\033[33m{len(skipped)} gate(s) could not run — that is not a pass.\033[0m")
    if failed:
        print(f"\n\033[31m{len(failed)} gate(s) failed.\033[0m")
        return 1
    if skipped:
        return 2
    # NOT "this is what CI will run". It is what CI will run MINUS the jobs
    # below, and a summary that rounds that up to "everything" is the same
    # overclaim this script was written to stop. Name them every time.
    print("\n\033[32mAll local gates green.\033[0m")
    print("\033[33mStill only CI can judge: " + ", ".join(uncovered()) + "\033[0m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
