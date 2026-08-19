#!/usr/bin/env python3
"""
CI test gate — baseline-diff regression guard.

The RUNECLAW test suite has a set of pre-existing failures that encode behavior
drift (tests asserting older behavior the code intentionally changed). Rather than
block all of CI on them or silently delete them, this gate runs the full suite and
fails ONLY when:

  * a NEW test fails that is not in tests/known_failures.txt, or
  * a baseline entry now PASSES (it must be trimmed — see below), or
  * a collection / internal error occurs.

Roadmap CI-hardening: a baseline test that starts passing is now a HARD failure,
not a warning. Previously the gate self-healed (warned but stayed green), which
let stale baseline entries hide real bugs (e.g. the dedup test that masked a
production bug). The baseline is for tests that consistently fail; the moment one
passes it must be removed, so CI forces the trim. Genuinely flaky tests do not
belong in the baseline — the per-node isolated re-run below filters those.

Usage:
    python scripts/ci_test_gate.py            # run suite + gate
    python scripts/ci_test_gate.py --update   # rewrite the baseline from this run
"""
from __future__ import annotations

import hashlib
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "known_failures.txt"

PYTEST_CMD = [
    sys.executable, "-m", "pytest",
    "-p", "no:cacheprovider",
    "--timeout=60", "--timeout-method=signal",
    "-rfE", "-q", "--no-header",
]

# Coverage floor on the money-moving modules. Measured ~70% at introduction;
# the floor is set below that so normal CI-env variance / the order-dependent
# flakes don't redden it, while still catching a real coverage regression.
# Ratchet this up as test isolation improves and scale_out.py gets tested.
# Coverage is collected on the gate's first full-suite run (pytest-cov), and the
# threshold is enforced separately via `coverage report` so the per-node flake
# re-runs (which use --no-cov) don't disturb the data.
COV_TARGETS = ["bot/risk", "bot/core/live_executor.py", "bot/compliance"]
COV_FAIL_UNDER = 60
COV_FLAGS = [f"--cov={t}" for t in COV_TARGETS] + ["--cov-report="]


def _load_baseline() -> set[str]:
    if not BASELINE.exists():
        return set()
    out: set[str] = set()
    for line in BASELINE.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


#: A pytest node id: a path to a .py file, optionally followed by ``::`` and
#: the test within it.
#:
#: `startswith("ERROR ")` ALONE MATCHED CAPTURED LOG RECORDS. The suite prints
#: what the code under test logs, and a logging formatter emits its level
#: first, so a plain log line
#:
#:     ERROR    bot.utils.website_sync:website_sync.py:139 Sync HTTP error 403
#:
#: was parsed as a failed test called
#: ``bot.utils.website_sync:website_sync.py:139``, which no test run can ever
#: contain. On 2026-08-19 two of them entered a FAIL verdict as phantom
#: failures, in the gate whose entire subject is not reporting one thing as
#: another. It normally hides: the flake filter re-runs each new failure alone,
#: pytest cannot collect a node that does not exist, and "could not run it" was
#: read as "it passed" — so the phantoms were quietly filed as flaky. With the
#: filter off (a tree that moved mid-run) they are reported as real.
#:
#: The shape is the discriminator. A node id starts with a path ending in
#: ``.py``; the log line's second field is a dotted logger name with a colon in
#: it, and cannot match.
_NODE_ID = re.compile(r"^[\w./\\-]+\.py(?:::|$)")


def _parse_failures(output: str) -> tuple[set[str], bool]:
    """Return (failed_node_ids, had_internal_error)."""
    failed: set[str] = set()
    internal_error = False
    for line in output.splitlines():
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            # split(None, 1) rather than split(" ", 1): a log formatter pads
            # its level column, so "ERROR    bot.utils..." has the payload
            # several spaces later and the old split handed back whitespace.
            parts = line.split(None, 1)
            node = parts[1].split(" - ", 1)[0].strip() if len(parts) > 1 else ""
            if node and _NODE_ID.match(node):
                failed.add(node)
        if "INTERNALERROR" in line:
            internal_error = True
    return failed, internal_error


def _rerun_verdict(returncode: int, node: str, node_failed: set[str]) -> str:
    """Where a re-run of one failing node lands: flaky / confirmed / unjudged.

    EXTRACTED SO IT CAN BE EXERCISED. This was three lines inline in main(),
    and the first test written for it reproduced the decision in the test file
    instead — which passed happily while the real branch was mutated back to
    the broken version. A test of a copy of the code is not a test of the code.

    Only exit code 0 means the re-run ran and passed. A node pytest could not
    collect prints no FAILED line, so `node not in node_failed` alone read
    "could not run it" as "it passed" — the behaviour that quietly absolved
    every phantom node id parsed out of a log record, and so hid that parse bug
    for as long as the flake filter was on.
    """
    if returncode == 0 and node not in node_failed:
        return "flaky"
    if node in node_failed:
        return "confirmed"
    return "unjudged"


