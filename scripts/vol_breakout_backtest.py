#!/usr/bin/env python3
"""Volatility breakout with momentum confirmation: an offline, pre-registered test.

Rules, windows and the pass rule are fixed in
docs/research/prereg/vol_breakout_2026-10/PREREGISTRATION.md and are not tuned
here. This script places nothing and is not connected to the bot.

Entry (signal at the close of bar t, fill at the open of t+1):
    long  : close above the prior 20 bars' highest high, range / ATR14 >= 1.5,
            volatility-scaled 6-bar momentum > 0
    short : the mirror
Exit: 2 x ATR stop, 4 x ATR target (1:2), 48-bar time stop; stop wins a tie.
Size: risk 1% of the balance over the 2 x ATR stop distance, capped at 1x notional.

Usage:
    python3 scripts/vol_breakout_backtest.py
    python3 scripts/vol_breakout_backtest.py --out /tmp/vb.json --null-draws 50
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import signal_research as sr  # noqa: E402

SYMBOLS = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
DISCOVERY = "benchmark/corr_dense_1h"
DISCOVERY_UNTIL = "2026-05-22T02:00:00+00:00"
HOLDOUT = "benchmark/majors_1h_v3"
HOLDOUT_FROM = "2026-07-06T12:00:00+00:00"
BOOT = 2000
SEED = 7
NULL_DRAWS = 200
MIN_HOLDOUT_TRADES = 30


@dataclass(frozen=True)
class Params:
    donchian: int = 20
    range_min: float = 1.5
    atr_stop: float = 2.0
    atr_target: float = 4.0
    max_hold: int = 48
    risk_frac: float = 0.01
    max_notional: float = 1.0
    fee: float = 0.0006
    slip: float = 0.0005
    balance: float = 10_000.0


# ── signals (bar t uses bars at or before t only) ──────────────────────────

def signals(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray,
            v: np.ndarray, p: Params) -> dict[str, np.ndarray]:
    """+1 long, -1 short, 0 none per bar, with the ATR14 at that bar."""
    n = len(c)
    f = sr.features(o, h, lo, c, v)
    a = sr.atr(h, lo, c)
    prior_hi = np.full(n, np.nan)
    prior_lo = np.full(n, np.nan)
    if n > p.donchian:
        prior_hi[p.donchian:] = sr._windows(h, p.donchian).max(axis=1)[:-1]
        prior_lo[p.donchian:] = sr._windows(lo, p.donchian).min(axis=1)[:-1]
    mom = f["volmom6"]
    expanded = f["rngexp"] >= p.range_min
    side = np.zeros(n, dtype=int)
    side[(c > prior_hi) & expanded & (mom > 0)] = 1
    side[(c < prior_lo) & expanded & (mom < 0)] = -1
    side[~np.isfinite(a)] = 0
    return {"side": side, "atr": a}


# ── one trade ──────────────────────────────────────────────────────────────

def position_size(balance: float, entry: float, stop_dist: float, p: Params) -> tuple[float, float]:
    """(quantity, realised risk as a fraction of balance) for one trade."""
    if not (entry > 0 and stop_dist > 0 and balance > 0):
        return 0.0, 0.0
    qty = p.risk_frac * balance / stop_dist
    cap = p.max_notional * balance / entry
    qty = min(qty, cap)
    return qty, qty * stop_dist / balance


def run_trade(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, e: int,
              side: int, atr_sig: float, balance: float, p: Params) -> dict | None:
    """Fill at the open of bar e, then walk forward. None if it cannot be sized."""
    n = len(c)
    entry = o[e] * (1.0 + side * p.slip)
    stop_dist = p.atr_stop * atr_sig
    qty, risk_frac = position_size(balance, entry, stop_dist, p)
    if qty <= 0:
        return None
    stop = entry - side * stop_dist
    target = entry + side * p.atr_target * atr_sig
    exit_px, exit_i, reason = None, None, ""
    last = min(e + p.max_hold, n) - 1
    for b in range(e, last + 1):
        if side == 1:
            if o[b] <= stop or lo[b] <= stop:
                exit_px, reason = min(o[b], stop), "stop"
            elif o[b] >= target or h[b] >= target:
                exit_px, reason = max(o[b], target), "target"
        else:
            if o[b] >= stop or h[b] >= stop:
                exit_px, reason = max(o[b], stop), "stop"
            elif o[b] <= target or lo[b] <= target:
                exit_px, reason = min(o[b], target), "target"
        if exit_px is not None:
            exit_i = b
            break
    if exit_px is None:
        if last + 1 < n:
            exit_i, exit_px, reason = last + 1, o[last + 1], "time"
        else:
            return None  # the data ended with the position open: not a result
    exit_fill = exit_px * (1.0 - side * p.slip)
    gross = side * qty * (exit_fill - entry)
    fees = p.fee * qty * (entry + exit_fill)
    net = gross - fees
    risk_amt = p.risk_frac * balance
    return {"entry_i": e, "exit_i": exit_i, "side": side, "reason": reason,
            "net": net, "r": net / risk_amt, "risk_frac": risk_frac,
            "ret": net / balance, "bars": exit_i - e + 1}


def simulate(s: dict, sig: dict[str, np.ndarray], p: Params, start: int, stop_at: int,
             entries: list[tuple[int, int]] | None = None) -> list[dict]:
    """Trades whose signal bar is in [start, stop_at). `entries` replaces the
    rule's signals with (bar, side) pairs, for the random-entry control."""
    o, h, lo, c = s["o"], s["h"], s["l"], s["c"]
    n = min(len(c), stop_at + p.max_hold + 2)
    if entries is None:
        pend = {t: int(sig["side"][t]) for t in range(start, min(stop_at, n - 1))
                if sig["side"][t] != 0}
    else:
        pend = {t: d for t, d in entries if start <= t < min(stop_at, n - 1)}
    out: list[dict] = []
    balance = p.balance
    t = start
    while t < min(stop_at, n - 1):
        side = pend.get(t, 0)
        if side == 0 or not np.isfinite(sig["atr"][t]):
            t += 1
            continue
        trade = run_trade(o[:n], h[:n], lo[:n], c[:n], t + 1, side, float(sig["atr"][t]),
                          balance, p)
        if trade is None:
            t += 1
            continue
        trade["ts"] = float(s["ts"][t + 1])
        balance += trade["net"]
        trade["balance"] = balance
        out.append(trade)
        t = trade["exit_i"]
    return out


