#!/usr/bin/env python3
"""Long only behind BTC's trend, as sealed: judge the three tested arms.

Design and rule: docs/research/prereg/long_regime_2026-11/PREREGISTRATION.md. The runs
are the plan in its ``runs/plan.txt``. The rule is the timeframe-variants study's
(`scripts/timeframe_variants_study.py::judge`) at this study's Bonferroni share.

    python3 scripts/long_regime_study.py report
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import timeframe_variants_study as tv  # noqa: E402

DIR = ROOT / "docs/research/prereg/long_regime_2026-11"
RUNS = DIR / "runs"
TESTED = ("L1", "L2", "L3")
#: one-sided 2.5% shared by the three tested arms (Bonferroni)
LOWER_PCT = 2.5 / 3
REPORTED = ("R", "L2_100", "L2_200", "dev_majors_R", "dev_majors_L1", "dev_majors_L2",
            "dev_corr_R", "dev_corr_L1", "dev_corr_L2")


def btc_above_share(days: int = 150) -> dict:
    """Share of the fresh set's hours on which BTC's last closed daily close
    stood above its N-day mean (the L2 gate, before any idea is asked)."""
    from bot.backtest.snapshot import load_dataset
    btc = sorted(load_dataset(str(ROOT / "benchmark/fresh10b_btc_1d"))["BTC/USDT:USDT"],
                 key=lambda b: b.timestamp)
    closes = [(int(b.timestamp.timestamp() * 1000) + 86_400_000, b.close) for b in btc]
    hours = next(iter(load_dataset(str(ROOT / "benchmark/fresh10b_1h")).values()))
    above = read = 0
    for h in hours:
        t = int(h.timestamp.timestamp() * 1000) + 3_600_000
        done = [c for ts, c in closes if ts <= t]
        if len(done) < days:
            continue
        read += 1
        above += int(done[-1] > sum(done[-days:]) / days)
    return {"hours_read": read, "share_above": above / read if read else None}


def per_coin(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        if r.get("net_pnl_pct") is not None:
            out.setdefault(r["symbol"].split("/")[0], []).append(float(r["net_pnl_pct"]))
    return {k: {"n": len(v), "mean": sum(v) / len(v)} for k, v in sorted(out.items())}


def report() -> dict:
    tv.LOWER_PCT = LOWER_PCT
    res: dict = {}
    for arm in TESTED + REPORTED:
        r = json.loads((RUNS / f"{arm}.json").read_text())
        rows = json.loads((RUNS / f"{arm}.trades.json").read_text())["trades"]
        folds = [float(f["return_pct"]) for f in sorted(r["folds"], key=lambda f: str(f["oos_start"]))]
        j = tv.judge(rows, folds)
        j.update(tv.describe(rows), folds=folds, pf=r.get("pooled", {}).get("pf"),
                 code_sha=r.get("code_sha"), per_coin=per_coin(rows),
                 longs=sum(1 for x in rows if x.get("direction") == "LONG"),
                 shorts=sum(1 for x in rows if x.get("direction") == "SHORT"))
        if arm not in TESTED:
            j.pop("makes_money", None)
            j["verdict"] = "reported, deciding nothing"
        res[arm] = j
    for arm in TESTED + ("L2_100", "L2_200"):
        if res[arm]["mean"] is not None and res["R"]["mean"] is not None:
            res[arm]["vs_R"] = res[arm]["mean"] - res["R"]["mean"]
    res["btc_above_150d"] = btc_above_share()
    (DIR / "result.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    for arm in TESTED + REPORTED:
        j = res[arm]
        print(f"{arm:14} n={j['n']:4d} L/S={j['longs']}/{j['shorts']} mean={j['mean']} "
              f"lower={j['lower']} fold_mean={j['fold_mean']} pf={j['pf']} | {j['verdict']}")
    print("BTC above 150d:", res["btc_above_150d"])
    return res


if __name__ == "__main__":
    report()
