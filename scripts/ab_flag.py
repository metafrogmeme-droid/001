#!/usr/bin/env python3
"""Direct A/B for a dark FEATURE FLAG — the operator doctrine, automated.

House rule: "if we add new, do a direct A/B; if it improves, default on."
The voter ablation harness answers "what does muting this VOTER cost?", but
several shipped features are behavior TOGGLES, not voters — they change how
the electorate is read, what levels exist, or how labels are attributed, and
nothing could measure them. They stayed dark by default, which is honest but
also means their value was never realized.

This runs the SAME honest benchmark twice per flag — once with the flag off
(baseline), once on — and reports the delta with an explicit verdict rule.
Both arms share every other setting, so the only difference is the flag.

    python scripts/ab_flag.py --flags VP_TWOPASS_DIRECTION_ENABLED
    python scripts/ab_flag.py --all              # every known dark flag
    python scripts/ab_flag.py --all --walk-forward   # 6-fold OOS (the arbiter)

Verdict rule (deliberately conservative — a flag must EARN its default):
  - walk-forward is the arbiter: mean OOS return must improve by >= +0.15pp
    AND profitable folds must not drop. Single-window runs can only ever
    say "promising", never "flip it" — one window is not evidence.
  - anything else is "no change" and the flag stays dark, which is the
    correct outcome for most ideas.

Nothing here writes config. It prints what the evidence supports; flipping a
default stays a human decision, recorded in bot/config.py beside the flag.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STD_SYMBOLS = ",".join(f"{b}/USDT:USDT" for b in (
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX", "LTC", "BNB"))

# The flags this harness knows how to measure, with why each is dark. Keep in
# sync with the notes beside each flag in bot/config.py.
KNOWN_FLAGS = {
    "VP_TWOPASS_DIRECTION_ENABLED":
        "volume-profile vote deferred until the whole electorate has registered "
        "(kills order-dependence near net-zero)",
    "PATTERN_TARGET_LEVELS_ENABLED":
        "already-computed pattern objectives (fib ext, harmonic D, Wyckoff "
        "extremes, necklines) fed into the SL/TP snap map",
    "DIVERGENCE_SUBLABELS_ENABLED":
        "per-indicator divergence labels so each source is individually "
        "attributable and learnable",
    "ELLIOTT_CURRENT_WAVE_ENABLED":
        "partial-impulse reads expose the current wave number",
    "CROSS_ASSET_VOTER_ENABLED":
        "regime/DXY/alt-season context as confluence votes (1 of 2 horizons "
        "agreed in the 2026-07 IC study — needs a longer window)",
}

_PATS = {
    "return_pct": r"Total Return:\s*([+-]?[\d.]+)%",
    "trades": r"Total Trades:\s*(\d+)",
    "profit_factor": r"Profit Factor:\s*([\d.]+)",
    "sharpe": r"Sharpe Ratio:\s*([+-]?[\d.]+)",
    "win_pct": r"Winners:\s*\d+\s*\((\d+)%\)",
}
_WF = r"profitable folds (\d+)/(\d+) \| mean OOS ret ([+-]?[\d.]+)%"


def _parse(text: str, wf: bool) -> dict:
    out: dict = {}
    if wf:
        m = re.search(_WF, text)
        if m:
            out = {"profitable_folds": int(m.group(1)), "total_folds": int(m.group(2)),
                   "mean_oos_pct": float(m.group(3))}
        out["ok"] = bool(m)
        return out
    for k, p in _PATS.items():
        m = re.search(p, text)
        out[k] = float(m.group(1)) if m else None
    out["ok"] = out.get("return_pct") is not None
    return out


def _run(label: str, flag: str | None, out_dir: str, wf: bool, limit: int) -> dict:
    """One benchmark arm. flag=None is the baseline (every known flag forced off
    so a stray environment setting can't contaminate the comparison)."""
    path = os.path.join(out_dir, f"{label}.out")
    cmd = [sys.executable, "-m", "bot.backtest.runner", "--symbols", STD_SYMBOLS,
           "--fetch", "--limit", str(limit), "--timeframe", "1h", "--honest"]
    if wf:
        cmd += ["--walk-forward", "6"]
    env = {**os.environ}
    for f in KNOWN_FLAGS:            # both arms start from a clean, known state
        env[f] = "0"
    if flag:
        env[flag] = "1"
    print(f"[ab_flag] {label} ...", flush=True)
    with open(path, "w") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                              env=env, cwd=ROOT)
    parsed = _parse(open(path, errors="replace").read(), wf)
    parsed.update(arm=label, exit_code=proc.returncode)
    return parsed


