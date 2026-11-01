#!/usr/bin/env python3
"""A closer target on the same entries, as sealed.

Design and rule: docs/research/prereg/closer_target_2026-10/PREREGISTRATION.md.

    python3 scripts/closer_target_benchmark.py report --runs DIR

DIR holds ``<dataset>_<arm>.json`` and ``.trades.json`` (or ``.trades.json.gz``)
for arms B, T10 and T15.
"""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
from pathlib import Path

import numpy as np

_spec = importlib.util.spec_from_file_location(
    "live_history_benchmark", Path(__file__).resolve().parent / "live_history_benchmark.py")
_lhb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lhb)
folds, summary = _lhb.folds, _lhb.summary

DATASETS = _lhb.DATASETS
CONTROL, ARMS = "B", ["T10", "T15"]
BOOT, SEED = 10_000, 7
#: One-sided 97.5% split over the two arms (Bonferroni): each bound at 98.75%.
LOWER_PCT = 2.5 / len(ARMS)
MIN_SETS = 4


def _lower(x: np.ndarray) -> float:
    draws = x[np.random.default_rng(SEED).integers(0, len(x), size=(BOOT, len(x)))].mean(1)
    return float(np.percentile(draws, LOWER_PCT))


def target_verdict(base: dict[str, list[float]], arm: dict[str, list[float]]) -> dict:
    """Q1: paired fold differences arm - control over every set."""
    diffs, better, worse = [], 0, 0
    for ds in base:
        if len(base[ds]) != len(arm[ds]):
            return {"verdict": f"no verdict: {ds} has unequal folds"}
        diffs += [a - b for b, a in zip(base[ds], arm[ds])]
        better += float(np.mean(arm[ds])) > float(np.mean(base[ds]))
        worse += float(np.mean(arm[ds])) < float(np.mean(base[ds]))
    x = np.array(diffs)
    out = {"n_folds": len(x), "mean_diff": float(x.mean()), "lower_diff": _lower(x),
           "lower_neg_diff": _lower(-x), "sets_better": better, "sets_worse": worse}
    if out["mean_diff"] > 0 and out["lower_diff"] > 0 and better >= MIN_SETS:
        out["verdict"] = "the closer target helps"
    elif out["mean_diff"] < 0 and out["lower_neg_diff"] > 0 and worse >= MIN_SETS:
        out["verdict"] = "the closer target hurts"
    else:
        out["verdict"] = "no verdict"
    return out


def money_verdict(arm: dict[str, list[float]]) -> dict:
    """Q2: does the arm make money: fold returns over every set."""
    x = np.array([v for ds in arm for v in arm[ds]])
    positive = sum(float(np.mean(arm[ds])) > 0 for ds in arm)
    out = {"n_folds": len(x), "mean": float(x.mean()), "lower": _lower(x),
           "sets_positive": positive}
    out["verdict"] = ("makes money" if out["mean"] > 0 and out["lower"] > 0
                      and positive >= MIN_SETS else "does not show it makes money")
    return out


def _rows(runs: Path, ds: str, arm: str) -> list[dict]:
    plain = runs / f"{ds}_{arm}.trades.json"
    raw = (plain.read_text() if plain.exists()
           else gzip.decompress((runs / f"{plain.name}.gz").read_bytes()).decode())
    return json.loads(raw)["trades"]


def exit_mix(rows: list[dict]) -> dict:
    """Share of closes by exit reason, as the trade rows name them."""
    out: dict = {}
    for r in rows:
        k = str(r.get("exit_reason") or "unrecorded")
        out[k] = out.get(k, 0) + 1
    return out


def report(runs: Path) -> dict:
    per: dict = {}
    for ds in DATASETS:
        per[ds] = {}
        for arm in [CONTROL, *ARMS]:
            run = json.loads((runs / f"{ds}_{arm}.json").read_text())
            rows = _rows(runs, ds, arm)
            per[ds][arm] = summary(run, rows) | {"exits": exit_mix(rows)}
    base = {ds: per[ds][CONTROL]["folds"] for ds in DATASETS}
    out: dict = {"per": per, "q1": {}, "q2": {}}
    for arm in ARMS:
        folds_arm = {ds: per[ds][arm]["folds"] for ds in DATASETS}
        out["q1"][arm] = target_verdict(base, folds_arm)
        out["q2"][arm] = money_verdict(folds_arm)
    for ds in DATASETS:
        cells = "  ".join(f"{a}: fold {per[ds][a]['mean_fold']:+.2f}% n {per[ds][a]['positions']}"
                          for a in [CONTROL, *ARMS])
        print(f"{ds:14} {cells}")
    print("Q1:", json.dumps(out["q1"]))
    print("Q2:", json.dumps(out["q2"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["report"])
    ap.add_argument("--runs", type=Path, required=True)
    a = ap.parse_args()
    (a.runs / "report.json").write_text(json.dumps(report(a.runs), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
