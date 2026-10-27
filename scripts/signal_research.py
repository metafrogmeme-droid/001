#!/usr/bin/env python3
"""Offline research harness: does a candidate feature predict the forward move?

Scores candidate voter features computed from PAST 1h OHLCV only, against the
forward ATR-scaled return, on a discovery window and a separate holdout
window, before any candidate becomes a voter. It never calls the analyzer.
The design is fixed in docs/research/prereg/voter_candidates_2026-10/.

Statistic: within each symbol the feature and the target are rank-transformed
over the window, so the pooled correlation is a Spearman net of each symbol's
own drift. Samples are taken every h bars so forward windows do not overlap.
Intervals are a percentile cluster bootstrap over (dataset, symbol, ISO week).
The discovery window ends h bars before its cutoff, so no forward window
crosses into the holdout.

Usage:
    python3 scripts/signal_research.py
    python3 scripts/signal_research.py --out /tmp/research.json --boot 500
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HORIZONS = (4, 12, 24)
ATR_N = 14
BOOT = 2000
SEED = 7
FDR = 0.10
COST_ATR = 0.10
COMPARATOR_CAP = 0.90
COMPARATORS = ("rsi14", "macd_hist")

DISCOVERY = "benchmark/corr_dense_1h"
DISCOVERY_UNTIL = "2026-05-22T02:00:00+00:00"
HOLDOUT = "benchmark/majors_1h_v3,benchmark/alts_1h_v3"
HOLDOUT_FROM = "2026-07-06T12:00:00+00:00"

# Cluster sums: n, sx, sy, sxx, syy, sxy, top n, top sy, bottom n, bottom sy.
_COLS = 10
_TOP = 0.3


# ── rolling building blocks (each value uses only bars at or before t) ──────

def _windows(x: np.ndarray, w: int) -> np.ndarray:
    return np.lib.stride_tricks.sliding_window_view(x, w)


def _pad(vals: np.ndarray, n: int) -> np.ndarray:
    out = np.full(n, np.nan)
    out[n - len(vals):] = vals
    return out


def rmean(x: np.ndarray, w: int) -> np.ndarray:
    if len(x) < w:
        return np.full(len(x), np.nan)
    return _pad(_windows(x, w).mean(axis=1), len(x))


def rstd(x: np.ndarray, w: int) -> np.ndarray:
    if len(x) < w:
        return np.full(len(x), np.nan)
    return _pad(_windows(x, w).std(axis=1), len(x))


def rsum(x: np.ndarray, w: int) -> np.ndarray:
    if len(x) < w:
        return np.full(len(x), np.nan)
    return _pad(_windows(x, w).sum(axis=1), len(x))


def _moment(x: np.ndarray, w: int, power: int) -> np.ndarray:
    """Standardised rolling moment (skew for 3, excess kurtosis for 4)."""
    if len(x) < w:
        return np.full(len(x), np.nan)
    win = _windows(x, w)
    mu = win.mean(axis=1, keepdims=True)
    sd = win.std(axis=1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(sd > 0, (win - mu) / sd, np.nan)
    m = (z ** power).mean(axis=1)
    return _pad(m - 3.0 if power == 4 else m, len(x))


def rpct(x: np.ndarray, w: int) -> np.ndarray:
    """Share of the last w values at or below the current one."""
    if len(x) < w:
        return np.full(len(x), np.nan)
    win = _windows(x, w)
    cur = win[:, -1:]
    out = (win <= cur).mean(axis=1)
    bad = np.isnan(win).any(axis=1)
    out[bad] = np.nan
    return _pad(out, len(x))


def ema(x: np.ndarray, span: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    alpha = 2.0 / (span + 1.0)
    prev = np.nan
    for i, v in enumerate(x):
        if np.isnan(v):
            continue
        prev = v if np.isnan(prev) else alpha * v + (1.0 - alpha) * prev
        out[i] = prev
    return out


def atr(h: np.ndarray, lo: np.ndarray, c: np.ndarray, n: int = ATR_N) -> np.ndarray:
    pc = np.concatenate(([c[0]], c[:-1]))
    tr = np.maximum.reduce([h - lo, np.abs(h - pc), np.abs(lo - pc)])
    return rmean(tr, n)


def rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    d = np.diff(c)
    up = np.clip(d, 0.0, None)
    dn = np.clip(-d, 0.0, None)
    au, ad = up[:n].mean(), dn[:n].mean()
    for i in range(n, len(c)):
        if i > n:
            au = (au * (n - 1) + up[i - 1]) / n
            ad = (ad * (n - 1) + dn[i - 1]) / n
        out[i] = 100.0 if ad == 0 else 100.0 - 100.0 / (1.0 + au / ad)
    return out


def features(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray,
             v: np.ndarray) -> dict[str, np.ndarray]:
    """Every candidate and comparator, aligned to the bars, past data only."""
    n = len(c)
    lr = np.log(c)
    r1 = np.concatenate(([np.nan], np.diff(lr)))
    rv24 = rstd(np.where(np.isnan(r1), 0.0, r1), 24)
    rv24 = np.where(np.arange(n) < 24, np.nan, rv24)
    a = atr(h, lo, c)
    feats: dict[str, np.ndarray] = {}
    for k in (6, 24, 72, 168):
        mom = np.full(n, np.nan)
        mom[k:] = lr[k:] - lr[:-k]
        feats[f"mom{k}"] = mom
        with np.errstate(divide="ignore", invalid="ignore"):
            feats[f"volmom{k}"] = np.where(rv24 > 0, mom / (rv24 * np.sqrt(k)), np.nan)
    feats["rev1"] = -r1
    mom4 = np.full(n, np.nan)
    mom4[4:] = lr[4:] - lr[:-4]
    feats["rev4"] = -mom4
    with np.errstate(divide="ignore", invalid="ignore"):
        sd48 = rstd(c, 48)
        feats["zma48"] = np.where(sd48 > 0, (c - rmean(c, 48)) / sd48, np.nan)
        feats["rvpct"] = rpct(rv24, 500)
        m72 = rmean(rv24, 72)
        feats["vov"] = np.where(m72 > 0, rstd(rv24, 72) / m72, np.nan)
        feats["rngexp"] = np.where(a > 0, (h - lo) / a, np.nan)
        vol24 = rsum(v, 24)
        feats["vwmom24"] = np.where(
            vol24 > 0, rsum(np.where(np.isnan(r1), 0.0, r1) * v, 24) / vol24, np.nan)
        dollar = v * c
        illiq = np.where(dollar > 0, np.abs(np.where(np.isnan(r1), 0.0, r1)) / dollar,
                         np.nan)
        feats["amihud"] = rmean(illiq, 24)
    r1z = np.where(np.isnan(r1), 0.0, r1)
    feats["skew72"] = _moment(r1z, 72, 3)
    feats["kurt72"] = _moment(r1z, 72, 4)
    feats["rsi14"] = rsi(c)
    macd = ema(c, 12) - ema(c, 26)
    feats["macd_hist"] = (macd - ema(np.where(np.isnan(macd), np.nan, macd), 9)) / c
    return feats


def targets(lr: np.ndarray, a: np.ndarray, c: np.ndarray, h: int) -> np.ndarray:
    """Forward log return over h bars in units of ATR14 as a fraction of price."""
    n = len(lr)
    out = np.full(n, np.nan)
    if n <= h:
        return out
    fwd = lr[h:] - lr[:-h]
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(a > 0, a / c, np.nan)[:-h]
    out[:-h] = fwd / scale
    return out


# ── ranks, cluster sums, bootstrap ─────────────────────────────────────────

def uniform_ranks(a: np.ndarray) -> np.ndarray:
    """Average ranks mapped to (-0.5, 0.5), ties share their mean rank."""
    uniq, inv, cnt = np.unique(a, return_inverse=True, return_counts=True)
    starts = np.cumsum(cnt) - cnt
    avg = starts + (cnt - 1) / 2.0
    return (avg[inv] + 0.5) / len(a) - 0.5


def cluster_row(ux: np.ndarray, uy: np.ndarray, y: np.ndarray) -> np.ndarray:
    top = ux > _TOP
    bot = ux < -_TOP
    return np.array([len(ux), ux.sum(), uy.sum(), (ux * ux).sum(), (uy * uy).sum(),
                     (ux * uy).sum(), top.sum(), y[top].sum(), bot.sum(), y[bot].sum()],
                    dtype=float)


def pearson(s: np.ndarray) -> float:
    n, sx, sy, sxx, syy, sxy = s[:6]
    if n < 3:
        return float("nan")
    cov = sxy - sx * sy / n
    vx = sxx - sx * sx / n
    vy = syy - sy * sy / n
    if vx <= 0 or vy <= 0:
        return float("nan")
    return float(cov / np.sqrt(vx * vy))


def spread(s: np.ndarray) -> float:
    if s[6] < 1 or s[8] < 1:
        return float("nan")
    return float(s[7] / s[6] - s[9] / s[8])


def bootstrap(clusters: np.ndarray, fn, boot: int, rng: np.random.Generator) -> np.ndarray:
    k = len(clusters)
    counts = rng.multinomial(k, np.full(k, 1.0 / k), size=boot)
    sums = counts @ clusters
    return np.array([fn(row) for row in sums])


def interval(dist: np.ndarray) -> tuple[float, float, float]:
    d = dist[~np.isnan(dist)]
    if len(d) < 50:
        return float("nan"), float("nan"), float("nan")
    lo, hi = np.percentile(d, [2.5, 97.5])
    p = 2.0 * min((np.sum(d <= 0) + 1) / (len(d) + 1), (np.sum(d >= 0) + 1) / (len(d) + 1))
    return float(lo), float(hi), float(min(1.0, p))


def bh_pass(pvals: list[float], q: float) -> list[bool]:
    """Benjamini-Hochberg: which hypotheses survive at false-discovery rate q."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: (np.isnan(pvals[i]), pvals[i]))
    cutoff = -1
    for rank, i in enumerate(order, start=1):
        if not np.isnan(pvals[i]) and pvals[i] <= q * rank / m:
            cutoff = rank
    keep = [False] * m
    for rank, i in enumerate(order, start=1):
        if rank <= cutoff:
            keep[i] = True
    return keep


