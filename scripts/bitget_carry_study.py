#!/usr/bin/env python3
"""Delta-neutral funding carry on Bitget, as pre-registered.

Design and rule: docs/research/prereg/bitget_carry_2026-10/PREREGISTRATION.md.

    python3 scripts/bitget_carry_study.py fetch
    python3 scripts/bitget_carry_study.py evaluate
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "docs" / "research" / "prereg" / "bitget_carry_2026-10"
DATA = DIR / "data"
API = "https://api.bitget.com"
UTC = timezone.utc
DAY_MS = 86_400_000

TOP_N = 20
START = date(2026, 8, 1)
END = date(2026, 10, 29)                 # last day included
REBAL_DAYS = 7
SPOT_SIDE = 0.0010 + 0.0005
PERP_SIDE = 0.0006 + 0.0005
COIN_SIDE = 0.5 * (SPOT_SIDE + PERP_SIDE)   # one side of both half-capital legs
ANNUAL = 365
BAR_PCT = 0.05
BOOT, SEED, BLOCK = 10_000, 7, 7


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


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


def days() -> list[date]:
    return [START + timedelta(days=i) for i in range((END - START).days + 1)]


# ── universe and fetch ─────────────────────────────────────────────────────

def universe(perps: list[dict], spots: list[dict], tickers: list[dict]) -> list[str]:
    """Online crypto perps with a same-symbol spot pair, top TOP_N by readable
    24h turnover; an unreadable turnover ranks after every read one."""
    spot_syms = {s["symbol"] for s in spots if s.get("status") == "online"}
    eligible = {p["symbol"] for p in perps if p.get("symbolType") == "crypto"
                and p.get("status") == "online" and p["symbol"] in spot_syms}
    turnover: dict[str, float] = {}
    for t in tickers:
        try:
            turnover[t["symbol"]] = float(t["turnover24h"])
        except (KeyError, TypeError, ValueError):
            continue
    ranked = sorted(eligible, key=lambda s: (s not in turnover, -turnover.get(s, 0.0), s))
    return ranked[:TOP_N]


def fetch_daily(category: str, symbol: str) -> list[list]:
    rows: dict[int, list] = {}
    cursor = _ms(END + timedelta(days=1))
    while cursor > _ms(START - timedelta(days=1)):
        page = _get(f"/api/v3/market/history-candles?category={category}&symbol={symbol}"
                    f"&interval=1D&limit=100&endTime={cursor}").get("data") or []
        if not page:
            break
        for r in page:
            rows[int(r[0])] = r
        earliest = min(int(r[0]) for r in page)
        if earliest >= cursor:
            break
        cursor = earliest - 1
        time.sleep(0.12)
    lo, hi = _ms(START - timedelta(days=1)), _ms(END)
    return [rows[k] for k in sorted(rows) if lo <= k <= hi]


def fetch_funding(symbol: str) -> list[list]:
    out: dict[int, float] = {}
    for page in range(1, 20):
        rows = _get(f"/api/v2/mix/market/history-fund-rate?symbol={symbol}"
                    f"&productType=USDT-FUTURES&pageSize=100&pageNo={page}").get("data") or []
        if not rows:
            break
        for r in rows:
            out[int(r["fundingTime"])] = float(r["fundingRate"])
        time.sleep(0.12)
    lo, hi = _ms(START), _ms(END + timedelta(days=1))
    return [[k, out[k]] for k in sorted(out) if lo <= k < hi]


def cmd_fetch() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    perps = _get("/api/v3/market/instruments?category=USDT-FUTURES")["data"]
    spots = _get("/api/v3/market/instruments?category=SPOT")["data"]
    tickers = _get("/api/v3/market/tickers?category=USDT-FUTURES")["data"]
    syms = universe(perps, spots, tickers)
    meta = {"fetched_utc": datetime.now(UTC).isoformat(), "universe": syms, "files": {}}
    for s in syms:
        path = DATA / f"{s}.json"
        path.write_text(json.dumps({"symbol": s, "perp": fetch_daily("USDT-FUTURES", s),
                                    "spot": fetch_daily("SPOT", s),
                                    "funding": fetch_funding(s)}))
        meta["files"][s] = hashlib.sha256(path.read_bytes()).hexdigest()
        print(s, flush=True)
    (DATA / "manifest.json").write_text(json.dumps(meta, indent=1))


# ── evaluation ─────────────────────────────────────────────────────────────

def coin_days(d: dict) -> tuple[dict[date, float], dict[date, float], dict[date, int]]:
    """Per UTC day: the coin's carry P&L per unit of capital (half spot long,
    half perp short), the day's funding sum and its settlement count. A day
    without both closes and the previous day's is absent, not zero."""
    def closes(rows):
        return {datetime.fromtimestamp(int(r[0]) / 1000, UTC).date(): float(r[4]) for r in rows}
    sp, pp = closes(d["spot"]), closes(d["perp"])
    fund: dict[date, float] = {}
    count: dict[date, int] = {}
    for t, rate in d["funding"]:
        day = datetime.fromtimestamp(int(t) / 1000, UTC).date()
        fund[day] = fund.get(day, 0.0) + rate
        count[day] = count.get(day, 0) + 1
    pnl: dict[date, float] = {}
    for day in days():
        prev = day - timedelta(days=1)
        if day in sp and prev in sp and day in pp and prev in pp and day in fund:
            r_spot = sp[day] / sp[prev] - 1
            r_perp = pp[day] / pp[prev] - 1
            pnl[day] = 0.5 * fund[day] + 0.5 * (r_spot - r_perp)
    return pnl, fund, count


