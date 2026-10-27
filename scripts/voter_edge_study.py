#!/usr/bin/env python3
"""Every voter's directional edge, discovery against holdout, as pre-registered.

Reads files written by `scripts/signal_edge.py collect`. Windows, the family,
the correction and the pass rule are in
docs/research/prereg/voter_edge_2026-10/PREREGISTRATION.md.

Usage:
    python3 scripts/voter_edge_study.py --discovery a.json,b.json --holdout c.json,d.json
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

HORIZONS = (1, 4, 12, 24, 48)
BOOT = 4000
SEED = 7
FDR = 0.10
DISCOVERY_UNTIL = "2026-05-22T02:00:00+00:00"
HOLDOUT_FROM = "2026-07-06T12:00:00+00:00"
MIN_CALLS_DISC, MIN_WEEKS_DISC = 200, 8
MIN_CALLS_HOLD, MIN_WEEKS_HOLD = 100, 4


def week_key(ts: str) -> str:
    y, w, _ = datetime.fromisoformat(ts).isocalendar()
    return f"{y}-{w:02d}"


def excess_sums(path: str, window: str, edge: float) -> dict[str, dict[int, dict[str, list]]]:
    """{voter: {horizon: {week: [sum of excess, count]}}} for one collected file."""
    d = json.loads(Path(path).read_text())
    if "calls" not in d:
        raise SystemExit(f"{path}: collected before voters were recorded")
    unc = {dr: {int(h): v for h, v in by.items()}
           for dr, by in d["calls_unconditional"].items()}
    out: dict[str, dict[int, dict[str, list]]] = collections.defaultdict(
        lambda: collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0])))
    for c in d["calls"]:
        t = datetime.fromisoformat(c["ts"]).timestamp()
        if (window == "discovery" and t >= edge) or (window == "holdout" and t < edge):
            continue
        wk = week_key(c["ts"])
        moves = {int(h): m for h, m in c["moves"].items()}
        for name, vote in c["votes"].items():
            side = "LONG" if vote > 0 else "SHORT"
            sign = 1.0 if vote > 0 else -1.0
            for h in HORIZONS:
                if h in moves and h in unc.get(side, {}):
                    acc = out[name][h][wk]
                    acc[0] += sign * moves[h] - unc[side][h]
                    acc[1] += 1
    return out


def merge(parts: list[dict]) -> dict:
    """Combine files; the same calendar week is one cluster across files."""
    out: dict[str, dict[int, dict[str, list]]] = collections.defaultdict(
        lambda: collections.defaultdict(lambda: collections.defaultdict(lambda: [0.0, 0])))
    for p in parts:
        for name, by_h in p.items():
            for h, by_wk in by_h.items():
                for wk, (s, n) in by_wk.items():
                    out[name][h][wk][0] += s
                    out[name][h][wk][1] += n
    return out


def boot_mean(weeks: dict[str, list], boot: int, rng: np.random.Generator
              ) -> tuple[float, float, float, float, int, int]:
    """(mean excess, 95% low, 95% high, two-sided p, calls, weeks)."""
    arr = np.array([[s, n] for s, n in weeks.values()], dtype=float)
    k = len(arr)
    calls = int(arr[:, 1].sum()) if k else 0
    if k < 2 or calls == 0:
        return float("nan"), float("nan"), float("nan"), float("nan"), calls, k
    counts = rng.multinomial(k, np.full(k, 1.0 / k), size=boot)
    sums = counts @ arr
    ok = sums[:, 1] > 0
    dist = sums[ok, 0] / sums[ok, 1]
    lo, hi = np.percentile(dist, [2.5, 97.5])
    p = 2.0 * min((np.sum(dist <= 0) + 1) / (len(dist) + 1),
                  (np.sum(dist >= 0) + 1) / (len(dist) + 1))
    return float(arr[:, 0].sum() / arr[:, 1].sum()), float(lo), float(hi), float(min(1.0, p)), calls, k


def bh_pass(pvals: list[float], q: float) -> list[bool]:
    m = len(pvals)
    order = sorted(range(m), key=lambda i: (np.isnan(pvals[i]), pvals[i]))
    cutoff = -1
    for rank, i in enumerate(order, start=1):
        if not np.isnan(pvals[i]) and pvals[i] <= q * rank / m:
            cutoff = rank
    return [rank <= cutoff for rank, _ in
            sorted(((r, i) for r, i in enumerate(order, start=1)), key=lambda t: t[1])]


def study(disc: dict, hold: dict, boot: int = BOOT, seed: int = SEED) -> list[dict]:
    rng = np.random.default_rng(seed)
    rows = []
    for name in sorted(disc):
        for h in HORIZONS:
            weeks = disc[name].get(h)
            if not weeks:
                continue
            m, lo, hi, p, n, k = boot_mean(weeks, boot, rng)
            if n < MIN_CALLS_DISC or k < MIN_WEEKS_DISC or not np.isfinite(p):
                continue
            row = {"voter": name, "h": h, "disc": m, "disc_lo": lo, "disc_hi": hi,
                   "p": p, "n_disc": n, "weeks_disc": k}
            hw = hold.get(name, {}).get(h)
            if hw:
                hm, hlo, hhi, _, hn, hk = boot_mean(hw, boot, rng)
                row.update(hold=hm, hold_lo=hlo, hold_hi=hhi, n_hold=hn, weeks_hold=hk)
            rows.append(row)
    keep = bh_pass([r["p"] for r in rows], FDR)
    for r, k in zip(rows, keep):
        r["bh"] = k
        sign = np.sign(r["disc"])
        sig_disc = bool(k and (r["disc_lo"] > 0 or r["disc_hi"] < 0))
        powered = r.get("n_hold", 0) >= MIN_CALLS_HOLD and r.get("weeks_hold", 0) >= MIN_WEEKS_HOLD
        same = bool(powered and np.sign(r["hold"]) == sign
                    and (r["hold_lo"] > 0 or r["hold_hi"] < 0))
        r["verdict"] = ("PASS" if sign > 0 else "HARMFUL") if (sig_disc and same) else "fail"
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--discovery", required=True)
    ap.add_argument("--holdout", required=True)
    ap.add_argument("--boot", type=int, default=BOOT)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    cut_d = datetime.fromisoformat(DISCOVERY_UNTIL).timestamp()
    cut_h = datetime.fromisoformat(HOLDOUT_FROM).timestamp()
    disc = merge([excess_sums(p, "discovery", cut_d) for p in args.discovery.split(",")])
    hold = merge([excess_sums(p, "holdout", cut_h) for p in args.holdout.split(",")])
    rows = study(disc, hold, args.boot)
    print(f"{len(rows)} (voter, horizon) tests; Benjamini-Hochberg q={FDR}; "
          f"bootstrap {args.boot} draws over calendar weeks, seed {SEED}")
    print(f"{'voter':20} {'h':>3} {'discovery':>24} {'holdout':>24} {'nD':>6} {'nH':>6} BH  verdict")
    for r in sorted(rows, key=lambda r: -abs(r["disc"])):
        h = (f"{r['hold']:+.3f} [{r['hold_lo']:+.3f},{r['hold_hi']:+.3f}]"
             if "hold" in r else "n/a")
        print(f"{r['voter']:20} {r['h']:>3} "
              f"{r['disc']:+.3f} [{r['disc_lo']:+.3f},{r['disc_hi']:+.3f}] {h:>24} "
              f"{r['n_disc']:>6} {r.get('n_hold', 0):>6} {'y' if r['bh'] else 'n':>3}  {r['verdict']}")
    print(f"\n{sum(r['verdict'] == 'PASS' for r in rows)} pass, "
          f"{sum(r['verdict'] == 'HARMFUL' for r in rows)} replicate as harmful, "
          f"of {len(rows)} tests")
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