# ── data ──────────────────────────────────────────────────────────────────

def load_arrays(dataset_dirs: str) -> list[dict]:
    """Per symbol arrays for one or more frozen snapshots."""
    from bot.backtest.snapshot import load_dataset
    out = []
    for d in [x.strip() for x in dataset_dirs.split(",") if x.strip()]:
        for sym, bars in load_dataset(ROOT / d).items():
            if len(bars) < 200:
                continue
            out.append({
                "dataset": Path(d).name, "symbol": sym,
                "ts": np.array([b.timestamp.timestamp() for b in bars]),
                "o": np.array([b.open for b in bars]),
                "h": np.array([b.high for b in bars]),
                "l": np.array([b.low for b in bars]),
                "c": np.array([b.close for b in bars]),
                "v": np.array([b.volume for b in bars]),
            })
    return out


def prepare(series: list[dict]) -> list[dict]:
    for s in series:
        s["f"] = features(s["o"], s["h"], s["l"], s["c"], s["v"])
        lr = np.log(s["c"])
        a = atr(s["h"], s["l"], s["c"])
        s["y"] = {h: targets(lr, a, s["c"], h) for h in HORIZONS}
        s["week"] = np.array([datetime.fromtimestamp(t).isocalendar()[:2] for t in s["ts"]],
                             dtype=object)
    return series