def books(series: dict[str, dict]) -> dict:
    coins = sorted(series)
    per = {c: coin_days(series[c]) for c in coins}
    ds = days()
    out = {"C1": [], "C2": []}
    left_out = {"coin_days_missing": 0}
    w2: dict[str, float] = {}
    for i, day in enumerate(ds):
        readable = [c for c in coins if day in per[c][0]]
        left_out["coin_days_missing"] += len(coins) - len(readable)
        # C1: equal weight over the coins readable today; costs on the first and
        # last day.
        c1 = float(np.mean([per[c][0][day] for c in readable])) if readable else None
        if c1 is not None:
            if i == 0:
                c1 -= COIN_SIDE
            if i == len(ds) - 1:
                c1 -= COIN_SIDE
            out["C1"].append({"day": day.isoformat(), "pnl": c1})
        # C2: weekly rebalance into the coins with positive mean funding over
        # the previous REBAL_DAYS days; nothing held before the first one.
        cost = 0.0
        if i >= REBAL_DAYS and (i - REBAL_DAYS) % REBAL_DAYS == 0:
            window = ds[i - REBAL_DAYS:i]
            keep = []
            for c in coins:
                f = [per[c][1][d] for d in window if d in per[c][1]]
                if f and np.mean(f) > 0:
                    keep.append(c)
            new = {c: 1.0 / len(keep) for c in keep} if keep else {}
            turnover = sum(abs(new.get(c, 0.0) - w2.get(c, 0.0)) for c in set(new) | set(w2))
            cost = turnover * COIN_SIDE
            w2 = new
        if i == len(ds) - 1:
            cost += sum(w2.values()) * COIN_SIDE
        held = [c for c in w2 if day in per[c][0]]
        c2 = sum(w2[c] * per[c][0][day] for c in held) - cost
        out["C2"].append({"day": day.isoformat(), "pnl": float(c2), "n_held": len(held)})
    return {"rows": out, "left_out": left_out}


def judge(rows: list[dict]) -> dict:
    x = np.array([r["pnl"] for r in rows])
    out = {"n_days": int(len(x)), "annualised": float(x.mean() * ANNUAL) if len(x) else None,
           "lower": None, "passed": False}
    if len(x) >= 2 * BLOCK:
        nb = len(x) // BLOCK
        blocks = x[: nb * BLOCK].reshape(nb, BLOCK)
        rng = np.random.default_rng(SEED)
        draws = blocks[rng.integers(0, nb, size=(BOOT, nb))].mean(axis=(1, 2)) * ANNUAL
        out["lower"] = float(np.percentile(draws, 2.5))
    out["passed"] = bool(out["annualised"] is not None and out["annualised"] >= BAR_PCT
                         and out["lower"] is not None and out["lower"] > 0)
    return out


def cmd_evaluate() -> None:
    meta = json.loads((DATA / "manifest.json").read_text())
    for s, digest in meta["files"].items():
        if hashlib.sha256((DATA / f"{s}.json").read_bytes()).hexdigest() != digest:
            sys.exit(f"{s}: data changed since fetch")
    series = {s: json.loads((DATA / f"{s}.json").read_text()) for s in meta["files"]}
    res = books(series)
    verdict = {k: judge(v) for k, v in res["rows"].items()}
    funding_ann = {}
    for s, d in series.items():
        _, fund, _ = coin_days(d)
        vals = list(fund.values())
        funding_ann[s] = float(np.mean(vals) * ANNUAL) if vals else None
    out = {"verdict": verdict, "left_out": res["left_out"],
           "funding_annualised_per_coin": funding_ann, "rows": res["rows"]}
    (DIR / "result.json").write_text(json.dumps(out, indent=1))
    lines = [f"universe: {', '.join(meta['universe'])}", f"left out: {res['left_out']}"]
    for k, v in verdict.items():
        tag = ("PASS" if v["passed"] else "FAIL") if k == "C2" else "reported only"
        lines.append(f"{k}: {v['n_days']} days, annualised net {v['annualised']}, "
                     f"lower {v['lower']}  {tag}")
    lines.append("funding, annualised gross, per coin: " + ", ".join(
        f"{s} {v:+.1%}" for s, v in sorted(funding_ann.items(), key=lambda kv: -(kv[1] or 0))
        if v is not None))
    (DIR / "report.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    {"fetch": cmd_fetch, "evaluate": cmd_evaluate}[sys.argv[1]]()
