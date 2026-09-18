#!/usr/bin/env python3
"""Re-measure `tests/network_reach_baseline.txt` deliberately.

WHY THIS EXISTS AND WHY IT IS NOT THE PER-RUN GATE. `tests/conftest.py`
refuses every non-loopback connect the suite makes and fails, by name, any
test FILE that reaches the network and is not on the baseline. That is the
GROWTH direction and it is enforced on every run. The STALE direction -- a
listed file that no longer reaches anything -- is deliberately NOT enforced
there, because the set is order-dependent (driven in CI, 60 of the 74 tests
that reached a venue PASSED when re-run alone), so a per-run stale check
would churn and its churn would be a flake generator.

So the stale half is a DELIBERATE re-measure, taken by a human, from one full
run. That is this script. Without it the baseline can only ever grow or stay,
which makes it a permanent list rather than a ratchet: there would be no way
to verify that stubbing a file's venue seams had actually worked.

THREE OUTCOMES, NOT TWO, which is the discipline `ruff_gate.check_version` and
`scripts/verify_deploy.sh` already state here:

    0   the baseline is what the run measured
    1   it is not -- growth, stale, or both, each named
    3   COULD NOT CHECK -- pytest did not run to completion, so nothing was
        measured. Reporting an unmeasured run as "everything is stale" would
        delete real rows, which is the expensive direction.

`--write` refuses on 3 for that reason, and refuses a run it cannot prove
completed.

    python3 scripts/network_reach_gate.py                # run the suite, compare
    python3 scripts/network_reach_gate.py --write        # ... and rewrite the list
    python3 scripts/network_reach_gate.py --report P     # compare an existing report
"""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "network_reach_baseline.txt"
REPORT_ENV = "RUNECLAW_REACH_REPORT"

#: pytest's own vocabulary. 0 is a clean run and 1 is "tests failed", which is
#: the EXPECTED status here -- a file reaching the network fails its own test.
#: Everything else (2 interrupted, 3 internal error, 4 usage, 5 no tests
#: collected) means the suite did not run through, so nothing was measured.
_RAN_THROUGH = (0, 1)


def read_baseline(path: pathlib.Path = BASELINE):
    """The listed files, and the header lines, kept apart.

    The header is preserved verbatim on a rewrite: it carries the argument for
    why the backlog exists and what is and is not enforced, and a rewrite that
    dropped it would leave the next reader a bare list with no reason.
    """
    header, files = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            header.append(line)
        else:
            files.append(stripped)
    return header, sorted(set(files))


def read_report(path: pathlib.Path):
    """(files, collected, exitstatus, whole) from a report conftest wrote.

    `collected` is None when the report carries no count -- an older conftest,
    or a run that died before the session object had one. The caller treats
    that as "could not check" rather than as zero, because zero and unknown
    are what this whole repository is organised around keeping apart. `whole`
    is None for the same reason and is NOT read as False: an older conftest
    did not answer the question, which is not the same as answering "no".
    """
    files, collected, status, whole = [], None, None, None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("# collected="):
            raw = stripped.split("=", 1)[1]
            collected = int(raw) if raw.isdigit() else None
        elif stripped.startswith("# exitstatus="):
            raw = stripped.split("=", 1)[1]
            status = int(raw) if raw.lstrip("-").isdigit() else None
        elif stripped.startswith("# whole="):
            raw = stripped.split("=", 1)[1].strip().lower()
            whole = True if raw == "true" else (False if raw == "false" else None)
        elif stripped and not stripped.startswith("#"):
            files.append(stripped)
    return sorted(set(files)), collected, status, whole


def run_suite(report_path: pathlib.Path) -> int:
    """One full run, with the report path set. Returns pytest's exit status.

    The suite is run exactly as CI runs it. Nothing about the containment is
    switched off: the report is written beside the ordinary verdicts, so this
    measurement is of the same behaviour a normal run has.
    """
    env = dict(os.environ)
    env[REPORT_ENV] = str(report_path)
    print(f"[reach] running the full suite (this takes a while) -> "
          f"{report_path}", flush=True)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "-p", "no:randomly",
         "--no-header", "-p", "no:cacheprovider"],
        cwd=ROOT, env=env,
    )
    return proc.returncode


def write_baseline(header, files, path: pathlib.Path = BASELINE) -> None:
    path.write_text("\n".join(list(header) + list(files)) + "\n",
                    encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true",
                    help="rewrite the baseline's file list from the measurement")
    ap.add_argument("--report", type=pathlib.Path, default=None,
                    help="compare an existing report instead of running the suite")
    args = ap.parse_args(argv)

    if args.report is not None:
        if not args.report.exists():
            print(f"[reach] CANNOT CHECK: no report at {args.report}")
            return 3
        report_path, status = args.report, None
    else:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="reach-")) / "reached.txt"
        status = run_suite(tmp)
        if status not in _RAN_THROUGH:
            print(f"[reach] CANNOT CHECK: pytest exited {status}, so the suite "
                  f"did not run through and nothing was measured. This is NOT "
                  f"a finding about the baseline.")
            return 3
        if not tmp.exists():
            print(f"[reach] CANNOT CHECK: the suite ran but wrote no report. "
                  f"An older conftest does not know {REPORT_ENV}.")
            return 3
        report_path = tmp

    measured, collected, reported_status, whole = read_report(report_path)
    if collected is None:
        print(f"[reach] CANNOT CHECK: {report_path} carries no collected "
              f"count, so there is no evidence the whole suite was measured.")
        return 3
    if status is None and reported_status not in _RAN_THROUGH:
        print(f"[reach] CANNOT CHECK: that report was written by a run that "
              f"exited {reported_status}.")
        return 3

    header, listed = read_baseline()
    grew = [f for f in measured if f not in set(listed)]

    print(f"\n[reach] {collected} test(s) collected; {len(measured)} file(s) "
          f"reached the network; {len(listed)} listed in "
          f"{BASELINE.relative_to(ROOT)}")
    for f in grew:
        print(f"  GREW  {f}   (reaches a venue and is not listed -- the "
              f"per-run gate already fails this)")

    # THE STALE HALF NEEDS A WHOLE-SUITE MEASUREMENT AND THE GROWTH HALF DOES
    # NOT. A file that reached and is not listed is a finding from any run,
    # however narrow; a file that did not reach is only evidence when the run
    # could have reached it. Reading a partial run's silence as "stale" is the
    # absent-is-a-measurement shape, and here it would DELETE real rows.
    if whole is not True:
        why = ("the report does not say whether the whole tests tree was "
               "asked for (an older conftest)" if whole is None else
               "that run collected only part of the tests tree")
        print(f"  ..... STALE not checked: {why}.")
        if args.write:
            print(f"[reach] REFUSING --write: {why}, so every unreached row "
                  f"would be deleted on no evidence.")
            return 3
        if grew:
            print("[reach] growth found; the stale half was not measured.")
            return 1
        print("[reach] no growth; the stale half was not measured.")
        return 3

    stale = [f for f in listed if f not in set(measured)]
    for f in stale:
        print(f"  STALE {f}   (listed, reached nothing this run)")

    if args.write:
        write_baseline(header, measured)
        print(f"[reach] rewrote {BASELINE.relative_to(ROOT)}: "
              f"{len(listed)} -> {len(measured)} file(s)")
        return 0

    if grew or stale:
        print("[reach] the baseline is not what this run measured. "
              "Re-record with --write, in the same commit as the change that "
              "moved it.")
        return 1
    print("[reach] the baseline is exactly what this run measured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
