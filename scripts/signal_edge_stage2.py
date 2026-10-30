#!/usr/bin/env python3
"""Signal-edge stage 2: the bot's calls on data it has never seen, as sealed.

Design and rule: docs/research/prereg/signal_edge_stage2_2026-10/PREREGISTRATION.md.

    python3 scripts/signal_edge_stage2.py run --out DIR
    python3 scripts/signal_edge_stage2.py report --out DIR
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

import numpy as np

ROOT = Path(__file__).resolve().parent.parent

DATASETS = {"F1": "majors_1h_fresh", "F2": "kraken_ethsol_1h"}
#: arm -> extra runner arguments
ARMS = {
    "A0": [],
    "A80": ["--confidence-threshold", "0.80"],
    "A0_gross": ["--commission", "0", "--slippage", "0"],
}
DECIDING = ("A0", "A80")
BOOT, SEED = 10_000, 7
#: one-sided 98.75%: two arms, Bonferroni over a one-sided 97.5% bar
LOWER_PCT = 1.25


def run_one(out: Path, ds_key: str, arm: str) -> int:
    stem = out / f"{ds_key}_{arm}"
    if (stem.with_suffix(".trades.json")).exists():
        return 0
    cmd = [sys.executable, "-m", "bot.backtest.runner", "--dataset",
           f"benchmark/{DATASETS[ds_key]}", "--timeframe", "1h", "--honest",
           "--walk-forward", "6", "--breaker-reset-bars", "24", *ARMS[arm],
           "-o", str(stem.with_suffix(".json")),
           "--trades-out", str(stem.with_suffix(".trades.json"))]
    return subprocess.run(cmd, cwd=ROOT, env=dict(os.environ), stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode


def week_of(iso: str) -> tuple[int, int]:
    y, w, _ = datetime.fromisoformat(iso).isocalendar()
    return (y, w)


def judge(rows: list[dict]) -> dict:
    """Pooled mean net % per position and the week-block bootstrap lower bound."""
    read = [r for r in rows if r.get("net_pnl_pct") is not None]
    out: dict = {"n": len(read), "unreadable": len(rows) - len(read), "mean": None,
                 "lower": None, "makes_money": False}
    if not read:
        return out
    x = np.array([float(r["net_pnl_pct"]) for r in read])
    out["mean"] = float(x.mean())
    weeks: dict[tuple[int, int], list[float]] = {}
    for r in read:
        weeks.setdefault(week_of(r["entry_time"]), []).append(float(r["net_pnl_pct"]))
    blocks = list(weeks.values())
    if len(blocks) >= 2:
        sums = np.array([sum(b) for b in blocks])
        counts = np.array([len(b) for b in blocks])
        rng = np.random.default_rng(SEED)
        idx = rng.integers(0, len(blocks), size=(BOOT, len(blocks)))
        draws = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
        out["lower"] = float(np.percentile(draws, LOWER_PCT))
    out["makes_money"] = bool(out["mean"] > 0 and out["lower"] is not None and out["lower"] > 0)
    return out


def verdict(per: dict) -> dict:
    """per[arm][ds_key] -> judge(); the sealed verdicts."""
    v = {arm: all(per[arm][d]["makes_money"] for d in DATASETS) for arm in DECIDING}
    gross = per["A0_gross"]
    v["information"] = all(gross[d]["mean"] is not None and gross[d]["mean"] > 0 for d in DATASETS)
    return v


def report(out: Path) -> dict:
    per: dict = {}
    for arm in ARMS:
        per[arm] = {}
        for d in DATASETS:
            rows = json.loads((out / f"{d}_{arm}.trades.json").read_text())["trades"]
            per[arm][d] = judge(rows)
            by_coin: dict[str, list] = {}
            for r in rows:
                by_coin.setdefault(r["symbol"], []).append(r)
            # Reported per coin, deciding nothing: no verdict field.
            per[arm][d]["by_coin"] = {
                s: {k: v for k, v in judge(rs).items() if k != "makes_money"}
                for s, rs in sorted(by_coin.items())}
    res = {"per": per, "verdict": verdict(per)}
    for arm in ARMS:
        for d in DATASETS:
            j = per[arm][d]
            print(f"{arm:9} {d}: n {j['n']:4d}  mean {j['mean']}  lower {j['lower']}  "
                  f"{'MAKES MONEY' if j['makes_money'] else '-'}")
    print("verdict:", res["verdict"])
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["run", "report"])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "run":
        jobs = [(d, arm) for d in DATASETS for arm in ARMS]
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            for (d, arm), rc in zip(jobs, pool.map(lambda j: run_one(a.out, *j), jobs)):
                print(d, arm, "rc", rc, flush=True)
        return 0
    res = report(a.out)
    (a.out / "report.json").write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