def _coverage_below_floor() -> bool:
    """Return True if coverage on COV_TARGETS is below COV_FAIL_UNDER.

    Best-effort: if pytest-cov / coverage isn't installed (e.g. a minimal local
    run), this is skipped (returns False) rather than failing the gate.
    """
    try:
        r = subprocess.run(
            [sys.executable, "-m", "coverage", "report", f"--fail-under={COV_FAIL_UNDER}"],
            cwd=ROOT, capture_output=True, text=True,
        )
    except Exception:
        return False
    print(r.stdout + r.stderr)
    # coverage exits 2 when below --fail-under; 0 when OK; 1 on no-data/other.
    if r.returncode == 0:
        return False
    if r.returncode == 2:
        print(f"[gate] FAIL — coverage on {COV_TARGETS} is below {COV_FAIL_UNDER}%.")
        return True
    # No data / coverage not available — skip, don't block.
    return False


def _tree_fingerprint() -> str:
    """A cheap fingerprint of every source file the suite reads.

    Compared before and after the run so the flake filter can tell "this test
    is order-dependent" from "somebody edited the code while I was running".

    mtime+size rather than content hashing: there are thousands of files and
    this runs on the critical path of every preflight. The failure mode of
    mtime (an edit that preserves both) is not reachable by a human editor or
    by git, and the cost of hashing contents is a measurable slice of a 15
    minute gate.
    """
    h = hashlib.sha256()
    for root in ("bot", "tests", "scripts"):
        base = ROOT / root
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.py")):
            try:
                st = f.stat()
            except OSError:
                continue
            h.update(f"{f.relative_to(ROOT)}:{st.st_mtime_ns}:{st.st_size}\n".encode())
    return h.hexdigest()