def eligible(s: dict, h: int, window: str, edge_ts: float) -> np.ndarray:
    """Sample indices every h bars. Discovery signals must have their whole
    forward window before the cutoff; holdout signals must start at or after it."""
    n = len(s["ts"])
    idx = np.arange(n)
    fwd_ts = s["ts"][np.minimum(idx + h, n - 1)]
    if window == "discovery":
        ok = (idx + h < n) & (fwd_ts < edge_ts)
    else:
        ok = (idx + h < n) & (s["ts"] >= edge_ts)
    pos = idx[ok]
    return pos[::h]


def collect(series: list[dict], names: list[str], window: str, edge_ts: float,
            cluster: str = "symbol-week") -> dict[tuple[str, int], np.ndarray]:
    """Per (feature, horizon): one row of cluster sums per cluster.

    `symbol-week` clusters by (dataset, symbol, ISO week). `week` merges every
    symbol of a week into one cluster, the stricter reading: crypto symbols move
    together, so symbols inside a week are not independent draws.
    """
    out: dict[tuple[str, int], dict[str, np.ndarray]] = {}
    for h in HORIZONS:
        for s in series:
            pos = eligible(s, h, window, edge_ts)
            y_all = s["y"][h][pos]
            for name in names:
                x_all = s["f"][name][pos]
                ok = np.isfinite(x_all) & np.isfinite(y_all)
                if ok.sum() < 30:
                    continue
                x, y, wk = x_all[ok], y_all[ok], s["week"][pos][ok]
                ux, uy = uniform_ranks(x), uniform_ranks(y)
                yd = y - y.mean()
                labels = np.array([f"{a}-{b}" for a, b in wk])
                for lab in np.unique(labels):
                    m = labels == lab
                    key = lab if cluster == "week" else f"{s['dataset']}-{s['symbol']}-{lab}"
                    row = cluster_row(ux[m], uy[m], yd[m])
                    slot = out.setdefault((name, h), {})
                    slot[key] = slot[key] + row if key in slot else row
    return {k: np.vstack(list(v.values())) for k, v in out.items()}


