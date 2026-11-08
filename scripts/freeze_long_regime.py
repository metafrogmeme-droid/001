#!/usr/bin/env python3
"""Freeze the datasets of the long-only BTC-trend pre-registration.

Design and rule: docs/research/prereg/long_regime_2026-11/PREREGISTRATION.md.

    python3 scripts/freeze_long_regime.py candidates      # ranked list, by the rule
    python3 -m bot.backtest.snapshot --symbols <list> --timeframe 1h --limit 9001 --out STAGE/1h
    python3 scripts/freeze_long_regime.py btc STAGE       # daily BTC, 90 days a request
    python3 scripts/freeze_long_regime.py assemble STAGE

The universe and its ranking are the timeframe-variants rule
(`scripts/freeze_timeframe_variants.py::rank`), read against every
``benchmark/*/manifest.json`` at the seal, so no coin of an earlier study is
taken. A coin that cannot serve 9,000 closed hours is passed over.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from freeze_timeframe_variants import _get, rank, used_bases  # noqa: E402

from bot.backtest.data_loader import DataLoader  # noqa: E402
from bot.backtest.snapshot import _manifest_path, build_manifest, load_dataset, safe_symbol  # noqa: E402

DIR = ROOT / "docs" / "research" / "prereg" / "long_regime_2026-11"
HOURS, DAYS, TAKE, SHORTLIST = 9_000, 600, 10, 40
BTC = "BTC/USDT:USDT"


def cmd_candidates() -> None:
    perps = _get("/api/v3/market/instruments?category=USDT-FUTURES")
    tickers = _get("/api/v3/market/tickers?category=USDT-FUTURES")
    ranked = rank(perps, tickers, used_bases())[:SHORTLIST]
    (DIR / "candidates.json").write_text(json.dumps(ranked, indent=1) + "\n")
    print(",".join(r["symbol"] for r in ranked))


def cmd_btc(stage: Path) -> None:
    """Closed daily BTC perp bars, paged 90 days a request (Bitget refuses a
    wider daily window, which the snapshot tool asks for), into STAGE/1d."""
    import time
    from datetime import datetime, timezone

    from bot.backtest.models import BacktestBar
    day = 86_400_000
    now = int(time.time() * 1000)
    end = now - now % day                       # the open of today's forming bar
    rows: dict[int, list] = {}
    while len(rows) < DAYS + 1:
        start = end - 90 * day
        got = _get(f"/api/v2/mix/market/history-candles?symbol=BTCUSDT&productType="
                   f"USDT-FUTURES&granularity=1D&startTime={start}&endTime={end}&limit=200")
        if not got:
            break
        for r in got:
            if int(r[0]) + day <= now:          # closed bars only
                rows[int(r[0])] = r
        end = start
    bars = [BacktestBar(timestamp=datetime.fromtimestamp(t / 1000, tz=timezone.utc),
                        open=float(r[1]), high=float(r[2]), low=float(r[3]),
                        close=float(r[4]), volume=float(r[5]), symbol=BTC)
            for t, r in sorted(rows.items())]
    print(f"{len(bars)} closed days, {bars[0].timestamp.date()} .. {bars[-1].timestamp.date()}")
    _write(stage / "1d", "1d", len(bars), {BTC: bars})


def _write(out: Path, tf: str, n: int, keep: dict) -> str:
    out.mkdir(parents=True, exist_ok=True)
    for s, bars in keep.items():
        DataLoader.save_csv(bars, str(out / f"{safe_symbol(s)}.csv.gz"))
    m = build_manifest(out, tf, n, keep)
    _manifest_path(out).write_text(json.dumps(m, indent=2) + "\n")
    return m["dataset_hash"]


def cmd_assemble(stage: Path) -> None:
    ranked = json.loads((DIR / "candidates.json").read_text())
    hours = load_dataset(str(stage / "1h"))
    served = {s for s, bars in hours.items() if len(bars) >= HOURS}
    chosen = [r["symbol"] for r in ranked if r["symbol"] in served][:TAKE]
    if len(chosen) < TAKE:
        sys.exit(f"only {len(chosen)} coins serve {HOURS} hours; widen the shortlist")
    keep = {s: sorted(hours[s], key=lambda b: b.timestamp)[-HOURS:] for s in chosen}
    h1 = _write(ROOT / "benchmark" / "fresh10b_1h", "1h", HOURS, keep)
    days = load_dataset(str(stage / "1d"))[BTC]
    if len(days) < DAYS:
        sys.exit(f"BTC serves {len(days)} closed days, fewer than {DAYS}")
    d1 = _write(ROOT / "benchmark" / "fresh10b_btc_1d", "1d", DAYS,
                {BTC: sorted(days, key=lambda b: b.timestamp)[-DAYS:]})
    last = chosen[-1]
    passed = [r["symbol"] for r in ranked[:[x["symbol"] for x in ranked].index(last)]
              if r["symbol"] not in chosen]
    (DIR / "universe.json").write_text(json.dumps(
        {"chosen": chosen, "passed_over": passed, "fresh10b_1h": h1,
         "fresh10b_btc_1d": d1}, indent=1) + "\n")
    print("fresh10b_1h", h1)
    print("fresh10b_btc_1d", d1)


if __name__ == "__main__":
    if sys.argv[1] == "candidates":
        cmd_candidates()
    elif sys.argv[1] == "btc":
        cmd_btc(Path(sys.argv[2]))
    else:
        cmd_assemble(Path(sys.argv[2]))
