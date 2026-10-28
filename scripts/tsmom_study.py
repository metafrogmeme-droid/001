#!/usr/bin/env python3
"""Multi-lookback time-series momentum study, as pre-registered.

Rules, split, costs and pass bars: docs/research/prereg/new_strategy_tsmom/
PREREGISTRATION.md and PREREGISTRATION.addendum_1.md. Nothing here is tuned.

    python3 scripts/tsmom_study.py fetch            # free Binance spot daily klines -> data/
    python3 scripts/tsmom_study.py run --split dev  # data truncated at the dev end
    python3 scripts/tsmom_study.py run --split holdout
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent.parent / "docs" / "research" / "prereg" / "new_strategy_tsmom"
COINS = ["ADA", "ALGO", "ATOM", "BCH", "BTC", "DOGE", "DOT", "ETC",
         "ETH", "FIL", "LINK", "LTC", "TRX", "UNI", "XLM", "XRP"]
LOOKBACK_SETS = {"A": (20, 50, 100, 200), "B": (50, 100, 200)}
VARIANTS = ("LF", "LS")
VOL_TARGET = 0.20
VOL_WINDOW = 60
MIN_HISTORY = 150
BAND = 0.25
COIN_CAP = 0.25
GROSS_CAP = 1.0
COST_SIDE = 0.001
FUNDING_DAY = 0.0003
DEV_START = date(2018, 1, 1)
DEV_END = date(2022, 12, 31)
HOLD_START = date(2023, 1, 1)
BOOT_BLOCK = 20
BOOT_DRAWS = 2000
BOOT_SEED = 20261028
MIN_SHARPE = 0.5
MIN_YEAR_DAYS = 90
KLINES = "https://data-api.binance.vision/api/v3/klines"


class CannotCheck(Exception):
    """A series could not be read honestly. Never rendered as zero."""


# ------------------------------------------------------------------ data

def fetch_coin(coin: str, get: Any) -> list[list[Any]]:
    """All closed daily bars for COINUSDT, oldest first. `get(url, params)` returns parsed JSON."""
    rows: list[list[Any]] = []
    start = int(datetime(2017, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)
    now_ms = int(time.time() * 1000)
    while True:
        chunk = get(KLINES, {"symbol": f"{coin}USDT", "interval": "1d", "startTime": start, "limit": 1000})
        if not chunk:
            break
        rows.extend(chunk)
        start = int(chunk[-1][0]) + 86_400_000
        if len(chunk) < 1000:
            break
    return [r for r in rows if int(r[6]) < now_ms]  # closed bars only


def _http_get(url: str, params: dict[str, Any]) -> Any:
    import requests  # local import: the study itself needs no network

    for attempt in range(4):
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        time.sleep(2 * (attempt + 1))
    raise CannotCheck(f"klines {params.get('symbol')} HTTP {resp.status_code}")


def cmd_fetch(outdir: Path) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    lines = []
    for coin in COINS:
        rows = fetch_coin(coin, _http_get)
        path = outdir / f"{coin}.csv"
        with path.open("w", encoding="utf-8") as fh:
            fh.write("date,open,high,low,close,volume\n")
            for r in rows:
                d = datetime.fromtimestamp(int(r[0]) / 1000, tz=timezone.utc).date().isoformat()
                fh.write(f"{d},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}\n")
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
        print(coin, len(rows), file=sys.stderr)
    (outdir.parent / "data_sha256.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_series(path: Path) -> tuple[list[date], np.ndarray, np.ndarray]:
    dates: list[date] = []
    opens: list[float] = []
    closes: list[float] = []
    with path.open(encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            p = line.strip().split(",")
            dates.append(date.fromisoformat(p[0]))
            opens.append(float(p[1]))
            closes.append(float(p[4]))
    for a, b in zip(dates, dates[1:]):
        if (b - a).days != 1:
            raise CannotCheck(f"{path.name}: gap between {a} and {b}")
    if any(v <= 0 for v in opens + closes):
        raise CannotCheck(f"{path.name}: non-positive price")
    return dates, np.array(opens), np.array(closes)


def build_panel(series: dict[str, tuple[list[date], np.ndarray, np.ndarray]], last: date | None = None):
    """Aligned (dates, opens, closes) with NaN before a coin's first bar. `last` truncates the data."""
    first = min(s[0][0] for s in series.values())
    end = max(s[0][-1] for s in series.values())
    if last is not None:
        end = min(end, last)
    n = (end - first).days + 1
    dates = [first + timedelta(days=i) for i in range(n)]
    names = list(series)
    op = np.full((n, len(names)), np.nan)
    cl = np.full((n, len(names)), np.nan)
    for j, nm in enumerate(names):
        ds, o, c = series[nm]
        for d, ov, cv in zip(ds, o, c):
            i = (d - first).days
            if 0 <= i < n:
                op[i, j] = ov
                cl[i, j] = cv
    return dates, names, op, cl


