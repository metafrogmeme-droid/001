#!/usr/bin/env python3
"""Weekend moves on Bitget US-stock perps, as pre-registered.

Design, clock, costs and the decision rule are in
docs/research/prereg/weekend_stock_perps_2026-10/PREREGISTRATION.md.

    python3 scripts/weekend_perps_study.py fetch      # public API -> data/, hashes
    python3 scripts/weekend_perps_study.py evaluate   # result.json + report.txt
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "docs" / "research" / "prereg" / "weekend_stock_perps_2026-10"
DATA = DIR / "data"
API = "https://api.bitget.com"
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
HOUR_MS = 3_600_000

HEDGE = "SPYUSDT"
TOP_N = 30
LAUNCHED_BY = datetime(2026, 2, 15, 23, 59, tzinfo=UTC)
FIRST_FRIDAY = date(2026, 2, 20)
LAST_MONDAY = date(2026, 10, 26)
HOLDOUT_FROM = date(2026, 7, 1)          # Fridays on or after this are holdout
COST_PER_SIDE = 0.0010                   # taker 0.06% + slippage 0.04%, per leg
SENSITIVITY = (0.0005, 0.0020)
BOOT, SEED = 10_000, 7
LOWER_PCT = 100 * 0.05 / 3 / 2           # 0.833rd percentile: 95% two-sided, Bonferroni 3


def _get(path: str) -> dict:
    for attempt in range(5):
        try:
            with urllib.request.urlopen(API + path, timeout=30) as r:
                d = json.loads(r.read())
            if d.get("code") == "00000":
                return d
            raise RuntimeError(f"{path}: {d.get('code')} {d.get('msg')}")
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


# ── universe and fetch ─────────────────────────────────────────────────────

def universe(instruments: list[dict], tickers: list[dict]) -> list[str]:
    """The pre-registered rule: online stock-type perps launched by the cutoff,
    top TOP_N by 24h quote turnover, the hedge excluded."""
    cutoff = int(LAUNCHED_BY.timestamp() * 1000)
    eligible = {i["symbol"] for i in instruments
                if i.get("symbolType") == "stock" and i.get("status") == "online"
                and i.get("launchTime") and 0 < int(i["launchTime"]) <= cutoff
                and i["symbol"] != HEDGE}
    turnover: dict[str, float] = {}
    for t in tickers:
        try:
            turnover[t["symbol"]] = float(t["turnover24h"])
        except (KeyError, TypeError, ValueError):
            continue          # unreadable turnover is unknown, not zero
    # Readable turnover first, highest first; a symbol whose turnover could not
    # be read ranks after every read one rather than as a zero.
    ranked = sorted(eligible, key=lambda s: (s not in turnover, -turnover.get(s, 0.0), s))
    return ranked[:TOP_N]


def fetch_candles(symbol: str, start_ms: int, end_ms: int) -> list[list]:
    """Hourly bars [open_ms, o, h, l, c, vol, quote_vol] from start to end, paging
    back from the end 100 bars at a time."""
    rows: dict[int, list] = {}
    cursor = end_ms
    while cursor > start_ms:
        d = _get(f"/api/v3/market/history-candles?category=USDT-FUTURES&symbol={symbol}"
                 f"&interval=1H&limit=100&endTime={cursor}")
        page = d.get("data") or []
        if not page:
            break
        for r in page:
            rows[int(r[0])] = r
        earliest = min(int(r[0]) for r in page)
        if earliest >= cursor:
            break
        cursor = earliest - 1
        time.sleep(0.12)
    return [rows[k] for k in sorted(rows) if start_ms <= k <= end_ms]


def fetch_funding(symbol: str, start_ms: int) -> list[list]:
    """[[settlement_ms, rate], ...] back to start, from the v2 paged endpoint."""
    out: dict[int, float] = {}
    page = 1
    while True:
        d = _get(f"/api/v2/mix/market/history-fund-rate?symbol={symbol}"
                 f"&productType=USDT-FUTURES&pageSize=100&pageNo={page}")
        rows = d.get("data") or []
        if not rows:
            break
        for r in rows:
            out[int(r["fundingTime"])] = float(r["fundingRate"])
        if min(int(r["fundingTime"]) for r in rows) < start_ms:
            break
        page += 1
        time.sleep(0.12)
    return [[k, out[k]] for k in sorted(out) if k >= start_ms]


def cmd_fetch() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    instruments = _get("/api/v3/market/instruments?category=USDT-FUTURES")["data"]
    tickers = _get("/api/v3/market/tickers?category=USDT-FUTURES")["data"]
    syms = universe(instruments, tickers)
    start = int(datetime(2026, 2, 19, tzinfo=UTC).timestamp() * 1000)
    end = int(datetime(2026, 10, 27, tzinfo=UTC).timestamp() * 1000)
    meta = {"fetched_utc": datetime.now(UTC).isoformat(), "universe": syms,
            "hedge": HEDGE, "files": {}}
    for s in [HEDGE, *syms]:
        path = DATA / f"{s}.json"
        payload = {"symbol": s, "candles": fetch_candles(s, start, end),
                   "funding": fetch_funding(s, start)}
        path.write_text(json.dumps(payload))
        meta["files"][s] = hashlib.sha256(path.read_bytes()).hexdigest()
        print(s, len(payload["candles"]), "bars", len(payload["funding"]), "settlements",
              flush=True)
    (DATA / "manifest.json").write_text(json.dumps(meta, indent=1))
    print("manifest sha256",
          hashlib.sha256((DATA / "manifest.json").read_bytes()).hexdigest())


# ── evaluation ─────────────────────────────────────────────────────────────

def at_et(d: date, hour: int) -> int:
    """Epoch ms of `hour`:00 ET on day `d`."""
    return int(datetime(d.year, d.month, d.day, hour, tzinfo=ET).timestamp() * 1000)


def close_at(bars: dict[int, list], t_ms: int):
    """Close of the hourly bar that ends at t (opened one hour earlier), or None."""
    b = bars.get(t_ms - HOUR_MS)
    return float(b[4]) if b is not None else None


def weekends() -> list[date]:
    out, f = [], FIRST_FRIDAY
    while f + timedelta(days=3) <= LAST_MONDAY:
        out.append(f)
        f += timedelta(days=7)
    return out


def funding_between(funding: list[list], t0: int, t1: int):
    """Sum of settlements in (t0, t1], and how many there were."""
    rates = [r for t, r in funding if t0 < t <= t1]
    return sum(rates), len(rates)


def legs_cost(n_legs: int, per_side: float) -> float:
    return 2 * n_legs * per_side


def evaluate(series: dict[str, dict], universe_syms: list[str],
             per_side: float = COST_PER_SIDE, fridays: list[date] | None = None,
             window_of=None) -> dict:
    """Per-weekend net P&L for H1/H2 (SPY-hedged, equal weight across symbols)
    and H3 (SPY alone), with what was left out and why.

    ``fridays`` and ``window_of`` default to this study's weekends and its
    discovery/holdout split; the forward test passes its own."""
    if window_of is None:
        def window_of(fri: date) -> str:
            return "holdout" if fri >= HOLDOUT_FROM else "discovery"
    bars = {s: {int(r[0]): r for r in d["candles"]} for s, d in series.items()}
    fund = {s: d["funding"] for s, d in series.items()}
    rows: dict[str, list[dict]] = {"H1": [], "H2": [], "H3": [],
                                   "H1_unhedged": [], "H2_unhedged": []}
    left_out = {"missing_bar": 0, "flat_signal": 0}
    sy_pairs = []
    for fri in (weekends() if fridays is None else fridays):
        A, B, C = at_et(fri, 16), at_et(fri + timedelta(days=2), 20), at_et(fri + timedelta(days=3), 9)
        win = window_of(fri)
        spy = [close_at(bars[HEDGE], t) for t in (A, B, C)]
        if None in spy:
            left_out["missing_bar"] += len(universe_syms)
            continue
        r_spy_ab, r_spy_bc = spy[1] / spy[0] - 1, spy[2] / spy[1] - 1
        f_spy, _ = funding_between(fund[HEDGE], B, C)
        if r_spy_ab != 0:
            side = np.sign(r_spy_ab)
            # A long pays positive funding, a short receives it.
            net = side * r_spy_bc - legs_cost(1, per_side) - side * f_spy
            rows["H3"].append({"friday": fri.isoformat(), "window": win, "pnl": float(net)})
        h1, h2, h1u, h2u = [], [], [], []
        for s in universe_syms:
            p = [close_at(bars[s], t) for t in (A, B, C)]
            if None in p:
                left_out["missing_bar"] += 1
                continue
            r_ab, r_bc = p[1] / p[0] - 1, p[2] / p[1] - 1
            sig, y = r_ab - r_spy_ab, r_bc - r_spy_bc
            if sig == 0:
                left_out["flat_signal"] += 1
                continue
            side = float(np.sign(sig))
            f_s, _ = funding_between(fund[s], B, C)
            # Long symbol + short SPY when side > 0: pay the symbol's funding,
            # receive SPY's; mirrored when side < 0.
            fund_cost = side * (f_s - f_spy)
            gross = side * y
            h1.append(gross - legs_cost(2, per_side) - fund_cost)
            h2.append(-gross - legs_cost(2, per_side) + fund_cost)
            h1u.append(side * r_bc - legs_cost(1, per_side) - side * f_s)
            h2u.append(-side * r_bc - legs_cost(1, per_side) + side * f_s)
            sy_pairs.append((sig, y, win))
        for key, vals in (("H1", h1), ("H2", h2), ("H1_unhedged", h1u), ("H2_unhedged", h2u)):
            if vals:
                rows[key].append({"friday": fri.isoformat(), "window": win,
                                  "pnl": float(np.mean(vals)), "n": len(vals),
                                  "hits": int(sum(v > 0 for v in vals))})
    return {"rows": rows, "left_out": left_out,
            "corr": {w: (float(np.corrcoef([a for a, _, x in sy_pairs if x == w],
                                           [b for _, b, x in sy_pairs if x == w])[0, 1])
                         if sum(1 for *_, x in sy_pairs if x == w) > 2 else None)
                     for w in ("discovery", "holdout")}}


def judge(rows: list[dict]) -> dict:
    disc = np.array([r["pnl"] for r in rows if r["window"] == "discovery"])
    hold = np.array([r["pnl"] for r in rows if r["window"] == "holdout"])
    out = {"n_disc": int(len(disc)), "n_hold": int(len(hold)),
           "disc_mean": float(disc.mean()) if len(disc) else None,
           "hold_mean": float(hold.mean()) if len(hold) else None,
           "hold_lower": None, "passed": False}
    if len(hold) >= 2:
        rng = np.random.default_rng(SEED)
        draws = hold[rng.integers(0, len(hold), size=(BOOT, len(hold)))].mean(axis=1)
        out["hold_lower"] = float(np.percentile(draws, LOWER_PCT))
    out["passed"] = bool(out["disc_mean"] is not None and out["disc_mean"] > 0
                         and out["hold_mean"] is not None and out["hold_mean"] > 0
                         and out["hold_lower"] is not None and out["hold_lower"] > 0)
    return out


def cmd_evaluate() -> None:
    meta = json.loads((DATA / "manifest.json").read_text())
    for s, digest in meta["files"].items():
        got = hashlib.sha256((DATA / f"{s}.json").read_bytes()).hexdigest()
        if got != digest:
            sys.exit(f"{s}: data changed since fetch")
    series = {s: json.loads((DATA / f"{s}.json").read_text()) for s in meta["files"]}
    res = evaluate(series, meta["universe"])
    verdicts = {k: judge(res["rows"][k]) for k in ("H1", "H2", "H3")}
    report = {k: judge(v) for k, v in res["rows"].items()}
    sens = {str(c): {k: judge(evaluate(series, meta["universe"], c)["rows"][k])
                     for k in ("H1", "H2", "H3")} for c in SENSITIVITY}
    vol = {s: {"weekend_bars": 0, "zero_volume": 0} for s in series}
    for s, d in series.items():
        for r in d["candles"]:
            t = datetime.fromtimestamp(int(r[0]) / 1000, ET)
            if t.weekday() >= 5:
                vol[s]["weekend_bars"] += 1
                vol[s]["zero_volume"] += float(r[5]) == 0.0
    out = {"verdicts": verdicts, "all_rules": report, "sensitivity": sens,
           "left_out": res["left_out"], "corr_signal_outcome": res["corr"],
           "weekend_volume": vol, "rows": res["rows"]}
    (DIR / "result.json").write_text(json.dumps(out, indent=1))
    lines = [f"universe ({len(meta['universe'])}): {', '.join(meta['universe'])}",
             f"left out: {res['left_out']}", f"corr(s, y): {res['corr']}", ""]
    for k, v in report.items():
        tag = ("PASS" if v["passed"] else "FAIL") if k in verdicts else "reported only"
        lines.append(f"{k:12} disc {v['disc_mean']!s:>24} (n {v['n_disc']})  hold "
                     f"{v['hold_mean']!s:>24} (n {v['n_hold']})  lower {v['hold_lower']}  {tag}")
    lines.append("")
    for c, vs in sens.items():
        lines.append(f"cost {c}/side: " + ", ".join(
            f"{k} hold {v['hold_mean']:+.5f}" for k, v in vs.items() if v["hold_mean"] is not None))
    (DIR / "report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    {"fetch": cmd_fetch, "evaluate": cmd_evaluate}[sys.argv[1]]()