# ── statistics ─────────────────────────────────────────────────────────────

def week_of(ts: float) -> str:
    y, w, _ = datetime.fromtimestamp(ts).isocalendar()
    return f"{y}-{w:02d}"


def summarize(trades: list[dict], p: Params) -> dict:
    if not trades:
        return {"trades": 0}
    r = np.array([t["r"] for t in trades])
    wins, loss = r[r > 0].sum(), -r[r < 0].sum()
    bal = np.array([p.balance] + [t["balance"] for t in trades])
    peak = np.maximum.accumulate(bal)
    return {"trades": len(trades), "expectancy_r": float(r.mean()),
            "win_rate": float((r > 0).mean()),
            "profit_factor": float(wins / loss) if loss > 0 else None,
            "max_drawdown_pct": float(((peak - bal) / peak).max() * 100.0),
            "mean_risk_pct": float(np.mean([t["risk_frac"] for t in trades]) * 100.0)}


def week_interval(rows: list[tuple[str, float]], boot: int,
                  rng: np.random.Generator) -> tuple[float, float, float]:
    """Mean R with a 95% interval, bootstrapping whole ISO weeks."""
    if not rows:
        return float("nan"), float("nan"), float("nan")
    weeks = sorted({w for w, _ in rows})
    agg = np.array([[sum(r for ww, r in rows if ww == w),
                     sum(1 for ww, _ in rows if ww == w)] for w in weeks], dtype=float)
    k = len(weeks)
    counts = rng.multinomial(k, np.full(k, 1.0 / k), size=boot)
    sums = counts @ agg
    ok = sums[:, 1] > 0
    dist = sums[ok, 0] / sums[ok, 1]
    lo, hi = np.percentile(dist, [2.5, 97.5])
    return float(agg[:, 0].sum() / agg[:, 1].sum()), float(lo), float(hi)


def random_entries(n_entries: int, start: int, stop: int,
                   rng: np.random.Generator) -> list[tuple[int, int]]:
    """Uniform random bars and sides; the simulator skips those inside a position."""
    if stop - start < 2 or n_entries < 1:
        return []
    bars = np.sort(rng.integers(start, stop - 1, size=n_entries))
    sides = rng.choice(np.array([-1, 1]), size=n_entries)
    return list(zip(bars.tolist(), sides.tolist()))


