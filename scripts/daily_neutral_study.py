#!/usr/bin/env python3
"""Two market-neutral daily rules: a reversal spread (N1) and ETH/BTC (N2).

Protocol and every number: docs/research/prereg/daily_neutral_2026-11/PREREGISTRATION.md.
Research only: no keys, no orders, no network. Reads the frozen xsmom panel.

    python3 scripts/daily_neutral_study.py dev        # development window only
    python3 scripts/daily_neutral_study.py holdout    # once, after code_at_unseal.sha256
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from xsmom_regime_study import (  # noqa: E402
    ANCHOR,
    DATA,
    DEV_END,
    DEV_START,
    FEE,
    HOLD_START,
    MIN_HISTORY,
    buy_hold,
    load_panel,
    max_dd,
    sharpe,
    window,
)

DIR = ROOT / "docs/research/prereg/daily_neutral_2026-11"
REBALANCE, LOOKBACK, MIN_COINS = 7, 7, 10
Z_WIN, Z_IN, Z_HOLD_MAX = 30, 2.0, 20
BLOCK, DRAWS, SEED, LOWER_PCT = 20, 2000, 20261108, 2.5
SHARPE_BAR, DD_BAR, MIN_TRIPS = 0.5, 0.20, 10


# ---------------------------------------------------------------- N1
def reversal(op: pd.DataFrame, cl: pd.DataFrame, short_leg: bool = True) -> pd.DataFrame:
    """Daily net returns of N1. ``short_leg=False`` is the long leg alone
    (reported against the equal-weight universe, deciding nothing)."""
    ret = op.shift(-1) / op - 1
    cols = list(cl.columns)
    seen = cl.notna().cumsum()
    w = pd.Series(0.0, index=cols)
    rows = []
    for s in range(1, len(cl) - 1):
        t = s - 1
        cost = 0.0
        if t >= LOOKBACK and (cl.index[t].date() - ANCHOR).days % REBALANCE == 0:
            now, then = cl.iloc[t], cl.iloc[t - LOOKBACK]
            elig = [c for c in cols if seen[c].iloc[t] >= MIN_HISTORY
                    and pd.notna(now[c]) and pd.notna(then[c]) and then[c] > 0]
            tgt = pd.Series(0.0, index=cols)
            if len(elig) >= MIN_COINS:
                r7 = (now[elig] / then[elig] - 1).sort_values(kind="mergesort")
                k = max(2, int(round(len(elig) / 5)))
                tgt[list(r7.index[:k])] += 0.5 / k
                if short_leg:
                    tgt[elig] -= 0.5 / len(elig)
            cost = FEE * float((tgt - w).abs().sum())
            w = tgt
        held = w[w != 0]
        r = ret.iloc[s]
        if held.size and r[held.index].isna().any():
            raise ValueError(f"unreadable return for a held coin on {cl.index[s].date()}")
        gross = float((held * r[held.index]).sum()) if held.size else 0.0
        rows.append((cl.index[s], gross - cost, gross, cost, float(held.abs().sum())))
        if held.size:
            w = w * (1 + r.fillna(0.0)) / (1 + gross)
    return pd.DataFrame(rows, columns=["date", "net", "gross", "cost", "exposure"]).set_index("date")


# ---------------------------------------------------------------- N2
def eth_btc(op: pd.DataFrame, cl: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Daily net returns of N2 and its round trips."""
    ret = op.shift(-1) / op - 1
    x = np.log(cl["ETH"] / cl["BTC"])
    z = (x - x.rolling(Z_WIN).mean()) / x.rolling(Z_WIN).std()
    state, held, entered = 0, 0, None
    trips: list[dict] = []
    rows = []
    for s in range(1, len(cl) - 1):
        t = s - 1
        zt = z.iloc[t]
        new = state
        if state == 0:
            if pd.notna(zt) and zt <= -Z_IN:
                new = 1
            elif pd.notna(zt) and zt >= Z_IN:
                new = -1
        elif (held >= Z_HOLD_MAX or (pd.notna(zt) and ((state == 1 and zt >= 0)
                                                        or (state == -1 and zt <= 0)))):
            new = 0
        cost = FEE * 1.0 * (abs(new - state) > 0)   # both legs, 0.5 each, traded once
        if new != state:
            if state != 0:
                trips.append({"side": state, "entered": str(entered), "exited": str(cl.index[s].date()),
                              "days": held})
            state, held = new, 0
            entered = cl.index[s].date() if new else None
        rE, rB = ret["ETH"].iloc[s], ret["BTC"].iloc[s]
        if state and (pd.isna(rE) or pd.isna(rB)):
            raise ValueError(f"unreadable ETH or BTC return on {cl.index[s].date()}")
        gross = 0.5 * state * float(rE) - 0.5 * state * float(rB) if state else 0.0
        if state:
            held += 1
        rows.append((cl.index[s], gross - cost, gross, cost, 1.0 if state else 0.0))
    out = pd.DataFrame(rows, columns=["date", "net", "gross", "cost", "exposure"]).set_index("date")
    return out, trips


