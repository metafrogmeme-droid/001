#!/usr/bin/env python3
"""Timeframe variants, as sealed: run the six arms and judge the four tested ones.

Design and rule: docs/research/prereg/timeframe_variants_2026-10/PREREGISTRATION.md.

    python3 scripts/timeframe_variants_study.py run --out DIR
    python3 scripts/timeframe_variants_study.py report --out DIR
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

#: arm -> (dataset, timeframe, one day of bars, extra flags, tested?, reference)
ARMS = {
    "R1": ("fresh10_1h", "1h", 24, [], False, None),
    "H1": ("fresh10_1h", "1h", 24, ["--swing-stop-tf", "4h"], True, "R1"),
    "H3": ("fresh10_1h", "1h", 24, ["--daily-trend-days", "50"], True, "R1"),
    "RS": ("fresh10_1h", "1h", 24, ["--setup-only", "scalp"], False, None),
    "H2a": ("fresh10_15m", "15m", 96, ["--setup-only", "scalp", "--mtf-scaled-history"], True, "RS"),
    "H2b": ("fresh10_5m", "5m", 288, ["--setup-only", "scalp", "--mtf-scaled-history"], True, "RS"),
}
TESTED = [a for a, v in ARMS.items() if v[4]]
#: ADDENDUM_1.md: H3 with live's history. H3h shares the family's bound.
ADDENDUM_ARMS = {
    "R1h": ("fresh10_1h", "1h", 24, ["--fold-history-bars", "4824"], False, None),
    "H3h": ("fresh10_1h", "1h", 24, ["--fold-history-bars", "4824", "--daily-trend-days", "50"],
            True, "R1h"),
}
MIN_POSITIONS = 40
BOOT, SEED = 10_000, 7
#: one-sided 2.5% shared by the four tested arms (Bonferroni)
LOWER_PCT = 2.5 / 4


def run_one(out: Path, arm: str) -> int:
    ds, tf, reset, extra, _, _ = {**ARMS, **ADDENDUM_ARMS}[arm]
    if (out / f"{arm}.trades.json").exists():
        return 0
    cmd = [sys.executable, "-m", "bot.backtest.runner", "--dataset", f"benchmark/{ds}",
           "--timeframe", tf, "--honest", "--walk-forward", "6",
           "--breaker-reset-bars", str(reset), *extra,
           "-o", str(out / f"{arm}.json"), "--trades-out", str(out / f"{arm}.trades.json")]
    return subprocess.run(cmd, cwd=ROOT, env=dict(os.environ), stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode


def judge(rows: list[dict], fold_returns: list[float]) -> dict:
    """The sealed three conditions. Unreadable rows are counted, never zeroed."""
    read = [r for r in rows if r.get("net_pnl_pct") is not None]
    out: dict = {"n": len(read), "unreadable": len(rows) - len(read), "mean": None,
                 "lower": None, "fold_mean": None, "makes_money": None}
    if fold_returns:
        out["fold_mean"] = float(np.mean(fold_returns))
    if len(read) < MIN_POSITIONS:
        out["verdict"] = f"no verdict: {len(read)} positions, fewer than {MIN_POSITIONS}"
        return out
    x = np.array([float(r["net_pnl_pct"]) for r in read])
    out["mean"] = float(x.mean())
    weeks: dict[tuple[int, int], list[float]] = {}
    for r in read:
        y, w, _ = datetime.fromisoformat(r["entry_time"]).isocalendar()
        weeks.setdefault((y, w), []).append(float(r["net_pnl_pct"]))
    sums = np.array([sum(v) for v in weeks.values()])
    counts = np.array([len(v) for v in weeks.values()])
    if len(sums) >= 2:
        idx = np.random.default_rng(SEED).integers(0, len(sums), size=(BOOT, len(sums)))
        out["lower"] = float(np.percentile(sums[idx].sum(1) / counts[idx].sum(1), LOWER_PCT))
    out["makes_money"] = bool(out["mean"] > 0 and out["lower"] is not None and out["lower"] > 0
                              and out["fold_mean"] is not None and out["fold_mean"] > 0)
    out["verdict"] = "MAKES MONEY" if out["makes_money"] else "does not make money"
    return out


def describe(rows: list[dict]) -> dict:
    read = [r for r in rows if r.get("net_pnl_pct") is not None]
    sized = [(r["size_rel"], r["net_pnl_pct"]) for r in read if r.get("size_rel") is not None]
    exits: dict[str, int] = {}
    for r in read:
        exits[str(r.get("exit_reason"))] = exits.get(str(r.get("exit_reason")), 0) + 1
    return {"size_weighted_mean": (sum(s * v for s, v in sized) / sum(s for s, _ in sized)
                                   if sized else None),
            "exits": dict(sorted(exits.items(), key=lambda kv: -kv[1]))}


def report(out: Path, arms: dict = ARMS) -> dict:
    res: dict = {}
    for arm in arms:
        r = json.loads((out / f"{arm}.json").read_text())
        rows = json.loads((out / f"{arm}.trades.json").read_text())["trades"]
        folds = [float(f["return_pct"]) for f in sorted(r["folds"], key=lambda f: str(f["oos_start"]))]
        j = judge(rows, folds)
        j.update(describe(rows), folds=folds, pf=r.get("pooled", {}).get("pf"),
                 code_sha=r.get("code_sha"), data_source=r.get("data_source"))
        res[arm] = j
    for arm in [a for a, v in arms.items() if v[4]]:
        ref = res[arms[arm][5]]
        if res[arm]["mean"] is not None and ref["mean"] is not None:
            res[arm]["vs_reference"] = res[arm]["mean"] - ref["mean"]
    for arm, j in res.items():
        tag = j.get("verdict") if arms[arm][4] else "reference"
        print(f"{arm:4} n={j['n']:4d} mean={j['mean']} lower={j['lower']} "
              f"fold_mean={j['fold_mean']} pf={j['pf']} | {tag}")
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["run", "report", "run-addendum", "report-addendum"])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "run":
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            for arm, rc in zip(ARMS, pool.map(lambda x: run_one(a.out, x), ARMS)):
                print(arm, "rc", rc, flush=True)
        return 0
    if a.cmd == "run-addendum":
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            for arm, rc in zip(ADDENDUM_ARMS, pool.map(lambda x: run_one(a.out, x), ADDENDUM_ARMS)):
                print(arm, "rc", rc, flush=True)
        return 0
    if a.cmd == "report-addendum":
        (a.out / "report_addendum_1.json").write_text(
            json.dumps(report(a.out, ADDENDUM_ARMS), indent=1, default=str))
        return 0
    (a.out / "report.json").write_text(json.dumps(report(a.out), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
