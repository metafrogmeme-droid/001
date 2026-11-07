#!/usr/bin/env python3
"""An earlier exit ladder, and live's own limit entries, as sealed.

Design and rule: docs/research/prereg/exit_ladder_2026-11/PREREGISTRATION.md.

    python3 scripts/exit_ladder_benchmark.py report --runs DIR

DIR holds ``<dataset>_<arm>.json`` and ``.trades.json`` (or ``.trades.json.gz``)
for arms B, E1, E2 and LV on the five primary sets and the confirmation set.
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
summary = _lhb.summary

PRIMARY = _lhb.DATASETS
CONFIRM = "fresh10_1h"
CONTROL, ARMS = "B", ["E1", "E2", "LV"]
LADDER_ARMS = ("E1", "E2")
BOOT, SEED = 10_000, 7
#: One-sided 97.5% split over the three comparisons (Bonferroni).
LOWER_PCT = 2.5 / len(ARMS)
MIN_SETS = 4


def _lower(x: np.ndarray) -> float:
    draws = x[np.random.default_rng(SEED).integers(0, len(x), size=(BOOT, len(x)))].mean(1)
    return float(np.percentile(draws, LOWER_PCT))


def compare(base: dict[str, list[float]], arm: dict[str, list[float]],
            confirm_diff: float | None) -> dict:
    """Paired fold differences arm - control over the primary sets, then the
    confirmation set's mean difference, which must agree in sign."""
    diffs, better, worse = [], 0, 0
    for ds in base:
        if len(base[ds]) != len(arm[ds]):
            return {"verdict": f"no verdict: {ds} has unequal folds"}
        diffs += [a - b for b, a in zip(base[ds], arm[ds])]
        better += float(np.mean(arm[ds])) > float(np.mean(base[ds]))
        worse += float(np.mean(arm[ds])) < float(np.mean(base[ds]))
    x = np.array(diffs)
    out = {"n_folds": len(x), "mean_diff": float(x.mean()), "lower_diff": _lower(x),
           "lower_neg_diff": _lower(-x), "sets_better": better, "sets_worse": worse,
           "confirm_diff": confirm_diff}
    helps = out["mean_diff"] > 0 and out["lower_diff"] > 0 and better >= MIN_SETS
    hurts = out["mean_diff"] < 0 and out["lower_neg_diff"] > 0 and worse >= MIN_SETS
    if confirm_diff is None:
        out["verdict"] = "no verdict: the confirmation set did not read"
    elif helps and confirm_diff > 0:
        out["verdict"] = "helps"
    elif hurts and confirm_diff < 0:
        out["verdict"] = "hurts"
    elif helps or hurts:
        out["verdict"] = "no verdict: the confirmation set disagrees"
    else:
        out["verdict"] = "no verdict"
    return out


def money(arm: dict[str, list[float]], confirm_mean: float | None) -> dict:
    """Does the arm make money: primary fold returns, and the confirmation set."""
    x = np.array([v for ds in arm for v in arm[ds]])
    positive = sum(float(np.mean(arm[ds])) > 0 for ds in arm)
    out = {"n_folds": len(x), "mean": float(x.mean()), "lower": _lower(x),
           "sets_positive": positive, "confirm_mean": confirm_mean}
    ok = (out["mean"] > 0 and out["lower"] > 0 and positive >= MIN_SETS
          and confirm_mean is not None and confirm_mean > 0)
    out["verdict"] = "makes money" if ok else "does not show it makes money"
    return out


def exit_mix(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        k = str(r.get("exit_reason") or "unrecorded")
        out[k] = out.get(k, 0) + 1
    return out


def ladder_moved(base_rows: list[dict], arm_rows: list[dict]) -> bool:
    """Manipulation check: an earlier TP1 must be reached more often, as a share
    of positions opened (a TP1 exit is one row; its position also has a row
    for the remainder)."""
    def share(rows):
        opened = len({(r.get("symbol"), r.get("entry_time")) for r in rows})
        tp1 = sum(1 for r in rows if r.get("exit_reason") == "TP1")
        return tp1 / opened if opened else None
    b, a = share(base_rows), share(arm_rows)
    return b is not None and a is not None and a > b


def limits_filled(run: dict) -> int:
    """Manipulation check for LV: limit entries that rested and filled. A fold
    whose count does not read adds nothing; the check asks for more than zero,
    so a run with no readable count fails it rather than passing."""
    total = 0
    for f in run.get("folds", []):
        v = (f.get("limits") or {}).get("filled")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            total += int(v)
    return total


def _rows(runs: Path, ds: str, arm: str) -> list[dict]:
    plain = runs / f"{ds}_{arm}.trades.json"
    raw = (plain.read_text() if plain.exists()
           else gzip.decompress((runs / f"{plain.name}.gz").read_bytes()).decode())
    return json.loads(raw)["trades"]


def report(runs: Path) -> dict:
    per: dict = {}
    rows: dict = {}
    raw_runs: dict = {}
    for ds in [*PRIMARY, CONFIRM]:
        per[ds], rows[ds], raw_runs[ds] = {}, {}, {}
        for arm in [CONTROL, *ARMS]:
            run = json.loads((runs / f"{ds}_{arm}.json").read_text())
            rows[ds][arm] = _rows(runs, ds, arm)
            raw_runs[ds][arm] = run
            per[ds][arm] = summary(run, rows[ds][arm]) | {"exits": exit_mix(rows[ds][arm])}
    base = {ds: per[ds][CONTROL]["folds"] for ds in PRIMARY}
    out: dict = {"per": per, "checks": {}, "q1": {}, "q2": {}}
    for arm in ARMS:
        if arm in LADDER_ARMS:
            moved = all(ladder_moved(rows[ds][CONTROL], rows[ds][arm])
                        for ds in [*PRIMARY, CONFIRM])
        else:
            moved = all(limits_filled(raw_runs[ds][arm]) > 0 for ds in [*PRIMARY, CONFIRM])
        out["checks"][arm] = moved
        cb, ca = per[CONFIRM][CONTROL]["mean_fold"], per[CONFIRM][arm]["mean_fold"]
        cdiff = None if cb is None or ca is None else ca - cb
        folds_arm = {ds: per[ds][arm]["folds"] for ds in PRIMARY}
        q1 = compare(base, folds_arm, cdiff)
        if not moved:
            q1["verdict"] = "no verdict: the arm did not change what it was meant to change"
        out["q1"][arm] = q1
        out["q2"][arm] = money(folds_arm, ca)
    for ds in [*PRIMARY, CONFIRM]:
        cells = "  ".join(f"{a}: {per[ds][a]['mean_fold']:+.2f}% n {per[ds][a]['positions']}"
                          for a in [CONTROL, *ARMS])
        print(f"{ds:14} {cells}")
    print("checks:", json.dumps(out["checks"]))
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