# ------------------------------------------------------------------ rule

def eligible(cl: np.ndarray, t: int, lookbacks: tuple[int, ...]) -> list[int]:
    """Coins with every lookback available, >= 150 own bars, and a gap-free history at row t."""
    need = max(MIN_HISTORY, max(lookbacks) + 1, VOL_WINDOW + 1)
    out = []
    for j in range(cl.shape[1]):
        col = cl[: t + 1, j]
        valid = ~np.isnan(col)
        if not valid.any():
            continue
        first = int(np.argmax(valid))
        if t - first + 1 >= need and valid[first:].all():
            out.append(j)
    return out


def target_weights(cl: np.ndarray, t: int, lookbacks: tuple[int, ...], variant: str):
    """Target weights and band budgets from closes up to and including row t. (w, b), both length C."""
    c = cl.shape[1]
    w = np.zeros(c)
    b = np.zeros(c)
    elig = eligible(cl, t, lookbacks)
    if not elig:
        return w, b
    n = len(elig)
    win = np.log(cl[t - VOL_WINDOW: t + 1, elig])
    rets = np.diff(win, axis=0)
    vol = rets.std(axis=0, ddof=1) * np.sqrt(365)
    score = np.zeros(n)
    for k, j in enumerate(elig):
        score[k] = np.mean([np.sign(cl[t, j] / cl[t - L, j] - 1.0) for L in lookbacks])
    if variant == "LF":
        score = np.maximum(score, 0.0)
    if np.any(vol <= 0):
        raise CannotCheck("zero realised vol in window")
    u = score / vol / n
    cov = np.cov(rets, rowvar=False, ddof=1) * 365
    cov = np.atleast_2d(cov)
    sp = float(np.sqrt(max(u @ cov @ u, 0.0)))
    if sp <= 0 or not np.any(score):
        return w, b
    k = VOL_TARGET / sp
    ww = k * u
    gross = float(np.abs(ww).sum())
    if gross > GROSS_CAP:
        ww = ww * (GROSS_CAP / gross)
        k = k * (GROSS_CAP / gross)
    ww = np.clip(ww, -COIN_CAP, COIN_CAP)
    for q, j in enumerate(elig):
        w[j] = ww[q]
        b[j] = k / vol[q] / n
    return w, b


def simulate(dates: list[date], op: np.ndarray, cl: np.ndarray,
             lookbacks: tuple[int, ...], variant: str) -> dict[str, Any]:
    """Daily gross returns, turnover and long notional, labelled by the later open's date."""
    T, c = op.shape
    w = np.zeros(c)
    pending: tuple[np.ndarray, np.ndarray] | None = None
    labels: list[int] = []
    first_fill: date | None = None
    gross: list[float] = []
    turn: list[float] = []
    longn: list[float] = []
    trades: list[int] = []
    for d in range(1, T):
        ret = op[d] / op[d - 1] - 1.0
        held = w != 0
        if np.any(np.isnan(ret[held])):
            raise CannotCheck(f"held coin has no price on {dates[d]}")
        r = float(np.sum(w[held] * ret[held]))
        ln = float(np.sum(np.maximum(w, 0.0)))
        if held.any():
            w = np.where(held, w * (1.0 + np.nan_to_num(ret)) / (1.0 + r), 0.0)
        tv = 0.0
        nt = 0
        if pending is not None:
            tgt, bud = pending
            pending = None
            for j in range(c):
                diff = tgt[j] - w[j]
                if (tgt[j] == 0 and w[j] != 0) or (diff != 0 and abs(diff) > BAND * bud[j]):
                    tv += abs(diff)
                    nt += 1
                    w[j] = tgt[j]
        labels.append(d)
        gross.append(r)
        turn.append(tv)
        longn.append(ln)
        trades.append(nt)
        if dates[d].weekday() == 6 and d + 1 < T:  # Sunday close -> fill at next open
            pending = target_weights(cl, d, lookbacks, variant)
            if first_fill is None and eligible(cl, d, lookbacks):
                first_fill = dates[d + 1]
    if first_fill is None:
        raise CannotCheck("no coin was ever eligible")
    floor = max(first_fill, DEV_START)
    keep = [i for i, d in enumerate(labels) if dates[d] >= floor]
    return {
        "dates": [dates[labels[i]] for i in keep],
        "gross": np.array([gross[i] for i in keep]),
        "turnover": np.array([turn[i] for i in keep]),
        "long": np.array([longn[i] for i in keep]),
        "trades": np.array([trades[i] for i in keep]),
    }


