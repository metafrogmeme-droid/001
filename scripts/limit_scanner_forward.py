#!/usr/bin/env python3
"""Score a Limit Entry Scanner forward test, with funding priced from files.

usage:
    python3 scripts/limit_scanner_forward.py --bars DIR --funding DIR [--test reclaim|original]

``--bars`` is a dataset in the frozen book's format (``manifest.json`` and one
``csv.gz`` of closed 1h bars per symbol) reaching from enough bars before the
cutoff to the present. ``--funding`` holds one JSON file per bar file stem, rows
``{"ms": ..., "rate": ...}`` as Bitget's public history-fund-rate reports them
(no key). Prints one JSON object. Paper only: nothing here places, signs or
reads an account.

``reclaim`` is the test registered 2026-11-12
(`docs/research/prereg/limit_scanner_reclaim_forward/PREREGISTRATION.md`):
V1 and V3, N = 2, and a variant survives only if it also survives with every
exit at the taker rate. ``original`` is the pre-fix test of 2026-10-25; its V1
and V3 were registered with a fill this code no longer runs, so only V0 and V2
are scored, against its own N of 4.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.core.limit_shadow import (  # noqa: E402
    FORWARD_CUTOFF,
    FORWARD_CUTOFF_MS,
    N_PREREGISTERED,
    N_RECLAIM_FORWARD,
    RECLAIM_FORWARD_CUTOFF,
    RECLAIM_FORWARD_CUTOFF_MS,
    RECLAIM_FORWARD_VARIANTS,
    forward_verdict,
    load_bars,
    load_funding,
    simulate,
    variant_params,
)
from bot.core.trade_costs import TAKER, leg_rate_pct  # noqa: E402

TESTS = {
    "reclaim": {
        "name": "limit_scanner_reclaim_forward", "variants": RECLAIM_FORWARD_VARIANTS,
        "cutoff_ms": RECLAIM_FORWARD_CUTOFF_MS, "cutoff": RECLAIM_FORWARD_CUTOFF,
        "n_trials": N_RECLAIM_FORWARD, "taker_exit_check": True, "note": "",
    },
    "original": {
        "name": "limit_scanner_forward", "variants": ("V0", "V2"),
        "cutoff_ms": FORWARD_CUTOFF_MS, "cutoff": FORWARD_CUTOFF,
        "n_trials": N_PREREGISTERED, "taker_exit_check": False,
        "note": "pre-fix test: its V1 and V3 cannot be reproduced from this code and are not scored",
    },
}


def score(bars, prints, test: str, base) -> dict:
    """Each variant's verdict in ``test``, funding priced from ``prints``."""
    t = TESTS[test]
    window = {"cutoff_ms": t["cutoff_ms"], "cutoff": t["cutoff"], "n_trials": t["n_trials"]}
    rows = []
    for name in t["variants"]:
        params, spec = variant_params(base, name)
        row = {"variant": name,
               "verdict": forward_verdict(simulate(bars, params, spec)["trades"], prints, **window)}
        passed = row["verdict"]["verdict"] == "survives"
        if t["taker_exit_check"]:
            taker = replace(spec, exit_fee_bp=leg_rate_pct(TAKER) * 100.0)
            row["verdict_taker_exits"] = forward_verdict(
                simulate(bars, params, taker)["trades"], prints, **window)
            passed = passed and row["verdict_taker_exits"]["verdict"] == "survives"
        row["survives"] = passed
        rows.append(row)
    return {"test": t["name"], "cutoff": t["cutoff"], "n_trials": t["n_trials"],
            "note": t["note"], "variants": rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bars", required=True, type=Path)
    ap.add_argument("--funding", required=True, type=Path)
    ap.add_argument("--test", choices=sorted(TESTS), default="reclaim")
    args = ap.parse_args()
    from bot.core.limit_scanner import params_from_preset
    from bot.skills.skill_registry import RunStrategySkill
    base = params_from_preset(RunStrategySkill.PRESETS["limit scanner"])
    if base is None:
        raise SystemExit("preset unreadable")
    bars, failed = load_bars(args.bars)
    out = score(bars, load_funding(args.funding, args.bars), args.test, base)
    out["dataset_failed"] = failed
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
