#!/usr/bin/env python3
"""Daily trend sleeve + funding-aware carry overlay + drawdown de-gearing, as pre-registered.

Rules, split, costs and pass bars: docs/research/prereg/new_strategy_trend_carry/PREREGISTRATION.md.
Nothing here is tuned. The trend leg is `scripts/tsmom_study.py` reused (one reading per quantity).

    python3 scripts/trend_carry_overlay_study.py fetch            # free data.binance.vision files -> data/
    python3 scripts/trend_carry_overlay_study.py run --split dev
    python3 scripts/trend_carry_overlay_study.py run --split holdout
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tsmom_study as ts  # noqa: E402

CannotCheck = ts.CannotCheck
ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "docs" / "research" / "prereg" / "new_strategy_trend_carry"
SPOT_SRC = ROOT / "docs" / "research" / "prereg" / "new_strategy_tsmom" / "data"
COINS = ts.COINS
LOOKBACKS = (20, 60, 120)
COST_SIDE = 0.001
SCORE_START = date(2020, 1, 1)
DEV_END = date(2022, 12, 31)
HOLD_START = date(2023, 1, 1)
LAST_DAY = date(2026, 9, 30)
FUND_FIRST_MONTH = (2019, 9)
FUND_WINDOW = 30
FUND_SHORT = 7
CARRY_ENTER = 0.00025
CARRY_STAY = 0.00010
CARRY_MAX_COINS = 5
CARRY_NOTIONAL = 0.10
CARRY_CAPITAL_FACTOR = 1.2
DG_WINDOW = 180
DG_LEVELS = ((0.15, 0.25), (0.08, 0.5))  # (dd >= level -> gear), checked in order; else 1.0
PERP_FALLBACK_FUNDING = 0.0003
BASE = "https://data.binance.vision/data/futures/um/monthly"

CONFIGS: dict[str, dict[str, Any]] = {
    "TC": {"trend": True, "venue": "spot", "carry": True, "degear": True},
    "P1": {"trend": True, "venue": "spot", "carry": False, "degear": False},
    "P0": {"trend": True, "venue": "perp", "carry": False, "degear": False},
    "TD": {"trend": True, "venue": "spot", "carry": False, "degear": True},
    "TK": {"trend": True, "venue": "spot", "carry": True, "degear": False},
    "K": {"trend": False, "venue": "spot", "carry": True, "degear": False},
}


# ------------------------------------------------------------------ fetch

def _months(first: tuple[int, int], last: date) -> list[tuple[int, int]]:
    out, (y, m) = [], first
    while (y, m) <= (last.year, last.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _get_zip(url: str) -> bytes | None:
    import requests  # local import: the study itself needs no network

    for attempt in range(4):
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            return resp.content
        if resp.status_code == 404:
            return None
        import time
        time.sleep(2 * (attempt + 1))
    raise CannotCheck(f"{url} HTTP {resp.status_code}")


def parse_funding(text: str) -> dict[date, tuple[float, int]]:
    """Daily (sum, count) of settled rates. A settlement belongs to the UTC date of ts - 1000 ms."""
    out: dict[date, tuple[float, int]] = {}
    for line in text.splitlines():
        p = line.strip().split(",")
        if not p or not p[0].isdigit():
            continue
        ts_ms = int(p[0])
        d = datetime.fromtimestamp((ts_ms - 1000) / 1000, tz=timezone.utc).date()
        s, n = out.get(d, (0.0, 0))
        out[d] = (s + float(p[2]), n + 1)
    return out


def parse_klines(text: str) -> dict[date, float]:
    out: dict[date, float] = {}
    for line in text.splitlines():
        p = line.strip().split(",")
        if not p or not p[0].isdigit():
            continue
        d = datetime.fromtimestamp(int(p[0]) / 1000, tz=timezone.utc).date()
        out[d] = float(p[1])
    return out


def _fetch_coin(coin: str) -> tuple[dict[date, tuple[float, int]], dict[date, float]]:
    fund: dict[date, tuple[float, int]] = {}
    perp: dict[date, float] = {}
    for y, m in _months(FUND_FIRST_MONTH, LAST_DAY):
        z = _get_zip(f"{BASE}/fundingRate/{coin}USDT/{coin}USDT-fundingRate-{y}-{m:02d}.zip")
        if z is not None:
            with zipfile.ZipFile(io.BytesIO(z)) as zf:
                fund.update(parse_funding(zf.read(zf.namelist()[0]).decode()))
        z = _get_zip(f"{BASE}/klines/{coin}USDT/1d/{coin}USDT-1d-{y}-{m:02d}.zip")
        if z is not None:
            with zipfile.ZipFile(io.BytesIO(z)) as zf:
                perp.update(parse_klines(zf.read(zf.namelist()[0]).decode()))
    return fund, perp


def cmd_fetch(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    for coin in COINS:
        shutil.copyfile(SPOT_SRC / f"{coin}.csv", outdir / f"{coin}.csv")
    with ThreadPoolExecutor(max_workers=4) as ex:
        res = list(ex.map(_fetch_coin, COINS))
    for coin, (fund, perp) in zip(COINS, res):
        with (outdir / f"funding_{coin}.csv").open("w", encoding="utf-8") as fh:
            fh.write("date,sum,n\n")
            for d in sorted(fund):
                fh.write(f"{d.isoformat()},{fund[d][0]:.10f},{fund[d][1]}\n")
        with (outdir / f"perp_{coin}.csv").open("w", encoding="utf-8") as fh:
            fh.write("date,open\n")
            for d in sorted(perp):
                fh.write(f"{d.isoformat()},{perp[d]}\n")
        print(coin, len(fund), len(perp), file=sys.stderr)
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(outdir.glob("*.csv"))]
    (outdir.parent / "data_sha256.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ load

def _contiguous(days: list[date], what: str) -> None:
    for a, b in zip(days, days[1:]):
        if (b - a).days != 1:
            raise CannotCheck(f"{what}: gap between {a} and {b}")


def load_aux(datadir: Path, coin: str) -> tuple[dict[date, float], dict[date, float]]:
    """(daily funding sum, daily perp open) for a coin; empty dicts when the coin has no perp file."""
    fpath, ppath = datadir / f"funding_{coin}.csv", datadir / f"perp_{coin}.csv"
    if not fpath.exists() or not ppath.exists():
        raise CannotCheck(f"missing perp/funding file for {coin}")
    fund: dict[date, float] = {}
    for line in fpath.read_text(encoding="utf-8").splitlines()[1:]:
        p = line.split(",")
        fund[date.fromisoformat(p[0])] = float(p[1])
    perp: dict[date, float] = {}
    for line in ppath.read_text(encoding="utf-8").splitlines()[1:]:
        p = line.split(",")
        perp[date.fromisoformat(p[0])] = float(p[1])
    if fund:
        _contiguous(sorted(fund), f"{coin} funding")
    if perp:
        _contiguous(sorted(perp), f"{coin} perp")
        if any(v <= 0 for v in perp.values()):
            raise CannotCheck(f"{coin}: non-positive perp price")
    return fund, perp


def align(dates: list[date], names: list[str], aux: dict[str, tuple[dict[date, float], dict[date, float]]]):
    """Funding F[T,C] and perp open PO[T,C] on the spot date axis, NaN where unread."""
    T, C = len(dates), len(names)
    F = np.full((T, C), np.nan)
    PO = np.full((T, C), np.nan)
    pos = {d: i for i, d in enumerate(dates)}
    for j, nm in enumerate(names):
        fund, perp = aux[nm]
        for d, v in fund.items():
            if d in pos:
                F[pos[d], j] = v
        for d, v in perp.items():
            if d in pos:
                PO[pos[d], j] = v
    return F, PO


# ------------------------------------------------------------------ legs

def trend_targets(dates: list[date], cl: np.ndarray) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """Target weights and band budgets at every Sunday close (reused tsmom_study rule, long/flat)."""
    out = {}
    for d in range(len(dates) - 1):
        if dates[d].weekday() == 6:
            out[d] = ts.target_weights(cl, d, LOOKBACKS, "LF")
    return out


def carry_target(d: int, F: np.ndarray, PO: np.ndarray, trend_w: np.ndarray | None,
                 current: np.ndarray) -> np.ndarray:
    """Carry notionals decided at the close of row d. Funding is read over the 30 completed days before d."""
    C = F.shape[1]
    scored: list[tuple[float, str, int]] = []
    if d - FUND_WINDOW < 0:
        return np.zeros(C)
    for j in range(C):
        win = F[d - FUND_WINDOW: d, j]
        if np.isnan(win).any() or np.isnan(PO[d - FUND_WINDOW: d + 2, j]).any():
            continue
        if trend_w is not None and trend_w[j] > 0:
            continue
        m30 = float(win.mean())
        m7 = float(win[-FUND_SHORT:].mean())
        floor = CARRY_STAY if current[j] > 0 else CARRY_ENTER
        if m30 >= floor and m7 > 0:
            scored.append((-m30, str(j).zfill(3), j))
    scored.sort()
    n = np.zeros(C)
    for _, _, j in scored[:CARRY_MAX_COINS]:
        n[j] = CARRY_NOTIONAL
    return n


def gear_for(dd: float) -> float:
    for level, g in DG_LEVELS:
        if dd >= level:
            return g
    return 1.0


# ------------------------------------------------------------------ simulation

def simulate(dates, op, cl, F, PO, targets, cfg, cost_side: float = COST_SIDE) -> dict[str, Any]:
    T, C = op.shape
    w = np.zeros(C)
    g = 1.0
    carry = np.zeros(C)
    pend_t = None
    pend_c = None
    pend_g = None
    E = 1.0
    eqs: list[float] = []
    cols = {k: [] for k in ("net", "trend", "tfund", "cbasis", "cfund", "cost", "tv", "cv", "gear", "trend_gross",
                            "carry_notional", "fills")}
    fills = 0
    for d in range(1, T):
        ret = op[d] / op[d - 1] - 1.0
        held = w != 0
        if np.any(np.isnan(ret[held])):
            raise CannotCheck(f"held coin has no price on {dates[d]}")
        r_tr = float(np.sum(w[held] * ret[held])) if held.any() else 0.0
        tfund, nfill = 0.0, 0
        if cfg["venue"] == "perp" and held.any():
            f = F[d - 1, held]
            ok = ~np.isnan(f)
            if not ok.all():
                nfill = int((~ok).sum())
                fill = float(f[ok].mean()) if ok.any() else PERP_FALLBACK_FUNDING
                f = np.where(ok, f, fill)
            tfund = float(np.sum(w[held] * f))
        ch = carry != 0
        r_cb = r_cf = 0.0
        if ch.any():
            pr = PO[d, ch] / PO[d - 1, ch] - 1.0
            fc = F[d - 1, ch]
            if np.isnan(pr).any() or np.isnan(fc).any():
                raise CannotCheck(f"held carry coin unreadable on {dates[d]}")
            r_cb = float(np.sum(carry[ch] * (ret[ch] - pr)))
            r_cf = float(np.sum(carry[ch] * fc))
        gross = r_tr - tfund + r_cb + r_cf
        if held.any():
            w = np.where(held, w * (1.0 + np.nan_to_num(ret)) / (1.0 + gross), 0.0)
        # fills at the open of d
        w_pre = w.copy()
        g_new = pend_g if pend_g is not None else g
        if g_new != g:
            w = w * (g_new / g)
        g = g_new
        pend_g = None
        if pend_t is not None:
            tgt, bud = pend_t
            pend_t = None
            for j in range(C):
                te, be = tgt[j] * g, bud[j] * g
                diff = te - w[j]
                if (te == 0 and w[j] != 0) or (diff != 0 and abs(diff) > ts.BAND * be):
                    w[j] = te
        tv = float(np.abs(w - w_pre).sum())
        cv = 0.0
        if pend_c is not None:
            cv = float(np.abs(pend_c - carry).sum())
            carry = pend_c.copy()
            pend_c = None
        cost = cost_side * (tv + 2.0 * cv)
        net = gross - cost
        E *= 1.0 + net
        eqs.append(E)
        for k, v in (("net", net), ("trend", r_tr), ("tfund", tfund), ("cbasis", r_cb), ("cfund", r_cf),
                     ("cost", cost), ("tv", tv), ("cv", cv), ("gear", g), ("trend_gross", float(np.sum(w))),
                     ("carry_notional", float(np.sum(carry))), ("fills", nfill)):
            cols[k].append(v)
        fills += nfill
        # decisions at the close of d
        if cfg["degear"]:
            peak = max(eqs[-DG_WINDOW:])
            pend_g = gear_for(1.0 - E / peak)
            if pend_g == g:
                pend_g = None
        if dates[d].weekday() == 6 and d + 1 < T:
            tgt_pair = targets.get(d)
            if cfg["trend"] and tgt_pair is not None:
                pend_t = tgt_pair
            if cfg["carry"]:
                trend_w = tgt_pair[0] if (cfg["trend"] and tgt_pair is not None) else None
                n = carry_target(d, F, PO, trend_w, carry)
                tg = float(tgt_pair[0].sum()) if trend_w is not None else 0.0
                cap = CARRY_CAPITAL_FACTOR * float(n.sum())
                if cap > 0 and tg + cap > 1.0:
                    n = n * (max(0.0, 1.0 - tg) / cap)
                pend_c = n
    keep = [i for i in range(len(eqs)) if dates[i + 1] >= SCORE_START]
    out: dict[str, Any] = {"dates": [dates[i + 1] for i in keep], "perp_fills": fills}
    for k, v in cols.items():
        out[k] = np.array([v[i] for i in keep])
    return out


# ------------------------------------------------------------------ metrics

def summarise(sim: dict[str, Any], idx: np.ndarray) -> dict[str, Any]:
    r = sim["net"][idx]
    yrs = len(idx) / 365.0
    return {
        "days": int(len(idx)),
        "sharpe": ts.sharpe(r),
        "cagr": ts.cagr(r),
        "ann_vol": float(r.std(ddof=1) * np.sqrt(365)) if len(r) > 1 else None,
        "max_dd": ts.max_dd(r),
        "total_return": float(np.prod(1.0 + r) - 1.0) if len(r) else None,
        "sum_trend_gross": float(sim["trend"][idx].sum()),
        "sum_trend_perp_funding_paid": float(sim["tfund"][idx].sum()),
        "sum_carry_funding": float(sim["cfund"][idx].sum()),
        "sum_carry_basis": float(sim["cbasis"][idx].sum()),
        "sum_costs": float(sim["cost"][idx].sum()),
        "trend_turnover_per_year": float(sim["tv"][idx].sum() / yrs) if yrs > 0 else None,
        "carry_turnover_per_year": float(sim["cv"][idx].sum() / yrs) if yrs > 0 else None,
        "mean_trend_gross": float(sim["trend_gross"][idx].mean()) if len(idx) else None,
        "mean_carry_notional": float(sim["carry_notional"][idx].mean()) if len(idx) else None,
        "mean_gear": float(sim["gear"][idx].mean()) if len(idx) else None,
        "share_days_geared_down": float((sim["gear"][idx] < 1.0).mean()) if len(idx) else None,
    }


def window(dates: list[date], lo: date, hi: date | None) -> np.ndarray:
    return ts.window(dates, lo, hi)


def btc_returns(dates, names, op, sim_dates) -> np.ndarray:
    return ts.btc_returns(dates, names, op, sim_dates)


def run_study(series, aux, split: str, cost_side: float = COST_SIDE, configs=None) -> dict[str, Any]:
    last = DEV_END if split == "dev" else LAST_DAY
    dates, names, op, cl = ts.build_panel(series, last)
    F, PO = align(dates, names, aux)
    targets = trend_targets(dates, cl)
    out: dict[str, Any] = {"split": split, "first_date": dates[0].isoformat(), "last_date": dates[-1].isoformat(),
                           "cost_side": cost_side, "tests": {}}
    btc = None
    for cid, cfg in (configs or CONFIGS).items():
        sim = simulate(dates, op, cl, F, PO, targets, cfg, cost_side)
        sd = sim["dates"]
        if not sd:
            raise CannotCheck("no scored days")
        didx = window(sd, SCORE_START, DEV_END)
        entry: dict[str, Any] = {"cfg": cfg, "scored_from": sd[0].isoformat(), "dev": summarise(sim, didx),
                                 "years": ts.by_year(sd, sim["net"]), "perp_funding_fills": sim["perp_fills"]}
        if split == "holdout":
            hidx = window(sd, HOLD_START, None)
            entry["holdout"] = summarise(sim, hidx)
            entry["holdout_boot_ci90"] = [ts.boot_lower(sim["net"][hidx], q=5.0),
                                          ts.boot_lower(sim["net"][hidx], q=95.0)]
            if btc is None:
                btc = btc_returns(dates, names, op, sd)
            entry["btc_holdout"] = {"sharpe": ts.sharpe(btc[hidx]), "max_dd": ts.max_dd(btc[hidx]),
                                    "cagr": ts.cagr(btc[hidx])}
            entry["btc_dev"] = {"sharpe": ts.sharpe(btc[didx]), "max_dd": ts.max_dd(btc[didx])}
            entry["bars"] = ts.bars(entry["dev"]["sharpe"], entry["holdout"]["sharpe"], entry["holdout_boot_ci90"][0],
                                    entry["holdout"]["max_dd"], entry["btc_holdout"]["max_dd"], entry["years"])
            entry["passes"] = all(entry["bars"].values())
        out["tests"][cid] = entry
    return out


def _load_all(datadir: Path):
    series, aux = {}, {}
    for coin in COINS:
        p = datadir / f"{coin}.csv"
        if not p.exists():
            raise CannotCheck(f"missing {p}")
        series[coin] = ts.load_series(p)
        aux[coin] = load_aux(datadir, coin)
    return series, aux


def cmd_run(split: str, datadir: Path) -> dict[str, Any]:
    series, aux = _load_all(datadir)
    res = run_study(series, aux, split)
    if split == "holdout":
        res["cost_0.20_side"] = run_study(series, aux, split, cost_side=0.002)["tests"]
    (HERE / f"result_{split}.json").write_text(json.dumps(ts._jsonable(res), indent=1), encoding="utf-8")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch")
    rp = sub.add_parser("run")
    rp.add_argument("--split", choices=("dev", "holdout"), required=True)
    args = ap.parse_args(argv)
    datadir = HERE / "data"
    if args.cmd == "fetch":
        cmd_fetch(datadir)
        return 0
    cmd_run(args.split, datadir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