def net_returns(sim: dict[str, Any], variant: str, cost_side: float = COST_SIDE,
                funding: bool | None = None) -> np.ndarray:
    """Net daily returns. funding None = the decision charge (LS only)."""
    if funding is None:
        funding = variant == "LS"
    r = sim["gross"] - cost_side * sim["turnover"]
    if funding:
        r = r - FUNDING_DAY * sim["long"]
    return r


# ------------------------------------------------------------------ metrics

def sharpe(r: np.ndarray) -> float | None:
    if len(r) < 2:
        return None
    sd = float(r.std(ddof=1))
    if sd == 0:
        return None
    return float(r.mean() / sd * np.sqrt(365))


def max_dd(r: np.ndarray) -> float | None:
    if len(r) == 0:
        return None
    eq = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    return float(np.max(1.0 - eq / peak))


def cagr(r: np.ndarray) -> float | None:
    if len(r) < 2:
        return None
    return float(np.prod(1.0 + r) ** (365.0 / len(r)) - 1.0)


def boot_lower(r: np.ndarray, block: int = BOOT_BLOCK, draws: int = BOOT_DRAWS,
               seed: int = BOOT_SEED, q: float = 5.0) -> float | None:
    n = len(r)
    if n < 2 * block:
        return None
    rng = np.random.default_rng(seed)
    nb = -(-n // block)
    out = []
    for _ in range(draws):
        starts = rng.integers(0, n - block + 1, size=nb)
        s = np.concatenate([r[a: a + block] for a in starts])[:n]
        v = sharpe(s)
        if v is not None:
            out.append(v)
    if len(out) < draws // 2:
        return None
    return float(np.percentile(out, q))


def by_year(dates: list[date], r: np.ndarray) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    for y in sorted({d.year for d in dates}):
        idx = [i for i, d in enumerate(dates) if d.year == y]
        out[y] = {"days": len(idx), "ret": float(np.prod(1.0 + r[idx]) - 1.0)}
    return out


def window(dates: list[date], lo: date, hi: date | None) -> np.ndarray:
    return np.array([i for i, d in enumerate(dates) if d >= lo and (hi is None or d <= hi)], dtype=int)


def summarise(dates: list[date], r: np.ndarray, sim: dict[str, Any], idx: np.ndarray) -> dict[str, Any]:
    rr = r[idx]
    yrs = len(idx) / 365.0
    gross_total = float(np.sum(sim["gross"][idx]))
    cost_total = float(np.sum(COST_SIDE * sim["turnover"][idx]))
    return {
        "days": int(len(idx)),
        "sharpe": sharpe(rr),
        "cagr": cagr(rr),
        "ann_vol": float(rr.std(ddof=1) * np.sqrt(365)) if len(rr) > 1 else None,
        "max_dd": max_dd(rr),
        "turnover_per_year": float(np.sum(sim["turnover"][idx]) / yrs) if yrs > 0 else None,
        "trades": int(np.sum(sim["trades"][idx])),
        "mean_long_exposure": float(np.mean(sim["long"][idx])) if len(idx) else None,
        "costs_sum_of_daily": cost_total,
        "gross_sum_of_daily": gross_total,
        "costs_share_of_gross": (cost_total / gross_total) if gross_total > 0 else None,
    }


def btc_returns(dates: list[date], names: list[str], op: np.ndarray, sim_dates: list[date]) -> np.ndarray:
    j = names.index("BTC")
    pos = {d: i for i, d in enumerate(dates)}
    out = []
    for d in sim_dates:
        i = pos[d]
        out.append(op[i, j] / op[i - 1, j] - 1.0)
    return np.array(out)


def bars(dev_sharpe, hold_sharpe, hold_lb, hold_dd, btc_dd, years) -> dict[str, bool]:
    """Each bar is False when its input is unreadable (None), never zero."""
    pos = [v["ret"] > 0 for v in years.values() if v["days"] >= MIN_YEAR_DAYS]
    return {
        "dev_sharpe": dev_sharpe is not None and dev_sharpe >= MIN_SHARPE,
        "holdout_sharpe": hold_sharpe is not None and hold_sharpe >= MIN_SHARPE,
        "holdout_boot_lb_gt_0": hold_lb is not None and hold_lb > 0,
        "holdout_dd_le_btc": hold_dd is not None and btc_dd is not None and hold_dd <= btc_dd,
        "years_majority_positive": bool(pos) and sum(pos) * 2 > len(pos),
    }


def run_study(series, split: str) -> dict[str, Any]:
    last = DEV_END if split == "dev" else None
    dates, names, op, cl = build_panel(series, last)
    out: dict[str, Any] = {"split": split, "first_date": dates[0].isoformat(),
                           "last_date": dates[-1].isoformat(), "tests": {}}
    for set_name, lbs in LOOKBACK_SETS.items():
        for var in VARIANTS:
            sim = simulate(dates, op, cl, lbs, var)
            sd = sim["dates"]
            if not sd:
                raise CannotCheck("no scored days")
            r = net_returns(sim, var)
            didx = window(sd, DEV_START, DEV_END)
            entry: dict[str, Any] = {"lookbacks": list(lbs), "variant": var, "scored_from": sd[0].isoformat()}
            entry["dev"] = summarise(sd, r, sim, didx)
            entry["years_full_so_far"] = by_year(sd, r)
            if split == "holdout":
                hidx = window(sd, HOLD_START, None)
                entry["holdout"] = summarise(sd, r, sim, hidx)
                entry["holdout_boot_lb5"] = boot_lower(r[hidx])
                entry["holdout_boot_ci90"] = [boot_lower(r[hidx], q=5.0), boot_lower(r[hidx], q=95.0)]
                btc = btc_returns(dates, names, op, sd)
                entry["btc_holdout"] = {"sharpe": sharpe(btc[hidx]), "max_dd": max_dd(btc[hidx]),
                                        "cagr": cagr(btc[hidx])}
                entry["btc_dev"] = {"sharpe": sharpe(btc[didx]), "max_dd": max_dd(btc[didx])}
                entry["bars"] = bars(
                    entry["dev"]["sharpe"], entry["holdout"]["sharpe"], entry["holdout_boot_lb5"],
                    entry["holdout"]["max_dd"], entry["btc_holdout"]["max_dd"], entry["years_full_so_far"])
                entry["passes"] = all(entry["bars"].values())
                sens = {}
                for label, kw in (("cost0.20_decision_funding", {"cost_side": 0.002}),
                                  ("no_funding", {"funding": False}),
                                  ("with_funding", {"funding": True})):
                    rs = net_returns(sim, var, **kw)
                    sens[label] = {"dev_sharpe": sharpe(rs[didx]), "holdout_sharpe": sharpe(rs[hidx]),
                                   "holdout_cagr": cagr(rs[hidx]), "holdout_max_dd": max_dd(rs[hidx])}
                entry["sensitivity"] = sens
            out["tests"][f"{set_name}_{var}"] = entry
    return out


def _jsonable(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return o


def cmd_run(split: str, datadir: Path) -> dict[str, Any]:
    series = {}
    for coin in COINS:
        p = datadir / f"{coin}.csv"
        if not p.exists():
            raise CannotCheck(f"missing {p}")
        series[coin] = load_series(p)
    res = run_study(series, split)
    (HERE / f"result_{split}.json").write_text(json.dumps(_jsonable(res), indent=1), encoding="utf-8")
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
