#!/usr/bin/env python3
"""Offline risk-settings sweep: one change at a time, judged as pre-registered.

Arms, datasets, windows and the pass rule are fixed in
docs/research/prereg/risk_settings_2026-10/PREREGISTRATION.md. Each arm sets
environment variables (read at import by bot/config.py) for one backtest run;
nothing here changes a default or touches the live bot.

Usage:
    python3 scripts/risk_sweep.py run    --out DIR [--stage 1|2] [--jobs 3]
    python3 scripts/risk_sweep.py report --out DIR [--stage 1|2]
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

ARMS: dict[str, dict[str, str]] = {
    "kelly_off": {"KELLY_SIZING_ENABLED": "0"},
    "kelly_floor": {"KELLY_FLOOR_RISK_PCT": "1.0"},
    "risk_half": {"SCALP_MAX_RISK_PCT": "0.5", "INTRADAY_MAX_RISK_PCT": "0.75",
                  "SWING_MAX_RISK_PCT": "1.0", "POSITION_MAX_RISK_PCT": "1.0"},
    "cap_half": {"SCALP_MAX_POSITION_PCT": "4", "INTRADAY_MAX_POSITION_PCT": "5",
                 "SWING_MAX_POSITION_PCT": "6.5", "POSITION_MAX_POSITION_PCT": "7.5"},
    "stop_floor": {"MIN_STOP_DISTANCE_PCT": "0.015"},
    "sl_wide": {"SCALP_SL_ATR_MULT": "1.95", "INTRADAY_SL_ATR_MULT": "2.6",
                "SWING_SL_ATR_MULT": "3.25", "POSITION_SL_ATR_MULT": "3.9"},
    "conf_70": {"MIN_CONFIDENCE": "0.70"},
    "open_3": {"MAX_OPEN_POSITIONS": "3"},
    "streak_3": {"MAX_CONSECUTIVE_LOSSES": "3"},
    "regime_off": {"REGIME_SIZING_ENABLED": "0"},
    "fee_gate_3": {"FEE_AWARE_ENTRY_GATE_ENABLED": "1", "FEE_AWARE_MIN_MULTIPLE": "3"},
    "fee_gate_6": {"FEE_AWARE_ENTRY_GATE_ENABLED": "1", "FEE_AWARE_MIN_MULTIPLE": "6"},
}
BASELINE = "baseline"
STAGE1 = (("corr_dense_1h", "discovery"), ("majors_1h_v2", "discovery"),
          ("majors_1h_v3", "holdout"), ("alts_1h_v3", "holdout"))
STAGE2 = (("majors_1h", "all"), ("alts_1h", "all"), ("alts_1h_v2", "all"))
DISCOVERY_END = "2026-05-22T02:00:00+00:00"
HOLDOUT_START = "2026-07-06T12:00:00+00:00"
MIN_GAIN_PP = 1.0
BOOT = 5000
SEED = 7


def out_path(out: Path, dataset: str, arm: str) -> Path:
    return out / f"{dataset}__{arm}.json"


def run_one(out: Path, dataset: str, arm: str) -> int:
    path = out_path(out, dataset, arm)
    if path.exists() and path.stat().st_size > 0:
        return 0
    env = {**os.environ, **({} if arm == BASELINE else ARMS[arm])}
    cmd = [sys.executable, "-m", "bot.backtest.runner", "--dataset", f"benchmark/{dataset}",
           "--honest", "--breaker-reset-bars", "24", "--walk-forward", "6", "-o", str(path)]
    done = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
    return done.returncode


def run(out: Path, stage: int, jobs: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    datasets = [d for d, _ in (STAGE1 if stage == 1 else STAGE2)]
    work = [(d, a) for d in datasets for a in [BASELINE, *ARMS]]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for (d, a), rc in zip(work, pool.map(lambda w: run_one(out, *w), work)):
            print(f"{d} {a} rc={rc}", flush=True)


# ── reading and judging ────────────────────────────────────────────────────

def _ts(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


def window_folds(result: dict, window: str) -> dict[int, dict]:
    """Folds of one run inside the window, keyed by fold position."""
    out: dict[int, dict] = {}
    for f in result["folds"]:
        if window == "discovery" and _ts(str(f["oos_end"])) > _ts(DISCOVERY_END):
            continue
        if window == "holdout" and _ts(str(f["oos_start"])) < _ts(HOLDOUT_START):
            continue
        out[int(f["fold"])] = f
    return out


def paired(base: dict, arm: dict, window: str) -> list[dict]:
    """Per fold: arm minus baseline return and drawdown, for folds both ran."""
    b, a = window_folds(base, window), window_folds(arm, window)
    return [{"fold": k, "d_ret": a[k]["return_pct"] - b[k]["return_pct"],
             "d_dd": a[k]["max_dd_pct"] - b[k]["max_dd_pct"],
             "dd_arm": a[k]["max_dd_pct"], "dd_base": b[k]["max_dd_pct"]}
            for k in sorted(b) if k in a]


def cluster_ci(clusters: list[float], boot: int, rng: np.random.Generator
               ) -> tuple[float, float, float]:
    x = np.array(clusters)
    if len(x) < 2:
        return float("nan"), float("nan"), float("nan")
    draws = x[rng.integers(0, len(x), size=(boot, len(x)))].mean(axis=1)
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return float(x.mean()), float(lo), float(hi)


def judge_stage1(per_dataset: dict[str, tuple[str, list[dict]]], boot: int = BOOT,
                 seed: int = SEED) -> dict:
    """The stage 1 rule over {dataset: (window, paired folds)}."""
    means = {d: (float(np.mean([r["d_ret"] for r in rows])) if rows else float("nan"))
             for d, (_, rows) in per_dataset.items()}
    hold = [r for _, (w, rows) in per_dataset.items() if w == "holdout" for r in rows]
    hold_mean = float(np.mean([r["d_ret"] for r in hold])) if hold else float("nan")
    dd_ok = bool(hold and np.mean([r["dd_arm"] for r in hold])
                 <= np.mean([r["dd_base"] for r in hold]))
    # One cluster per (window, fold position): datasets that cover the same
    # dates move together, so their difference is averaged before resampling.
    groups: dict[tuple[str, int], list[float]] = {}
    for _, (w, rows) in per_dataset.items():
        for r in rows:
            groups.setdefault((w, r["fold"]), []).append(r["d_ret"])
    clusters = [float(np.mean(v)) for v in groups.values()]
    mean, lo, hi = cluster_ci(clusters, boot, np.random.default_rng(seed))
    each = all(np.isfinite(m) and m >= MIN_GAIN_PP for m in means.values())
    rule1 = bool(means) and each
    rule2 = bool(np.isfinite(hold_mean) and hold_mean >= MIN_GAIN_PP and dd_ok)
    rule3 = bool(np.isfinite(lo) and lo > 0)
    return {"means": means, "holdout_mean": hold_mean, "holdout_dd_not_worse": dd_ok,
            "mean": mean, "lo": lo, "hi": hi, "rule1": rule1, "rule2": rule2,
            "rule3": rule3, "passed": rule1 and rule2 and rule3}


def load(out: Path, dataset: str, arm: str) -> dict | None:
    p = out_path(out, dataset, arm)
    return json.loads(p.read_text()) if p.exists() and p.stat().st_size > 0 else None


def report(out: Path, stage: int) -> None:
    sets = STAGE1 if stage == 1 else STAGE2
    print(f"stage {stage}: {[d for d, _ in sets]}\n")
    hdr = "  ".join(f"{d[:13]:>13}" for d, _ in sets)
    print(f"{'arm':12} {hdr}  {'holdout':>8} {'ddOK':>4}  {'mean [95% CI]':>22}  verdict")
    for arm in ARMS:
        per = {}
        for d, w in sets:
            base, run_ = load(out, d, BASELINE), load(out, d, arm)
            if base is not None and run_ is not None:
                per[d] = (w, paired(base, run_, w))
        if len(per) < len(sets):
            print(f"{arm:12} missing runs: {sorted(set(d for d, _ in sets) - set(per))}")
            continue
        j = judge_stage1(per)
        cells = "  ".join(f"{j['means'][d]:+13.2f}" for d, _ in sets)
        if stage == 1:
            tag = "PASS" if j["passed"] else "fail"
        else:
            tag = "positive" if all(m > 0 for m in j["means"].values()) else "not replicated"
        print(f"{arm:12} {cells}  {j['holdout_mean']:+8.2f} {'y' if j['holdout_dd_not_worse'] else 'n':>4}  "
              f"{j['mean']:+6.2f} [{j['lo']:+.2f},{j['hi']:+.2f}]  {tag}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("run", "report"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--stage", type=int, choices=(1, 2), default=1)
    ap.add_argument("--jobs", type=int, default=3)
    args = ap.parse_args()
    out = Path(args.out)
    if args.cmd == "run":
        run(out, args.stage, args.jobs)
    else:
        report(out, args.stage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
