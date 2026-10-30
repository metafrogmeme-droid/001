#!/usr/bin/env python3
"""Weekend reversal with maker costs, forward test, as pre-registered.

Design and rule: docs/research/prereg/weekend_reversal_forward_2026-10/PREREGISTRATION.md.
It reuses the earlier study's clock and P&L code (scripts/weekend_perps_study.py)
and its frozen universe.

    python3 scripts/weekend_reversal_forward.py status     # weekends so far, nothing fetched
    python3 scripts/weekend_reversal_forward.py fetch      # only after LAST_MONDAY
    python3 scripts/weekend_reversal_forward.py evaluate   # result.json + report.txt
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import weekend_perps_study as ws  # noqa: E402

DIR = ROOT / "docs" / "research" / "prereg" / "weekend_reversal_forward_2026-10"
DATA = DIR / "data"
FROZEN = ws.DATA / "manifest.json"
FIRST_FRIDAY = date(2026, 10, 30)
LAST_MONDAY = date(2027, 2, 15)
COST_PER_SIDE = 0.0004                    # maker 0.02% + 0.02% adverse selection
TAKER_REPORT = 0.0010
LOWER_PCT = 100 * 0.05 / 2 / 2            # 1.25th percentile: 95% two-sided, Bonferroni 2
RULES = {"R1": "H2", "R2": "H2_unhedged"}  # reversal, hedged and unhedged


def fridays() -> list[date]:
    out, f = [], FIRST_FRIDAY
    while f + timedelta(days=3) <= LAST_MONDAY:
        out.append(f)
        f += timedelta(days=7)
    return out


def judge(rows: list[dict]) -> dict:
    x = np.array([r["pnl"] for r in rows])
    out = {"n": int(len(x)), "mean": float(x.mean()) if len(x) else None,
           "lower": None, "passed": False}
    if len(x) >= 2:
        rng = np.random.default_rng(ws.SEED)
        draws = x[rng.integers(0, len(x), size=(ws.BOOT, len(x)))].mean(axis=1)
        out["lower"] = float(np.percentile(draws, LOWER_PCT))
    out["passed"] = bool(out["mean"] is not None and out["mean"] > 0
                         and out["lower"] is not None and out["lower"] > 0)
    return out


def cmd_status(today: date | None = None) -> int:
    today = today or datetime.now(timezone.utc).date()
    done = [f for f in fridays() if f + timedelta(days=3) < today]
    print(f"{len(done)} of {len(fridays())} weekends complete; evaluation allowed after "
          f"{LAST_MONDAY.isoformat()}")
    return 0


def cmd_fetch(today: date | None = None) -> int:
    today = today or datetime.now(timezone.utc).date()
    if today <= LAST_MONDAY:
        print(f"refusing: the window runs to {LAST_MONDAY.isoformat()}; fetch after it")
        return 2
    frozen = json.loads(FROZEN.read_text())
    DATA.mkdir(parents=True, exist_ok=True)
    start = int(datetime.combine(FIRST_FRIDAY - timedelta(days=1), datetime.min.time(),
                                 timezone.utc).timestamp() * 1000)
    end = int(datetime.combine(LAST_MONDAY + timedelta(days=1), datetime.min.time(),
                               timezone.utc).timestamp() * 1000)
    meta = {"fetched_utc": datetime.now(timezone.utc).isoformat(),
            "universe": frozen["universe"], "hedge": ws.HEDGE, "files": {}}
    for s in [ws.HEDGE, *frozen["universe"]]:
        path = DATA / f"{s}.json"
        path.write_text(json.dumps({"symbol": s, "candles": ws.fetch_candles(s, start, end),
                                    "funding": ws.fetch_funding(s, start)}))
        meta["files"][s] = hashlib.sha256(path.read_bytes()).hexdigest()
        print(s, flush=True)
    (DATA / "manifest.json").write_text(json.dumps(meta, indent=1))
    return 0


def evaluate(series: dict, universe: list[str], per_side: float) -> dict:
    res = ws.evaluate(series, universe, per_side=per_side, fridays=fridays(),
                      window_of=lambda _f: "forward")
    return {"rules": {r: judge(res["rows"][k]) for r, k in RULES.items()},
            "left_out": res["left_out"], "corr": res["corr"].get("forward"),
            "rows": {r: res["rows"][k] for r, k in RULES.items()}}


def cmd_evaluate() -> int:
    meta_path = DATA / "manifest.json"
    if not meta_path.exists():
        print("fetch first")
        return 2
    meta = json.loads(meta_path.read_text())
    for s, digest in meta["files"].items():
        if hashlib.sha256((DATA / f"{s}.json").read_bytes()).hexdigest() != digest:
            print(f"{s}: data changed since fetch")
            return 2
    series = {s: json.loads((DATA / f"{s}.json").read_text()) for s in meta["files"]}
    main = evaluate(series, meta["universe"], COST_PER_SIDE)
    taker = evaluate(series, meta["universe"], TAKER_REPORT)
    out = {"maker": main, "taker_reported_only": {r: v for r, v in taker["rules"].items()}}
    (DIR / "result.json").write_text(json.dumps(out, indent=1))
    lines = [f"left out: {main['left_out']}  corr(s, y): {main['corr']}"]
    for r, v in main["rules"].items():
        lines.append(f"{r}: n {v['n']}  mean {v['mean']}  lower {v['lower']}  "
                     f"{'PASS' if v['passed'] else 'FAIL'}")
    for r, v in taker["rules"].items():
        lines.append(f"{r} at taker (reported only): mean {v['mean']}")
    (DIR / "report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    raise SystemExit({"status": cmd_status, "fetch": cmd_fetch, "evaluate": cmd_evaluate}[cmd]())
