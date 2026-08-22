#!/usr/bin/env python3
"""Attack the custody boundary, and fail if anything gets through.

`scripts/red_team.py` attacks the RiskEngine with adversarial trade IDEAS —
can a bad setup get sized and sent. This attacks the layer that decides
whether funds can move at all: `bot/guardian/authority.py`'s `authorize`,
with adversarial ACTIONS. Withdrawals to an attacker address, transfers under
a default-deny envelope, orders over the per-trade cap, a last leg that drains
the daily budget, an off-venue or off-market-type order, a symbol outside the
allowlist, replay of an expired authority, replay of a revoked one, an unknown
action kind, and a prompt injection talking the compiler into a bigger cap.
Every one must be DENIED — and one in-bounds control action must be ALLOWED,
so a gate that simply refuses everything cannot pass.

    `bot/guardian/authority_redteam.py` WAS 213 LINES THAT NOTHING COULD RUN.

Reachable only from its own tests, exactly as `bot/core/red_team.py` was until
`scripts/red_team.py` was written. `tests/unreachable_baseline.txt` records it
under "safety controls, uncalled", and the entry above it explains what
changed for its sibling:

    red_team LEFT this list: scripts/red_team.py runs it and CI gates on it,
    so the 30 adversarial scenarios now attack the risk engine on every change
    instead of being 691 lines nothing could reach.

This is the same runner for the custody half. `authorize` is not hypothetical
code — `bot/risk/risk_engine.py` and `bot/web/user_gateway.py` both call it on
the live path — so what was untested was the gate that stands between an agent
and someone's funds.

    scripts/authority_red_team.py            # human summary, exit 1 on failure
    scripts/authority_red_team.py --json     # machine output for CI

IT IS 12/12 TODAY, on the first run it was ever given. That makes this a
REGRESSION GUARD, not a bug report — the same thing its sibling was on day one.

EXIT CODE IS THE POINT. 0 means every custody attack was refused and the
control action was allowed. 1 means one got through, and the scenario that did
is named. A run that cannot complete is also 1: a red team that errors has not
cleared the gate, and printing "no failures" because the harness broke is the
confident all-clear this repo spends its guard tests preventing.

No temp state file is needed here, unlike the engine red team — the harness is
pure. It builds envelopes and actions, calls `authorize`, and touches no
network, no clock and no operator state.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(verbose: bool = False) -> dict:
    """Run every custody scenario against the REAL authorize(). Returns the report."""
    # Imported here, not at module scope, so the quiet path suppresses
    # import-time chatter too — `bot.config` narrates the secrets vault as it
    # loads, which is useful to an operator and noise in a gate.
    if verbose:
        from bot.guardian.authority_redteam import run_authority_redteam
        return run_authority_redteam()

    logging.disable(logging.CRITICAL)
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            from bot.guardian.authority_redteam import run_authority_redteam
            return run_authority_redteam()
    finally:
        logging.disable(logging.NOTSET)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--verbose", action="store_true",
                    help="show import-time and audit chatter during the run")
    args = ap.parse_args()

    try:
        report = run(verbose=args.verbose)
    except Exception as exc:                                       # noqa: BLE001
        # A harness that could not run has NOT cleared the gate. Reporting
        # "0 failures" here would be an all-clear manufactured from no data.
        if args.json:
            print(json.dumps({"error": str(exc), "ran": False}))
        else:
            print(f"✗ authority red team could not run: {exc}", file=sys.stderr)
            print("  This is NOT a pass. The custody gate was never attacked.",
                  file=sys.stderr)
        return 1

    total = report.get("total", 0)
    if not total:
        # Zero scenarios would report 100% and exit 0 — a perfect score over an
        # empty set. The same trap as `integrity_veto.assess({}) == "clear"`.
        print("✗ authority red team ran no scenarios — nothing was attacked.",
              file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report))
        return 1 if report.get("failed") else 0

    by_cat: dict[str, list] = {}
    for s in report["scenarios"]:
        by_cat.setdefault(s["category"], []).append(s)

    print(f"AUTHORITY RED TEAM — {total} custody attacks against the live "
          f"authorize()\n")
    for cat in sorted(by_cat):
        rows = by_cat[cat]
        bad = [r for r in rows if not r["passed"]]
        mark = "✓" if not bad else "✗"
        print(f"  {mark} {cat:<20} {len(rows) - len(bad)}/{len(rows)}")
        for r in bad:
            print(f"      GOT THROUGH: {r['name']}")
            print(f"        expected {r['expected']}, got {r['actual']}")
            print(f"        {r['description']}")

    print()
    if report["failed"]:
        print(f"✗ {report['failed']} of {total} custody scenarios were handled "
              f"wrongly ({report['pass_rate']}% correct)")
        return 1
    print(f"✓ all {total} custody attacks were denied, and the in-bounds "
          "control action was allowed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