def verdict(base: dict, arm: dict, wf: bool) -> str:
    """Conservative by construction: a flag must EARN its default flip."""
    if not arm.get("ok") or not base.get("ok"):
        return "run failed — no verdict"
    if wf:
        d = arm["mean_oos_pct"] - base["mean_oos_pct"]
        folds = arm["profitable_folds"] - base["profitable_folds"]
        if d >= 0.15 and folds >= 0:
            return f"FLIP ON: OOS {d:+.2f}pp, folds {folds:+d}"
        if d <= -0.15:
            return f"STAY DARK: OOS {d:+.2f}pp (hurts)"
        return f"STAY DARK: OOS {d:+.2f}pp (inside noise)"
    d = (arm["return_pct"] or 0) - (base["return_pct"] or 0)
    if d >= 0.15:
        return f"PROMISING (single window {d:+.2f}pp) — confirm with --walk-forward"
    if d <= -0.15:
        return f"STAY DARK: {d:+.2f}pp (hurts)"
    return f"STAY DARK: {d:+.2f}pp (inside noise)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--flags", default=None,
                    help="comma-separated flags to measure (default: --all set)")
    ap.add_argument("--all", action="store_true", help="measure every known dark flag")
    ap.add_argument("--walk-forward", action="store_true",
                    help="6-fold OOS — the only arm that can say FLIP ON")
    ap.add_argument("--limit", type=int, default=6000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.flags:
        flags = [f.strip().upper() for f in args.flags.split(",") if f.strip()]
    elif args.all:
        flags = list(KNOWN_FLAGS)
    else:
        ap.error("pass --flags NAME[,NAME...] or --all")
    unknown = [f for f in flags if f not in KNOWN_FLAGS]
    if unknown:
        print(f"unknown flag(s): {', '.join(unknown)}\nknown: {', '.join(KNOWN_FLAGS)}",
              file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out or os.path.join(ROOT, "data", "ab_flag", stamp)
    os.makedirs(out_dir, exist_ok=True)

    base = _run("baseline", None, out_dir, args.walk_forward, args.limit)
    results = []
    for f in flags:
        arm = _run(f.lower(), f, out_dir, args.walk_forward, args.limit)
        arm["flag"] = f
        arm["verdict"] = verdict(base, arm, args.walk_forward)
        results.append(arm)

    metric = "mean_oos_pct" if args.walk_forward else "return_pct"
    print("\n" + "=" * 78)
    print(f"FLAG A/B — {'6-fold walk-forward' if args.walk_forward else 'single window'}"
          f" · baseline {metric} = {base.get(metric)}")
    print("=" * 78)
    for r in sorted(results, key=lambda x: -(x.get(metric) or -1e9)):
        delta = (r.get(metric) or 0) - (base.get(metric) or 0)
        print(f"  {r['flag']:34} {delta:+7.2f}pp   {r['verdict']}")
    if not args.walk_forward:
        print("\nSingle-window results can only ever say PROMISING. Re-run the "
              "survivors with --walk-forward before flipping any default.")

    payload = {"generated_at": stamp, "walk_forward": args.walk_forward,
               "limit": args.limit, "baseline": base, "arms": results,
               "flag_notes": {f: KNOWN_FLAGS[f] for f in flags}}
    with open(os.path.join(out_dir, "results.json"), "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nwrote {os.path.join(out_dir, 'results.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
