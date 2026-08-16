#!/usr/bin/env python3
"""Attack the risk engine, and fail if anything gets through.

`bot/core/red_team.py` builds adversarial TradeIdeas designed to slip past the
risk engine — flash crashes, liquidity drains, correlated selloffs, stale data,
confidence manipulation, risk/reward gaming, circuit-breaker evasion, zero and
negative values, direction inversion, position flooding, malformed input — runs
each through the REAL engine, and reports what was caught.

    IT WAS 691 LINES THAT NOTHING COULD RUN.

No script, no command, no CI job, no import outside its own tests: reachable
only by `tests/test_core.py`, which asserts the report has the right FIELDS.
So the risk engine — the thing the whole product's safety claim rests on — had
never actually been attacked outside a unit test asserting a schema.

This is the runner. It exists so that "the risk gate refuses anything out of
bounds" is a checked claim rather than a stated one, on every change.

    scripts/red_team.py            # human summary, exit 1 on any failure
    scripts/red_team.py --json     # machine output for CI
    scripts/red_team.py --verbose  # show the engine's audit log too

EXIT CODE IS THE POINT. 0 means every adversarial scenario was refused; 1 means
one got through, and the name of the scenario that did is printed. A run that
cannot complete is also 1 — a red team that errors has not cleared the engine,
and reporting "no failures" because the harness broke is the exact defect this
repo spends its guard tests preventing.

THE ENGINE IS GIVEN ITS OWN STATE FILE, in a temp dir, deleted afterwards.
Pointing it at the operator's real state would let a stress test's synthetic
losses and tripped breakers land in production state.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _attack(state: str):
    """Import, build a clean engine, and run every scenario."""
    # Imported HERE rather than at module scope so the quiet path can suppress
    # import-time chatter too — `bot.config` reports on the secrets vault as it
    # loads, which is useful to an operator and noise in a gate.
    from bot.core.red_team import RedTeamEngine
    from bot.risk.portfolio import PortfolioTracker
    from bot.risk.risk_engine import RiskEngine

    portfolio = PortfolioTracker()
    engine = RiskEngine(portfolio, state_file=state)
    return RedTeamEngine(engine, portfolio).run_stress_test()


def run(verbose: bool = False):
    """Run every scenario. Returns the StressTestReport."""
    tmp = tempfile.mkdtemp(prefix="redteam-")
    state = os.path.join(tmp, "state.json")
    try:
        # The engine narrates every check as structured audit JSON. Correct for
        # production and useless as a test report — thirty scenarios bury the
        # verdict in several hundred lines. Silenced unless asked for.
        if verbose:
            return _attack(state)

        logging.disable(logging.CRITICAL)
        sink = io.StringIO()
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                return _attack(state)
        finally:
            logging.disable(logging.NOTSET)
    finally:
        with contextlib.suppress(OSError):
            os.remove(state)
        with contextlib.suppress(OSError):
            os.rmdir(tmp)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--verbose", action="store_true",
                    help="show the engine's audit log during the run")
    args = ap.parse_args()

    try:
        report = run(verbose=args.verbose)
    except Exception as exc:                                       # noqa: BLE001
        # A harness that could not run has NOT cleared the engine. Saying
        # "0 failures" here would be a confident all-clear manufactured from
        # no data — the `integrity_veto.assess({}) == "clear"` trap, one level
        # over.
        if args.json:
            print(json.dumps({"error": str(exc), "ran": False}))
        else:
            print(f"✗ red team could not run: {exc}", file=sys.stderr)
            print("  This is NOT a pass. The engine was never attacked.",
                  file=sys.stderr)
        return 1

    if args.json:
        print(report.model_dump_json())
        return 1 if report.failed else 0

    by_cat: dict[str, list] = {}
    for s in report.scenarios:
        by_cat.setdefault(s.category, []).append(s)

    print(f"RED TEAM — {report.total_scenarios} adversarial scenarios "
          f"against the live risk engine\n")
    for cat in sorted(by_cat):
        rows = by_cat[cat]
        bad = [r for r in rows if not r.passed]
        mark = "✓" if not bad else "✗"
        print(f"  {mark} {cat:<28} {len(rows) - len(bad)}/{len(rows)}")
        for r in bad:
            print(f"      SLIPPED THROUGH: {r.name}")
            print(f"        expected {r.expected_verdict}, got {r.actual_verdict}")

    print()
    if report.failed:
        print(f"✗ {report.failed} of {report.total_scenarios} got past the risk "
              f"engine ({report.pass_rate}% caught)")
        return 1
    print(f"✓ all {report.total_scenarios} adversarial scenarios were refused")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
