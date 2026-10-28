#!/usr/bin/env python3
"""Cross-sectional momentum behind a BTC trend regime gate: pre-registered study.

Protocol and every parameter live in
docs/research/prereg/new_strategy_xsmom_regime/PREREGISTRATION.md. Research only:
no keys, no orders, no network except the public daily-kline fetch.

    python3 scripts/xsmom_regime_study.py fetch
    python3 scripts/xsmom_regime_study.py dev
    python3 scripts/xsmom_regime_study.py holdout    # once, after code_at_unseal.sha256
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "docs/research/prereg/new_strategy_xsmom_regime"
DATA = DIR / "data"
UNIVERSE = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "DOT", "AVAX", "LTC",
            "LINK", "TRX", "BCH", "ATOM", "UNI", "ETC", "FIL", "NEAR", "XLM", "ALGO"]
ANCHOR = date(2017, 8, 17)
DEV_START, DEV_END = "2018-06-01", "2022-12-31"
HOLD_START = "2023-01-01"
FEE = 0.0010
FUNDING_PER_DAY = 0.0003
MIN_HISTORY = 150
TARGET_VOL = 0.30
VOL_WIN, PORT_VOL_WIN = 30, 60
BLOCK, DRAWS, SEED = 20, 2000, 20261028
KLINES = "https://data-api.binance.vision/api/v3/klines"


@dataclass(frozen=True)
class Params:
    lookback: int = 90
    rebalance: int = 14
    regime_ma: int = 150
    fee: float = FEE
    funding_per_day: float = 0.0


PRIMARY = Params()
GRID = [Params(lb, rb, ma) for lb in (60, 90) for rb in (14, 30) for ma in (100, 200)]


# ---------------------------------------------------------------- data
def fetch(coin: str, end_ms: int) -> pd.DataFrame:
    import requests
    rows, start = [], int(datetime(2017, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)
    while start < end_ms:
        r = requests.get(KLINES, params={"symbol": f"{coin}USDT", "interval": "1d",
                                         "startTime": start, "limit": 1000}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"{coin}: HTTP {r.status_code}")
        batch = r.json()
        if not batch:
            break
        rows += [b for b in batch if b[6] < end_ms]  # closed bars only
        start = batch[-1][0] + 86_400_000
        time.sleep(0.1)
    df = pd.DataFrame(rows, columns=range(12)).iloc[:, :6]
    df.columns = ["ts", "open", "high", "low", "close", "volume"]
    df["date"] = pd.to_datetime(df["ts"], unit="ms").dt.strftime("%Y-%m-%d")
    return df[["date", "open", "high", "low", "close", "volume"]]


def cmd_fetch() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    end_ms = int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp() * 1000)
    lines = []
    for c in UNIVERSE:
        try:
            df = fetch(c, end_ms)
        except Exception as e:  # said, never zero
            print(f"{c}: UNREADABLE ({type(e).__name__})")
            continue
        if df.empty:
            print(f"{c}: no Binance USDT history")
            continue
        p = DATA / f"{c}.csv"
        df.to_csv(p, index=False)
        lines.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}")
        print(f"{c}: {len(df)} bars {df.date.iloc[0]}..{df.date.iloc[-1]}")
    (DIR / "data.sha256").write_text("\n".join(lines) + "\n")


def load_panel(data_dir: Path, last: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """open and close panels indexed by date; bars after `last` are never loaded."""
    o, c = {}, {}
    for p in sorted(Path(data_dir).glob("*.csv")):
        df = pd.read_csv(p)
        if last is not None:
            df = df[df["date"] <= last]
        df.index = pd.to_datetime(df["date"])
        o[p.stem], c[p.stem] = df["open"].astype(float), df["close"].astype(float)
    if "BTC" not in o:
        raise ValueError("BTC history is required and unreadable")
    op, cl = pd.DataFrame(o), pd.DataFrame(c)
    full = pd.date_range(cl.index.min(), cl.index.max(), freq="D")
    op, cl = op.reindex(full), cl.reindex(full)
    if op["BTC"].isna().any() or cl["BTC"].isna().any():
        raise ValueError("BTC has missing days; refusing to treat them as zero")
    return op, cl


# ---------------------------------------------------------------- rule
def decide(close: pd.DataFrame, t: int, p: Params) -> pd.Series:
    """Target weights decided at the close of row t from rows <= t only."""
    zero = pd.Series(0.0, index=close.columns)
    btc = close["BTC"]
    if t < p.regime_ma - 1 or not btc.iloc[t - p.regime_ma + 1:t + 1].notna().all():
        return zero
    if not btc.iloc[t] > btc.iloc[t - p.regime_ma + 1:t + 1].mean():
        return zero
    hist = close.iloc[: t + 1].notna().sum()
    elig = [c for c in close.columns
            if hist[c] >= MIN_HISTORY and t >= p.lookback
            and close[c].iloc[t - max(p.lookback, VOL_WIN, PORT_VOL_WIN):t + 1].notna().all()]
    n = len(elig)
    if n < 3:
        return zero
    k = min(4, n // 3)
    mom = pd.Series({c: close[c].iloc[t] / close[c].iloc[t - p.lookback] - 1 for c in elig})
    top = mom.sort_values(ascending=False).head(k)
    top = top[top > 0]
    if top.empty:
        return zero
    rets = close[list(top.index)].pct_change()
    vol = rets.iloc[t - VOL_WIN + 1:t + 1].std() * np.sqrt(365)
    if not (vol > 0).all():
        return zero
    w = (1 / vol) / (1 / vol).sum()
    basket = (rets.iloc[t - PORT_VOL_WIN + 1:t + 1] * w).sum(axis=1)
    pv = basket.std() * np.sqrt(365)
    if not pv > 0:
        return zero
    scale = min(1.0, TARGET_VOL / pv)
    out = zero.copy()
    out[w.index] = w * scale
    return out


def backtest(op: pd.DataFrame, cl: pd.DataFrame, p: Params,
             hold_btc_only: bool = False) -> pd.DataFrame:
    """Net daily returns. Decision at close of row s-1, fill at open of s, held to open s+1."""
    n = len(cl)
    ret = op.shift(-1) / op - 1  # row s: open s -> open s+1
    cols = list(cl.columns)
    w = pd.Series(0.0, index=cols)
    prev_regime = None
    rows = []
    for s in range(1, n - 1):
        t = s - 1
        d = cl.index[t].date()
        # a flip of the BTC gate forces a rebalance
        gate = (t >= p.regime_ma - 1 and cl["BTC"].iloc[t] > cl["BTC"].iloc[t - p.regime_ma + 1:t + 1].mean())
        flip = prev_regime is not None and gate != prev_regime
        prev_regime = gate
        cost = 0.0
        if (d - ANCHOR).days % p.rebalance == 0 or flip:
            if hold_btc_only:
                tgt = pd.Series(0.0, index=cols)
                tgt["BTC"] = 1.0 if gate else 0.0
            else:
                tgt = decide(cl, t, p)
            cost = p.fee * float((tgt - w).abs().sum())
            w = tgt
        held = w[w != 0]
        r = ret.iloc[s]
        if held.size and r[held.index].isna().any():
            raise ValueError(f"unreadable return for held coin(s) on {cl.index[s].date()}")
        gross = float((held * r[held.index]).sum()) if held.size else 0.0
        net = gross - cost - p.funding_per_day * float(held.sum())
        rows.append((cl.index[s], net, gross, cost, float(held.sum())))
        if held.size:
            w = w * (1 + r.fillna(0.0)) / (1 + gross)
    return pd.DataFrame(rows, columns=["date", "net", "gross", "cost", "exposure"]).set_index("date")


def buy_hold(op: pd.DataFrame, cl: pd.DataFrame, equal_weight: bool) -> pd.Series:
    ret = (op.shift(-1) / op - 1).iloc[1:-1]
    if not equal_weight:
        return ret["BTC"]
    hist = cl.notna().cumsum().shift(1).reindex(ret.index)
    elig = (hist >= MIN_HISTORY) & ret.notna()
    return ret.where(elig).mean(axis=1)


# ---------------------------------------------------------------- stats
def sharpe(r: pd.Series) -> float | None:
    r = r.dropna()
    if len(r) < 30 or not r.std() > 0:
        return None
    return float(r.mean() / r.std() * np.sqrt(365))


def max_dd(r: pd.Series) -> float:
    eq = (1 + r).cumprod()
    return float((1 - eq / eq.cummax()).max())


def boot_lo(r: pd.Series, blocks: int = BLOCK, draws: int = DRAWS, seed: int = SEED):
    x = r.dropna().to_numpy()
    n = len(x)
    if n < 2 * blocks:
        return None, None
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(n / blocks))
    out = np.empty(draws)
    for i in range(draws):
        st = rng.integers(0, n - blocks + 1, nb)
        s = np.concatenate([x[a:a + blocks] for a in st])[:n]
        out[i] = s.mean() / s.std() * np.sqrt(365) if s.std() > 0 else np.nan
    return float(np.nanpercentile(out, 5)), float(np.nanpercentile(out, 95))


def by_year(r: pd.Series, exposure: pd.Series | None = None) -> dict:
    res = {}
    for y, g in r.groupby(r.index.year):
        if exposure is not None and not (exposure.loc[g.index] > 0).any():
            continue
        res[int(y)] = float((1 + g).prod() - 1)
    return res


def window(s, a: str, b: str | None = None):
    s = s[s.index >= pd.Timestamp(a)]
    return s if b is None else s[s.index <= pd.Timestamp(b)]


def summarize(r: pd.Series) -> dict:
    lo, hi = boot_lo(r)
    n = len(r)
    return {"days": n, "sharpe": sharpe(r), "ann_return": float((1 + r).prod() ** (365 / n) - 1) if n else None,
            "ann_vol": float(r.std() * np.sqrt(365)), "max_dd": max_dd(r) if n else None,
            "boot90_lo": lo, "boot90_hi": hi}


def evaluate(op: pd.DataFrame, cl: pd.DataFrame, a: str, b: str | None, grid: bool = True) -> dict:
    out = {}
    bt = backtest(op, cl, PRIMARY)
    r = window(bt["net"], a, b)
    ex = window(bt["exposure"], a, b)
    out["primary"] = summarize(r) | {
        "by_year": by_year(r, ex), "avg_exposure": float(ex.mean()),
        "turnover_cost_share_of_gross": float(window(bt["cost"], a, b).sum() / max(window(bt["gross"], a, b).abs().sum(), 1e-12)),
        "cost_per_year": float(window(bt["cost"], a, b).mean() * 365)}
    btc = window(buy_hold(op, cl, False), a, b)
    out["btc_buy_hold"] = summarize(btc)
    out["equal_weight_universe"] = summarize(window(buy_hold(op, cl, True), a, b))
    out["btc_regime_only"] = summarize(window(backtest(op, cl, PRIMARY, hold_btc_only=True)["net"], a, b))
    out["primary_funding_0.03pct_day"] = summarize(window(backtest(op, cl, replace(PRIMARY, funding_per_day=FUNDING_PER_DAY))["net"], a, b))
    out["primary_cost_0.20pct_side"] = summarize(window(backtest(op, cl, replace(PRIMARY, fee=0.002))["net"], a, b))
    if grid:
        out["grid"] = {f"lb{g.lookback}_rb{g.rebalance}_ma{g.regime_ma}":
                       {k: v for k, v in summarize(window(backtest(op, cl, g)["net"], a, b)).items()
                        if k in ("sharpe", "max_dd", "ann_return")} for g in GRID}
    return out


# ---------------------------------------------------------------- protocol
def code_hash() -> str:
    h = hashlib.sha256()
    for p in (ROOT / "scripts/xsmom_regime_study.py", ROOT / "tests/test_xsmom_regime_study.py"):
        h.update(p.read_bytes())
    return h.hexdigest()


def cmd_dev() -> None:
    op, cl = load_panel(DATA, DEV_END)
    res = evaluate(op, cl, DEV_START, DEV_END)
    res["data_last_bar"] = str(cl.index[-1].date())
    (DIR / "result_dev.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


def cmd_holdout() -> None:
    seal, dev = DIR / "code_at_unseal.sha256", DIR / "result_dev.json"
    if (DIR / "holdout_read_at").exists():
        sys.exit("holdout already read once; refusing")
    if not dev.exists() or not seal.exists():
        sys.exit("dev result or code seal missing; refusing")
    if seal.read_text().split()[0] != code_hash():
        sys.exit("code changed since seal; refusing")
    op, cl = load_panel(DATA)
    (DIR / "holdout_read_at").write_text(datetime.now(timezone.utc).isoformat() + "\n")
    res = evaluate(op, cl, HOLD_START, None)
    res["data_last_bar"] = str(cl.index[-1].date())
    # reproduction check: full-data run restricted to dev equals the dev-only run
    full_dev = evaluate(op, cl, DEV_START, DEV_END, grid=False)["primary"]["sharpe"]
    res["dev_sharpe_reproduced_on_full_data"] = full_dev
    (DIR / "result_holdout.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    {"fetch": cmd_fetch, "dev": cmd_dev, "holdout": cmd_holdout}[sys.argv[1]]()
