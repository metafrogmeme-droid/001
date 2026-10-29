#!/usr/bin/env python3
"""Learned voter weights against the hand-tuned ones, as pre-registered.

The design, windows and decision rule are in
docs/research/prereg/voter_weights_2026-10/PREREGISTRATION.md.

    python3 scripts/voter_weights_test.py fit-runs --out DIR   # discovery, default arm
    python3 scripts/voter_weights_test.py fit --out DIR        # fit + hash the weights
    python3 scripts/voter_weights_test.py test-runs --out DIR  # holdout, both arms
    python3 scripts/voter_weights_test.py report --out DIR

Each run gets its own empty state directory, so the two arms differ only in the
flag and the learned arm's weight file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import risk_sweep as rs  # noqa: E402

DISCOVERY = tuple(d for d, w in rs.STAGE1 if w == "discovery")
HOLDOUT = tuple(d for d, w in rs.STAGE1 if w == "holdout")
ARMS = ("default", "learned")
WEIGHTS = "voter_weights.json"


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


def run_one(out: Path, dataset: str, arm: str, trades: bool = False) -> int:
    path = rs.out_path(out, dataset, arm)
    if path.exists() and path.stat().st_size > 0:
        return 0
    state = out / "state" / f"{dataset}__{arm}"
    if state.exists():
        shutil.rmtree(state)
    (state / "learning").mkdir(parents=True)
    env = {**os.environ, "RUNECLAW_STATE_DIR": str(state),
           "VOTER_WEIGHT_LEARNING_ENABLED": "0"}
    if arm == "learned":
        shutil.copyfile(out / WEIGHTS, state / "learning" / WEIGHTS)
        env["VOTER_WEIGHT_LEARNING_ENABLED"] = "1"
    cmd = [sys.executable, "-m", "bot.backtest.runner", "--dataset", f"benchmark/{dataset}",
           "--honest", "--breaker-reset-bars", "24", "--walk-forward", "6", "-o", str(path)]
    if trades:
        cmd += ["--trades-out", str(out / f"{dataset}__{arm}__trades.json")]
    done = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
    return done.returncode


def samples(rows: list[dict], until: str) -> tuple[list, dict]:
    """``(votes, direction, won)`` for positions that closed by ``until``,
    in exit order, with counts of what was left out and why."""
    kept, skipped = [], {"after_window": 0, "no_votes": 0, "no_return": 0}
    for r in sorted(rows, key=lambda r: r["exit_time"]):
        if _ts(r["exit_time"]) > _ts(until):
            skipped["after_window"] += 1
        elif not r.get("voter_votes"):
            skipped["no_votes"] += 1
        elif r.get("net_pnl_pct") is None:
            skipped["no_return"] += 1
        else:
            kept.append(([tuple(v) for v in r["voter_votes"]], r["direction"],
                         r["net_pnl_pct"] > 0))
    return kept, skipped


def fit(out: Path) -> dict:
    from bot.learning.voter_weights import VoterWeightLearner
    allrows = []
    for d in DISCOVERY:
        allrows += json.loads((out / f"{d}__default__trades.json").read_text())["trades"]
    train, skipped = samples(allrows, rs.DISCOVERY_END)
    learner = VoterWeightLearner().fit(train)
    path = out / WEIGHTS
    learner.save(str(path))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (out / f"{WEIGHTS}.sha256").write_text(f"{digest}  {WEIGHTS}\n")
    summary = {"n_train": len(train), "skipped": skipped, "ready": learner.is_ready(),
               "multipliers": learner.to_dict().get("mult", {}),
               "validate_oos": learner.validate_oos(train), "sha256": digest}
    (out / "fit_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary


def ranking(out: Path) -> dict:
    """The learner's own AUC check on holdout default-arm positions."""
    from bot.learning.voter_weights import VoterWeightLearner, trade_ranking
    learner = VoterWeightLearner.load(str(out / WEIGHTS))
    rows = []
    for d in HOLDOUT:
        p = out / f"{d}__default__trades.json"
        if p.exists():
            rows += [r for r in json.loads(p.read_text())["trades"]
                     if _ts(r["entry_time"]) >= _ts(rs.HOLDOUT_START)]
    test, _ = samples(rows, "9999-12-31T00:00:00+00:00")
    return trade_ranking(test, learner.multiplier)


def report(out: Path) -> None:
    fs = json.loads((out / "fit_summary.json").read_text())
    print(f"fit: {fs['n_train']} discovery positions, skipped {fs['skipped']}, "
          f"ready={fs['ready']}, weights sha256 {fs['sha256'][:12]}")
    mult = fs["multipliers"]
    moved = sorted(mult.items(), key=lambda kv: abs(kv[1] - 1.0), reverse=True)
    print("largest multipliers:", ", ".join(f"{k} {v:.2f}" for k, v in moved[:8]))
    print(f"validate_oos on discovery: {fs['validate_oos']}\n")
    per = {}
    for d in HOLDOUT:
        base, arm = rs.load(out, d, "default"), rs.load(out, d, "learned")
        if base is None or arm is None:
            print(f"{d}: missing runs")
            return
        per[d] = ("holdout", rs.paired(base, arm, "holdout"))
        trades = {a: sum(int(f["trades"]) for f in rs.window_folds(r, "holdout").values())
                  for a, r in (("default", base), ("learned", arm))}
        print(f"{d}: holdout trades default {trades['default']}, learned {trades['learned']}")
    j = rs.judge_stage1(per)
    cells = "  ".join(f"{d} {j['means'][d]:+.2f}" for d in HOLDOUT)
    print(f"\nlearned - default, per fold (pp): {cells}")
    print(f"holdout mean {j['holdout_mean']:+.2f}, drawdown not worse: {j['holdout_dd_not_worse']}, "
          f"mean {j['mean']:+.2f} [95% {j['lo']:+.2f}, {j['hi']:+.2f}]")
    print(f"rule1 {j['rule1']}  rule2 {j['rule2']}  rule3 {j['rule3']}  ->  "
          f"{'PASS' if j['passed'] else 'FAIL'}")
    print(f"\nreported only: holdout trade_ranking {ranking(out)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["fit-runs", "fit", "test-runs", "report"])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if a.cmd == "fit-runs":
        work = [(d, "default") for d in DISCOVERY]
        trades = True
    elif a.cmd == "test-runs":
        if not (a.out / f"{WEIGHTS}.sha256").exists():
            print("fit first: the weights must be fixed before any holdout run")
            return 2
        work = [(d, arm) for d in HOLDOUT for arm in ARMS]
        trades = True
    elif a.cmd == "fit":
        print(json.dumps(fit(a.out), indent=1, default=str))
        return 0
    else:
        report(a.out)
        return 0
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        for (d, arm), rc in zip(work, pool.map(lambda w: run_one(a.out, *w, trades=trades), work)):
            print(f"{d} {arm} rc={rc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