def comparator_overlap(series: list[dict], names: list[str]) -> dict[str, float]:
    """Largest average within-symbol rank correlation with a comparator."""
    res: dict[str, float] = {}
    for name in names:
        worst = 0.0
        for comp in COMPARATORS:
            if name == comp:
                continue
            vals = []
            for s in series:
                x, c = s["f"][name], s["f"][comp]
                ok = np.isfinite(x) & np.isfinite(c)
                if ok.sum() > 100:
                    vals.append(float(np.corrcoef(uniform_ranks(x[ok]),
                                                  uniform_ranks(c[ok]))[0, 1]))
            if vals:
                worst = max(worst, abs(float(np.mean(vals))))
        res[name] = worst
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--discovery", default=DISCOVERY)
    ap.add_argument("--discovery-until", default=DISCOVERY_UNTIL)
    ap.add_argument("--holdout", default=HOLDOUT)
    ap.add_argument("--holdout-from", default=HOLDOUT_FROM)
    ap.add_argument("--boot", type=int, default=BOOT)
    ap.add_argument("--cluster", choices=("symbol-week", "week"), default="symbol-week",
                    help="bootstrap unit; `week` is the stricter sensitivity reading")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rng = np.random.default_rng(SEED)
    disc = prepare(load_arrays(args.discovery))
    hold = prepare(load_arrays(args.holdout))
    names = sorted(disc[0]["f"])
    cand = [n for n in names if n not in COMPARATORS]
    cut_d = datetime.fromisoformat(args.discovery_until).timestamp()
    cut_h = datetime.fromisoformat(args.holdout_from).timestamp()
    print(f"discovery {args.discovery} {len(disc)} symbols before {args.discovery_until}")
    print(f"holdout   {args.holdout} {len(hold)} symbols from {args.holdout_from}")
    print(f"clusters  {args.cluster}")

    cd = collect(disc, names, "discovery", cut_d, args.cluster)
    ch = collect(hold, names, "holdout", cut_h, args.cluster)
    overlap = comparator_overlap(disc, cand)

    rows = []
    for name in names:
        for h in HORIZONS:
            if (name, h) not in cd:
                continue
            ic = pearson(cd[(name, h)].sum(axis=0))
            lo, hi, p = interval(bootstrap(cd[(name, h)], pearson, args.boot, rng))
            row = {"feature": name, "h": h, "comparator": name in COMPARATORS,
                   "disc_ic": ic, "disc_lo": lo, "disc_hi": hi, "p": p,
                   "disc_spread": spread(cd[(name, h)].sum(axis=0)),
                   "n_disc": int(cd[(name, h)][:, 0].sum()),
                   "overlap": overlap.get(name)}
            if (name, h) in ch:
                hic = pearson(ch[(name, h)].sum(axis=0))
                hlo, hhi, _ = interval(bootstrap(ch[(name, h)], pearson, args.boot, rng))
                row.update(hold_ic=hic, hold_lo=hlo, hold_hi=hhi,
                           hold_spread=spread(ch[(name, h)].sum(axis=0)),
                           n_hold=int(ch[(name, h)][:, 0].sum()))
            rows.append(row)

    tested = [r for r in rows if not r["comparator"]]
    keep = bh_pass([r["p"] for r in tested], FDR)
    for r, k in zip(tested, keep):
        r["bh"] = k
        sign = np.sign(r["disc_ic"]) if np.isfinite(r["disc_ic"]) else 0.0
        hold_ok = ("hold_ic" in r and sign != 0 and np.sign(r["hold_ic"]) == sign
                   and (r["hold_lo"] > 0 or r["hold_hi"] < 0))
        spread_ok = ("hold_spread" in r and np.isfinite(r["hold_spread"])
                     and sign * r["hold_spread"] > COST_ATR)
        distinct = r["overlap"] is not None and r["overlap"] <= COMPARATOR_CAP
        r["passed"] = bool(k and hold_ok and spread_ok and distinct)

    def fmt(r: dict, p: str) -> str:
        if f"{p}_ic" not in r:
            return "      n/a      "
        return f"{r[f'{p}_ic']:+.3f} [{r[f'{p}_lo']:+.3f},{r[f'{p}_hi']:+.3f}]"

    print(f"\n{len(tested)} (feature, horizon) tests; Benjamini-Hochberg q={FDR}; "
          f"cost {COST_ATR} ATR; bootstrap {args.boot} draws seed {SEED}")
    print(f"{'feature':10} {'h':>3}  {'discovery IC':26} {'holdout IC':26} "
          f"{'spr d':>7} {'spr h':>7} {'BH':>3} {'corr':>5}  verdict")
    for r in sorted(rows, key=lambda r: -abs(r["disc_ic"]) if np.isfinite(r["disc_ic"]) else 0):
        tag = "comparator" if r["comparator"] else ("PASS" if r["passed"] else "fail")
        sh = r.get("hold_spread")
        print(f"{r['feature']:10} {r['h']:>3}  {fmt(r, 'disc'):26} {fmt(r, 'hold'):26} "
              f"{r['disc_spread']:+7.3f} {(sh if sh is not None else float('nan')):+7.3f} "
              f"{'y' if r.get('bh') else 'n':>3} "
              f"{(r['overlap'] if r['overlap'] is not None else float('nan')):5.2f}  {tag}")
    passed = [r for r in tested if r["passed"]]
    print(f"\n{len(passed)} of {len(tested)} candidates pass every rule of the pre-registration")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(rows, indent=2, default=float))
        print(f"saved {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