# ---------------------------------------------------------------- stats
def boot_lower(r: pd.Series) -> float | None:
    """The 2.5th percentile of a 20-day block bootstrap of the Sharpe."""
    x = r.dropna().to_numpy()
    n = len(x)
    if n < 2 * BLOCK:
        return None
    rng = np.random.default_rng(SEED)
    nb = int(np.ceil(n / BLOCK))
    out = np.empty(DRAWS)
    for i in range(DRAWS):
        st = rng.integers(0, n - BLOCK + 1, nb)
        s = np.concatenate([x[a:a + BLOCK] for a in st])[:n]
        out[i] = s.mean() / s.std() * np.sqrt(365) if s.std() > 0 else np.nan
    return float(np.nanpercentile(out, LOWER_PCT))


def years_up(bt: pd.DataFrame) -> tuple[int, int]:
    up = counted = 0
    for _, g in bt.groupby(bt.index.year):
        if not (g["exposure"] > 0).any():
            continue
        counted += 1
        up += int((1 + g["net"]).prod() - 1 > 0)
    return up, counted


def summary(bt: pd.DataFrame, a: str, b: str | None) -> dict:
    w = window(bt, a, b)
    r = w["net"]
    n = len(r)
    gross = float(w["gross"].sum())
    return {"days": n, "sharpe": sharpe(r),
            "ann_return": float((1 + r).prod() ** (365 / n) - 1) if n else None,
            "max_dd": max_dd(r) if n else None, "gross_sum": gross,
            "cost_sum": float(w["cost"].sum()), "avg_exposure": float(w["exposure"].mean()),
            "by_year": {int(y): float((1 + g).prod() - 1) for y, g in r.groupby(r.index.year)}}


def code_hash() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def run(holdout: bool) -> dict:
    op, cl = load_panel(DATA, None if holdout else DEV_END)
    n1 = reversal(op, cl)
    n1_long = reversal(op, cl, short_leg=False)
    n2, trips = eth_btc(op, cl)
    ew = buy_hold(op, cl, True)
    btc = buy_hold(op, cl, False)
    res: dict = {"code_sha256": code_hash(), "panel_last": str(cl.index[-1].date())}
    for name, bt in (("N1", n1), ("N2", n2)):
        dev = summary(bt, DEV_START, DEV_END)
        entry = {"dev": dev}
        if holdout:
            hold = summary(bt, HOLD_START, None)
            r = window(bt["net"], HOLD_START, None)
            lo = boot_lower(r)
            up, counted = years_up(window(bt, DEV_START, None))
            hold_trips = [t for t in trips if t["entered"] >= HOLD_START] if name == "N2" else None
            bars = {
                "sharpe_dev": dev["sharpe"] is not None and dev["sharpe"] >= SHARPE_BAR,
                "sharpe_holdout": hold["sharpe"] is not None and hold["sharpe"] >= SHARPE_BAR,
                "bootstrap_lower_above_0": lo is not None and lo > 0,
                "holdout_dd": hold["max_dd"] is not None and hold["max_dd"] <= DD_BAR,
                "years_up_majority": counted > 0 and up > counted / 2,
            }
            verdict = "PASS" if all(bars.values()) else "FAIL"
            if name == "N2" and len(hold_trips) < MIN_TRIPS:
                verdict = f"NO VERDICT: {len(hold_trips)} holdout round trips, fewer than {MIN_TRIPS}"
            entry |= {"holdout": hold, "bootstrap_lower_2.5": lo, "years_up": [up, counted],
                      "bars": bars, "verdict": verdict,
                      "corr_with_btc_holdout": float(r.corr(window(btc, HOLD_START, None)))}
            if name == "N2":
                entry["holdout_round_trips"] = len(hold_trips)
        if name == "N2":
            entry["round_trips_dev"] = len([t for t in trips if t["entered"] <= DEV_END])
        res[name] = entry
    seg = (HOLD_START, None) if holdout else (DEV_START, DEV_END)
    spread = window(n1_long["net"], *seg) - window(ew, *seg)
    res["N1_long_leg_minus_equal_weight"] = {"window": list(map(str, seg)),
                                             "sharpe": sharpe(spread.dropna())}
    return res


def main() -> None:
    cmd = sys.argv[1]
    if cmd == "dev":
        res = run(False)
        (DIR / "result_dev.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    elif cmd == "holdout":
        sealed = (DIR / "code_at_unseal.sha256").read_text().split()[0]
        if sealed != code_hash():
            sys.exit("the evaluator changed since code_at_unseal.sha256; refusing the holdout")
        if (DIR / "result_holdout.json").exists():
            sys.exit("the holdout was read once already")
        res = run(True)
        (DIR / "result_holdout.json").write_text(json.dumps(res, indent=1, default=str) + "\n")
    else:
        sys.exit(__doc__)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
