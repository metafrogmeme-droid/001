#!/usr/bin/env python3
"""Freeze the datasets of the timeframe-variants pre-registration.

Design and rule: docs/research/prereg/timeframe_variants_2026-10/PREREGISTRATION.md.

    python3 scripts/freeze_timeframe_variants.py candidates      # ranked list, by the rule
    # fetch each series with the repo's snapshot tool into STAGE/<tf>
    python3 scripts/freeze_timeframe_variants.py assemble STAGE  # keep the first 10 served in full

The universe: Bitget USDT-M perpetuals with symbolType crypto, online, ranked
by 24 h turnover, whose base coin appears in no ``benchmark/*/manifest.json``.
A coin that cannot serve every series in full is passed over for the next.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.backtest.data_loader import DataLoader  # noqa: E402
from bot.backtest.snapshot import _manifest_path, build_manifest, load_dataset, safe_symbol  # noqa: E402

API = "https://api.bitget.com"
DIR = ROOT / "docs" / "research" / "prereg" / "timeframe_variants_2026-10"
SERIES = {"1h": 9_000, "15m": 20_000, "5m": 20_000}
OUT = {"1h": "fresh10_1h", "15m": "fresh10_15m", "5m": "fresh10_5m"}
TAKE = 10
SHORTLIST = 60


def used_bases() -> set[str]:
    used: set[str] = set()
    for m in sorted((ROOT / "benchmark").glob("*/manifest.json")):
        used |= {s.split("/")[0] for s in json.loads(m.read_text()).get("symbols", {})}
    return used


def rank(perps: list[dict], tickers: list[dict], used: set[str]) -> list[dict]:
    """Eligible perps by readable 24 h turnover, highest first. A ticker whose
    turnover does not read is not ranked: it is no evidence of liquidity."""
    turnover: dict[str, float] = {}
    for t in tickers:
        try:
            turnover[t["symbol"]] = float(t["turnover24h"])
        except (KeyError, TypeError, ValueError):
            continue
    rows = []
    for p in perps:
        sym = p.get("symbol", "")
        base = p.get("baseCoin") or sym.removesuffix("USDT")
        if (p.get("symbolType") == "crypto" and p.get("status") == "online"
                and sym.endswith("USDT") and base not in used and sym in turnover):
            rows.append({"symbol": f"{base}/USDT:USDT", "base": base, "turnover24h": turnover[sym]})
    return sorted(rows, key=lambda r: (-r["turnover24h"], r["base"]))


def _get(path: str) -> list:
    with urllib.request.urlopen(API + path, timeout=30) as r:
        d = json.loads(r.read())
    if d.get("code") != "00000":
        raise RuntimeError(f"{path}: {d.get('code')} {d.get('msg')}")
    return d["data"]


def cmd_candidates() -> None:
    perps = _get("/api/v3/market/instruments?category=USDT-FUTURES")
    tickers = _get("/api/v3/market/tickers?category=USDT-FUTURES")
    ranked = rank(perps, tickers, used_bases())[:SHORTLIST]
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / "candidates.json").write_text(json.dumps(ranked, indent=1) + "\n")
    print(",".join(r["symbol"] for r in ranked))


def pick(ranked: list[dict], served: dict[str, set[str]]) -> list[str]:
    """The first TAKE symbols, in rank order, that every series served in full."""
    full = set.intersection(*served.values()) if served else set()
    return [r["symbol"] for r in ranked if r["symbol"] in full][:TAKE]


def cmd_assemble(stage: Path) -> None:
    ranked = json.loads((DIR / "candidates.json").read_text())
    data = {tf: load_dataset(str(stage / tf)) for tf in SERIES}
    served = {tf: {s for s, bars in d.items() if len(bars) >= SERIES[tf]} for tf, d in data.items()}
    chosen = pick(ranked, served)
    if len(chosen) < TAKE:
        sys.exit(f"only {len(chosen)} coins serve every series in full; widen the shortlist")
    for tf, n in SERIES.items():
        out = ROOT / "benchmark" / OUT[tf]
        out.mkdir(parents=True, exist_ok=True)
        keep = {s: sorted(data[tf][s], key=lambda b: b.timestamp)[-n:] for s in chosen}
        for s, bars in keep.items():
            DataLoader.save_csv(bars, str(out / f"{safe_symbol(s)}.csv.gz"))
        m = build_manifest(out, tf, n, keep)
        _manifest_path(out).write_text(json.dumps(m, indent=2) + "\n")
        print(OUT[tf], m["dataset_hash"])
    passed = [r["symbol"] for r in ranked if r["symbol"] not in chosen
              and ranked.index(r) < ranked.index(next(x for x in ranked if x["symbol"] == chosen[-1]))]
    (DIR / "universe.json").write_text(json.dumps(
        {"chosen": chosen, "passed_over": passed,
         "served_in_full": {tf: sorted(v) for tf, v in served.items()}}, indent=1) + "\n")


if __name__ == "__main__":
    if sys.argv[1] == "candidates":
        cmd_candidates()
    else:
        cmd_assemble(Path(sys.argv[2]))