def main() -> int:
    update = "--update" in sys.argv
    cov_available = False
    try:
        import pytest_cov  # noqa: F401
        cov_available = True
    except Exception:
        cov_available = False
    first_cmd = PYTEST_CMD + (COV_FLAGS if cov_available else [])
    fingerprint_before = _tree_fingerprint()
    proc = subprocess.run(first_cmd, cwd=ROOT, capture_output=True, text=True)
    output = proc.stdout + proc.stderr
    print(output)

    failed, internal_error = _parse_failures(output)

    # THE VERDICT USED TO IGNORE pytest's OWN EXIT CODE.
    #
    # Everything below reads stdout. A run that never got as far as printing
    # `FAILED `/`ERROR ` lines therefore parsed as zero failures, and the gate
    # announced "PASS — no new failures beyond the known baseline" having
    # executed nothing. Driven, not reasoned: pytest with an unknown flag exits
    # 4, collects zero tests, and this gate returned 0.
    #
    # That is the defect this repository is built around — a subset (here, the
    # empty set) reported as the whole — sitting in the gate that exists to
    # prevent it, and standing behind every "preflight green" anyone has ever
    # quoted.
    #
    # 0 = all passed, 1 = some tests failed. Those two are the only codes that
    # mean "the suite ran and stdout describes it". Everything else — 2
    # interrupted, 3 internal error, 4 usage/conftest error, 5 nothing
    # collected — means the parse below is describing a run that did not
    # happen.
    if proc.returncode not in (0, 1):
        print(f"[gate] FAIL — pytest exited {proc.returncode}: the suite did not "
              "run to completion, so the failure list below describes nothing. "
              "(2=interrupted, 3=internal, 4=usage/conftest, 5=no tests collected)")
        print("=" * 70)
        return 1

    # rc=1 with nothing parsed is the same hazard wearing a different hat:
    # pytest says tests failed and this parser cannot see them, so its silence
    # is ignorance rather than evidence.
    if proc.returncode == 1 and not failed and not internal_error:
        print("[gate] FAIL — pytest exited 1 but no FAILED/ERROR lines were "
              "parsed. The gate cannot describe this run; refusing to call it "
              "green.")
        print("=" * 70)
        return 1

    if update:
        header = (
            "# Known pre-existing test failures (behavior drift) — baseline for the\n"
            "# CI gate (scripts/ci_test_gate.py). NEW failures outside this list fail CI.\n"
            "# Regenerate with: python scripts/ci_test_gate.py --update\n"
        )
        BASELINE.write_text(header + "\n".join(sorted(failed)) + "\n")
        print(f"\n[gate] baseline updated: {len(failed)} known failures written to {BASELINE}")
        return 0

    known = _load_baseline()
    new_failures = sorted(failed - known)
    now_passing = sorted(known - failed)

    # Flake filter: some suites are mildly flaky (time-based, e.g.
    # ProactiveMonitor) or order-sensitive (tests in test_core that pollute each
    # other's in-process state — they pass alone but fail in sequence). Before
    # failing the build on a NEW failure, re-run each node IN ITS OWN process
    # (isolated). A node that passes alone is a flake / order-dependence artifact
    # and is dropped (reported, not fatal); only nodes that still fail in
    # isolation count as real regressions. Trade-off: an order-dependent *real*
    # regression (test A breaks test B) is not caught here, but that is rare and
    # far less disruptive than flakes reddening every run.
    # THE FLAKE FILTER RE-READS THE TREE FROM DISK, so an edit landing mid-run
    # turns a REAL failure into a phantom flake: the first pass fails against
    # the old source, the isolated re-run passes against the new, and the gate
    # concludes "order-dependent" and drops it.
    #
    # Observed 2026-08-17. A source-scanning test failed legitimately, the fix
    # was written while the ~15 minute suite was still running, and this gate
    # reported "PASS — no new failures" for a run that contained a genuine
    # regression. CI, whose checkout is immutable for the duration, failed the
    # same commit — and the local run was the one that looked trustworthy.
    #
    # "Passes alone" is only evidence about flakiness if BOTH runs saw the same
    # code. When they did not, nothing was established, and this gate already
    # carries the same lesson one layer down: it used to ignore pytest's exit
    # code and announce PASS having executed nothing. An unreadable result is
    # not a passing one.
    tree_changed = _tree_fingerprint() != fingerprint_before

    flaky: list[str] = []
    if new_failures and tree_changed:
        print("\n" + "=" * 70)
        print("[gate] SOURCE CHANGED DURING THE RUN — flake filter DISABLED.")
        print("  A file under bot/, tests/ or scripts/ was modified while the")
        print("  suite was running, so an isolated re-run would be testing")
        print("  DIFFERENT code and 'passes alone' would prove nothing. Every")
        print("  new failure below is reported as real. Re-run the gate on a")
        print("  quiescent tree to get a verdict you can trust.")
        print("=" * 70)
    elif new_failures:
        confirmed: list[str] = []
        print("\n----- re-running new failures individually (flake filter) -----")
        for node in new_failures:
            # --no-cov on re-runs so they don't overwrite the full-suite coverage.
            r = subprocess.run(PYTEST_CMD + (["--no-cov"] if cov_available else []) + [node],
                               cwd=ROOT, capture_output=True, text=True)
            node_failed, node_internal = _parse_failures(r.stdout + r.stderr)
            internal_error = internal_error or node_internal
            # THE RE-RUN HAS TO BE READABLE BEFORE ITS RESULT COUNTS.
            #
            # This was `if node in node_failed: confirmed else: flaky`, so a
            # node pytest COULD NOT COLLECT — a bad id, a renamed test, a
            # deleted one — printed no FAILED line, missed the set, and was
            # filed as "passes alone (flaky/order-dependent)". Absent read as
            # a pass, in the gate that exists to stop exactly that, and it is
            # what hid the phantom node ids parsed out of log records above:
            # they could never be collected, so they were always absolved.
            #
            # Only an exit code of 0 means it ran and passed. Anything else is
            # not a verdict, and a failure that cannot be judged stays a
            # failure.
            verdict = _rerun_verdict(r.returncode, node, node_failed)
            if verdict == "flaky":
                flaky.append(node)
                print(f"  ~ passes alone (flaky/order-dependent): {node}")
            elif verdict == "confirmed":
                confirmed.append(node)
                print(f"  ✗ still fails alone: {node}")
            else:
                confirmed.append(node)
                print(f"  ? could not be re-run alone (pytest exit "
                      f"{r.returncode}) — counted as failing: {node}")
        flaky = sorted(flaky)
        new_failures = sorted(confirmed)

    print("\n" + "=" * 70)
    print(f"[gate] total failing: {len(failed)} | known-baseline: {len(known)}")
    if now_passing:
        print(f"[gate] FAIL — {len(now_passing)} baseline test(s) now PASS — trim them "
              f"from tests/known_failures.txt:")
        for n in now_passing:
            print(f"         + {n}")
    if flaky:
        print(f"[gate] {len(flaky)} flaky test(s) failed then passed on re-run (ignored):")
        for n in flaky:
            print(f"         ~ {n}")
    if internal_error:
        print("[gate] FAIL — pytest reported an INTERNALERROR (collection/runtime).")
    if new_failures:
        print(f"[gate] FAIL — {len(new_failures)} NEW failure(s) not in the baseline:")
        for n in new_failures:
            print(f"         ✗ {n}")
    # Coverage floor is only meaningful when the suite itself is healthy.
    cov_failed = False
    if cov_available and not (new_failures or internal_error):
        cov_failed = _coverage_below_floor()

    if not new_failures and not internal_error and not now_passing and not cov_failed:
        print("[gate] PASS — no new failures beyond the known baseline.")
    print("=" * 70)

    return 1 if (new_failures or internal_error or now_passing or cov_failed) else 0


if __name__ == "__main__":
    sys.exit(main())
