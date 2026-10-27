#!/usr/bin/env python3
"""Maker (limit) entries against taker (market) entries, as pre-registered.

Every arm runs the live system (`--as-live`); only the entry fills differ. Arms,
datasets, windows and the pass rule are in
docs/research/prereg/maker_orders_2026-10/PREREGISTRATION.md. Offline only.

Usage:
    python3 scripts/maker_test.py run    --out DIR [--jobs 3]
    python3 scripts/maker_test.py report --out DIR
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import risk_sweep as rs  # noqa: E402

BASE = "market"
ARMS: dict[str, dict] = {
    "market": {"env": {"DEFAULT_ORDER_TYPE": "market"}, "args": []},
    "touch": {"env": {}, "args": []},
    "realistic": {"env": {}, "args": [
        "--limit-through-bp", "5", "--limit-queue-p", "0.5",
        "--limit-queue-sure-bp", "10", "--limit-maker-entry"]},
}
PRIMARY = "realistic"


def run_one(out: Path, dataset: str, arm: str) -> int:
    path = rs.out_path(out, dataset, arm)
    if path.exists() and path.stat().st_size > 0:
        return 0
    spec = ARMS[arm]
    cmd = [sys.executable, "-m", "bot.backtest.runner", "--dataset", f"benchmark/{dataset}",
           "--as-live", "--breaker-reset-bars", "24", "--walk-forward", "6",
           "-o", str(path), *spec["args"]]
    done = subprocess.run(cmd, cwd=ROOT, env={**os.environ, **spec["env"]},
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return done.returncode


def run(out: Path, jobs: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    work = [(d, a) for d, _ in rs.STAGE1 for a in ARMS]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for (d, a), rc in zip(work, pool.map(lambda w: run_one(out, *w), work)):
            print(f"{d} {a} rc={rc}", flush=True)


def lifecycle(results: list[dict]) -> dict:
    """Ideas, trades and what became of the limits, summed over every fold."""
    tot = {"ideas": 0, "trades": 0, "filled": 0, "expired": 0, "cancelled_drift": 0}
    for r in results:
        for f in r["folds"]:
            tot["trades"] += int(f["trades"])
            tot["ideas"] += int(f.get("ideas", 0))
            for k in ("filled", "expired", "cancelled_drift"):
                tot[k] += int(f.get("limits", {}).get(k, 0))
    rest = tot["filled"] + tot["expired"] + tot["cancelled_drift"]
    tot["fill_rate"] = tot["filled"] / rest if rest else None
    return tot


def report(out: Path) -> None:
    print(f"datasets: {[d for d, _ in rs.STAGE1]}\n")
    for arm in ARMS:
        runs = [r for d, _ in rs.STAGE1 if (r := rs.load(out, d, arm)) is not None]
        if len(runs) < len(rs.STAGE1):
            print(f"{arm}: missing runs")
            continue
        lc = lifecycle(runs)
        fr = f"{lc['fill_rate']:.2f}" if lc["fill_rate"] is not None else "n/a (no limits)"
        print(f"{arm:10} trades {lc['trades']:5}  ideas {lc['ideas']:5}  limits filled "
              f"{lc['filled']:5} expired {lc['expired']:5} drift-cancelled {lc['cancelled_drift']:5}"
              f"  fill rate {fr}")
    print()
    hdr = "  ".join(f"{d[:13]:>13}" for d, _ in rs.STAGE1)
    print(f"{'vs ' + BASE:12} {hdr}  {'holdout':>8} {'ddOK':>4}  {'mean [95% CI]':>22}  verdict")
    for arm in ("touch", "realistic"):
        per = {}
        for d, w in rs.STAGE1:
            base, run_ = rs.load(out, d, BASE), rs.load(out, d, arm)
            if base is not None and run_ is not None:
                per[d] = (w, rs.paired(base, run_, w))
        if len(per) < len(rs.STAGE1):
            print(f"{arm:12} missing runs")
            continue
        j = rs.judge_stage1(per)
        cells = "  ".join(f"{j['means'][d]:+13.2f}" for d, _ in rs.STAGE1)
        tag = ("PASS" if j["passed"] else "fail") if arm == PRIMARY else "upper bound"
        print(f"{arm:12} {cells}  {j['holdout_mean']:+8.2f} {'y' if j['holdout_dd_not_worse'] else 'n':>4}  "
              f"{j['mean']:+6.2f} [{j['lo']:+.2f},{j['hi']:+.2f}]  {tag}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("run", "report"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=3)
    args = ap.parse_args()
    out = Path(args.out)
    if args.cmd == "run":
        run(out, args.jobs)
    else:
        report(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