def run_window(series: list[dict], p: Params, window: str, edge_ts: float, boot: int,
               null_draws: int, rng: np.random.Generator) -> dict:
    """Rule trades, per symbol and pooled, plus the random-entry control."""
    per, all_rows, spans, counts = {}, [], {}, {}
    for s in series:
        sig = signals(s["o"], s["h"], s["l"], s["c"], s["v"], p)
        if window == "discovery":
            start = int(np.argmax(np.isfinite(sig["atr"])))
            stop = int(np.searchsorted(s["ts"], edge_ts)) - p.max_hold - 2
        else:
            start = int(np.searchsorted(s["ts"], edge_ts))
            stop = len(s["ts"]) - 1
        trades = simulate(s, sig, p, start, stop)
        per[s["symbol"]] = summarize(trades, p)
        all_rows += [(week_of(t["ts"]), t["r"]) for t in trades]
        spans[s["symbol"]] = (sig, start, stop)
        counts[s["symbol"]] = len(trades)
    mean, lo, hi = week_interval(all_rows, boot, rng)
    null_means = []
    for _ in range(null_draws):
        rs = []
        for s in series:
            sig, start, stop = spans[s["symbol"]]
            ent = random_entries(max(1, counts[s["symbol"]]) * 3, start, stop, rng)
            got = simulate(s, sig, p, start, stop, entries=ent)
            keep = rng.permutation(len(got))[:counts[s["symbol"]]]
            rs += [got[i]["r"] for i in keep]
        if rs:
            null_means.append(float(np.mean(rs)))
    pooled_r = np.array([r for _, r in all_rows])
    return {"per_symbol": per, "trades": len(all_rows),
            "expectancy_r": mean, "ci_lo": lo, "ci_hi": hi,
            "win_rate": float((pooled_r > 0).mean()) if len(pooled_r) else None,
            "null_p95": float(np.percentile(null_means, 95)) if null_means else None,
            "null_mean": float(np.mean(null_means)) if null_means else None}


def verdict(disc: dict, hold: dict) -> dict:
    ci_ok = bool(disc["trades"] and np.isfinite(disc["ci_lo"]) and disc["ci_lo"] > 0)
    null_ok = bool(disc["null_p95"] is not None and disc["expectancy_r"] > disc["null_p95"])
    powered = hold["trades"] >= MIN_HOLDOUT_TRADES
    hold_ok = bool(powered and hold["expectancy_r"] > 0)
    return {"discovery_ci_above_zero": ci_ok, "discovery_beats_random_p95": null_ok,
            "holdout_has_30_trades": powered, "holdout_positive": hold_ok,
            "passed": ci_ok and null_ok and hold_ok}


def load(dataset: str, symbols: tuple[str, ...]) -> list[dict]:
    return [s for s in sr.load_arrays(dataset) if s["symbol"] in symbols]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--discovery", default=DISCOVERY)
    ap.add_argument("--discovery-until", default=DISCOVERY_UNTIL)
    ap.add_argument("--holdout", default=HOLDOUT)
    ap.add_argument("--holdout-from", default=HOLDOUT_FROM)
    ap.add_argument("--boot", type=int, default=BOOT)
    ap.add_argument("--null-draws", type=int, default=NULL_DRAWS)
    ap.add_argument("--max-notional", type=float, default=Params.max_notional,
                    help="sensitivity only; the registered value is 1.0")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    p = Params(max_notional=args.max_notional)
    rng = np.random.default_rng(SEED)
    disc_s, hold_s = load(args.discovery, SYMBOLS), load(args.holdout, SYMBOLS)
    cut_d = datetime.fromisoformat(args.discovery_until).timestamp()
    cut_h = datetime.fromisoformat(args.holdout_from).timestamp()
    disc = run_window(disc_s, p, "discovery", cut_d, args.boot, args.null_draws, rng)
    hold = run_window(hold_s, p, "holdout", cut_h, args.boot, args.null_draws, rng)
    res = verdict(disc, hold)
    for name, w in (("DISCOVERY", disc), ("HOLDOUT", hold)):
        print(f"\n{name}: {w['trades']} trades  expectancy {w['expectancy_r']:+.3f}R "
              f"[{w['ci_lo']:+.3f},{w['ci_hi']:+.3f}]  win {w['win_rate']}  "
              f"random-entry mean {w['null_mean']} p95 {w['null_p95']}")
        for sym, r in w["per_symbol"].items():
            print(f"  {sym:16} {r}")
    print(f"\nverdict: {res}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(
            {"params": p.__dict__, "discovery": disc, "holdout": hold, "verdict": res},
            indent=2, default=float))
        print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
