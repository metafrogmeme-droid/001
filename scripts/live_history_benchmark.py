#!/usr/bin/env python3
"""The benchmark re-measured with live's history, as sealed.

Design and rule: docs/research/prereg/live_history_benchmark_2026-10/PREREGISTRATION.md.

    python3 scripts/live_history_benchmark.py report --runs DIR

DIR holds ``<dataset>_<arm>.json`` and ``.trades.json`` for arms A, B, C.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import numpy as np

DATASETS = ["majors_1h", "majors_1h_v2", "majors_1h_v3", "alts_1h_v3", "corr_dense_1h"]
ARMS = ["A", "B", "C"]
BOOT, SEED, LOWER_PCT = 10_000, 7, 2.5
MIN_SETS = 4


def folds(run: dict) -> list[float]:
    return [float(f["return_pct"]) for f in sorted(run["folds"], key=lambda f: str(f["oos_start"]))]


def summary(run: dict, rows: list[dict]) -> dict:
    read = [r for r in rows if r.get("net_pnl_pct") is not None]
    sized = [(r["size_rel"], r["net_pnl_pct"]) for r in read if r.get("size_rel") is not None]
    fr = folds(run)
    return {"positions": len(read), "unreadable": len(rows) - len(read),
            "mean_fold": float(np.mean(fr)) if fr else None, "folds": fr,
            "pf": (run.get("pooled") or {}).get("pf"),
            "size_weighted_mean": (sum(s * v for s, v in sized) / sum(s for s, _ in sized)
                                   if sized else None)}


def gate_verdict(b: dict[str, list[float]], c: dict[str, list[float]]) -> dict:
    """Q2: paired fold differences C - B over every set. 'costs' when C wins,
    'earns' when B wins, each needing the mean, its bound and >= MIN_SETS sets."""
    diffs, wins_c, wins_b = [], 0, 0
    for ds in b:
        if len(b[ds]) != len(c[ds]):
            return {"verdict": f"no verdict: {ds} has unequal folds"}
        d = [ci - bi for bi, ci in zip(b[ds], c[ds])]
        diffs += d
        wins_c += float(np.mean(c[ds])) > float(np.mean(b[ds]))
        wins_b += float(np.mean(b[ds])) > float(np.mean(c[ds]))
    x = np.array(diffs)
    draws = x[np.random.default_rng(SEED).integers(0, len(x), size=(BOOT, len(x)))].mean(1)
    lo_c, lo_b = float(np.percentile(draws, LOWER_PCT)), float(np.percentile(-draws, LOWER_PCT))
    out = {"n_folds": len(x), "mean_c_minus_b": float(x.mean()), "lower_c_minus_b": lo_c,
           "lower_b_minus_c": lo_b, "sets_c_better": wins_c, "sets_b_better": wins_b}
    if x.mean() > 0 and lo_c > 0 and wins_c >= MIN_SETS:
        out["verdict"] = "the gate costs money"
    elif x.mean() < 0 and lo_b > 0 and wins_b >= MIN_SETS:
        out["verdict"] = "the gate earns its place"
    else:
        out["verdict"] = "no verdict"
    return out


def report(runs: Path) -> dict:
    per: dict = {}
    for ds in DATASETS:
        per[ds] = {}
        for arm in ARMS:
            run = json.loads((runs / f"{ds}_{arm}.json").read_text())
            plain = runs / f"{ds}_{arm}.trades.json"
            raw = (plain.read_text() if plain.exists()
                   else gzip.decompress((runs / f"{plain.name}.gz").read_bytes()).decode())
            rows = json.loads(raw)["trades"]
            per[ds][arm] = summary(run, rows)
    q2 = gate_verdict({ds: per[ds]["B"]["folds"] for ds in DATASETS},
                      {ds: per[ds]["C"]["folds"] for ds in DATASETS})
    for ds in DATASETS:
        cells = "  ".join(f"{a}: fold {per[ds][a]['mean_fold']:+.2f}% pf {per[ds][a]['pf']:.2f} "
                          f"n {per[ds][a]['positions']}" for a in ARMS)
        print(f"{ds:14} {cells}")
    print("Q2:", q2)
    return {"per": per, "q2": q2}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["report"])
    ap.add_argument("--runs", type=Path, required=True)
    a = ap.parse_args()
    (a.runs / "report.json").write_text(json.dumps(report(a.runs), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
