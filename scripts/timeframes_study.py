#!/usr/bin/env python3
"""The bot, unchanged, on other bar sizes, as pre-registered.

Design and rule: docs/research/prereg/timeframes_2026-10/PREREGISTRATION.md (and
ADDENDUM_1.md for the daily set).

    python3 scripts/timeframes_study.py run --out DIR
    python3 scripts/timeframes_study.py report --out DIR
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: timeframe -> (dataset, breaker reset bars = one day of bars, decides?)
RUNS = {
    "15m": ("majors_15m", 96, True),
    "4h": ("majors_4h", 6, True),
    "1d": ("majors_1d", 1, True),
    "1h": ("majors_1h_tf", 24, False),     # the reference, deciding nothing
}
FOLD_LOSS_FLOOR = -5.0


def run_one(out: Path, tf: str) -> int:
    ds, reset, _ = RUNS[tf]
    path = out / f"{tf}.json"
    if path.exists() and path.stat().st_size > 0:
        return 0
    cmd = [sys.executable, "-m", "bot.backtest.runner", "--dataset", f"benchmark/{ds}",
           "--timeframe", tf, "--honest", "--walk-forward", "6",
           "--breaker-reset-bars", str(reset), "-o", str(path)]
    return subprocess.run(cmd, cwd=ROOT, env=dict(os.environ), stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode


def judge(folds: list[dict]) -> dict:
    """The pre-registered screen over six walk-forward folds in time order."""
    folds = sorted(folds, key=lambda f: str(f["oos_start"]))
    rets = [float(f["return_pct"]) for f in folds]
    out = {"n_folds": len(rets), "returns": rets, "passed": False,
           "disc_mean": None, "hold_mean": None, "worst_hold": None}
    if len(rets) != 6:
        return out
    disc, hold = rets[:3], rets[3:]
    out.update(disc_mean=sum(disc) / 3, hold_mean=sum(hold) / 3, worst_hold=min(hold))
    out["passed"] = bool(out["disc_mean"] > 0 and out["hold_mean"] > 0
                         and out["worst_hold"] > FOLD_LOSS_FLOOR)
    return out


def buy_and_hold(ds: str, folds: list[dict]) -> list[float | None]:
    """Equal-weight buy-and-hold return (%) over each fold's out-of-sample window."""
    from bot.backtest.snapshot import load_dataset
    data = load_dataset(str(ROOT / "benchmark" / ds))
    out: list[float | None] = []
    for f in sorted(folds, key=lambda f: str(f["oos_start"])):
        a, b = datetime.fromisoformat(str(f["oos_start"])), datetime.fromisoformat(str(f["oos_end"]))
        rs = []
        for bars in data.values():
            inside = [x for x in bars if a <= x.timestamp <= b]
            if len(inside) >= 2:
                rs.append(inside[-1].close / inside[0].open - 1)
        out.append(100 * sum(rs) / len(rs) if rs else None)
    return out


def report(out: Path) -> dict:
    res = {}
    for tf, (ds, _, decides) in RUNS.items():
        p = out / f"{tf}.json"
        if not p.exists():
            res[tf] = {"missing": True}
            continue
        r = json.loads(p.read_text())
        folds = r.get("folds") or []
        j = judge(folds)
        j["decides"] = decides
        j["trades"] = sum(int(f.get("trades", 0)) for f in folds)
        j["buy_and_hold"] = buy_and_hold(ds, folds)
        res[tf] = j
        tag = ("PASS" if j["passed"] else "FAIL") if decides else "reference"
        rets = ", ".join(f"{x:+.2f}" for x in j["returns"])
        bh = ", ".join("n/a" if x is None else f"{x:+.1f}" for x in j["buy_and_hold"])
        print(f"{tf:>4}: folds [{rets}]  disc {j['disc_mean']}  hold {j['hold_mean']}  "
              f"worst hold {j['worst_hold']}  trades {j['trades']}  {tag}")
        print(f"      buy-and-hold per fold: [{bh}]")
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "run":
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            for tf, rc in zip(RUNS, pool.map(lambda t: run_one(a.out, t), RUNS)):
                print(tf, "rc", rc, flush=True)
        return 0
    res = report(a.out)
    (a.out / "report.json").write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
