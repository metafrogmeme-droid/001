#!/usr/bin/env python3
"""Freeze the two datasets of the signal-edge stage 2 pre-registration.

Design: docs/research/prereg/signal_edge_stage2_2026-10/PREREGISTRATION.md.

    python3 scripts/freeze_signal_stage2.py --eth ETHUSDT_60.csv --sol SOLUSDT_60.csv

F1 slices benchmark/majors_1h_tf to the bars after benchmark/majors_1h's
last bar. F2 reads Kraken's 60-minute OHLCVT export (epoch seconds, open,
high, low, close, volume, trades; no header) for ETH from 2021-01-01 and SOL
from 2024-01-01, both through 2025-10-26T23:00Z. Bars are kept as supplied:
a missing hour stays missing and is listed in the manifest.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.backtest.data_loader import DataLoader  # noqa: E402
from bot.backtest.models import BacktestBar  # noqa: E402
from bot.backtest.snapshot import _manifest_path, build_manifest, load_dataset, load_manifest, safe_symbol  # noqa: E402

UTC = timezone.utc
F2_END = datetime(2025, 10, 26, 23, tzinfo=UTC)
F2_START = {"ETH/USDT": datetime(2021, 1, 1, tzinfo=UTC),
            "SOL/USDT": datetime(2024, 1, 1, tzinfo=UTC)}


def kraken_bars(path: Path, symbol: str) -> list[BacktestBar]:
    bars = []
    with path.open(newline="") as f:
        for row in csv.reader(f):
            ts = datetime.fromtimestamp(int(row[0]), UTC)
            if F2_START[symbol] <= ts <= F2_END:
                bars.append(BacktestBar(timestamp=ts, open=float(row[1]), high=float(row[2]),
                                        low=float(row[3]), close=float(row[4]),
                                        volume=float(row[5]), symbol=symbol))
    return sorted(bars, key=lambda b: b.timestamp)


def write(out: Path, per_symbol: dict[str, list[BacktestBar]]) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    for sym, bars in per_symbol.items():
        DataLoader.save_csv(bars, str(out / f"{safe_symbol(sym)}.csv.gz"))
    manifest = build_manifest(out, "1h", max(len(b) for b in per_symbol.values()), per_symbol)
    _manifest_path(out).write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--eth", type=Path, required=True)
    ap.add_argument("--sol", type=Path, required=True)
    a = ap.parse_args()
    tuned_last = max(datetime.fromisoformat(v["last"]) for v in
                     load_manifest(ROOT / "benchmark" / "majors_1h")["symbols"].values())
    fresh = {s: [b for b in bars if b.timestamp > tuned_last]
             for s, bars in load_dataset(ROOT / "benchmark" / "majors_1h_tf").items()}
    m1 = write(ROOT / "benchmark" / "majors_1h_fresh", fresh)
    m2 = write(ROOT / "benchmark" / "kraken_ethsol_1h",
               {"ETH/USDT": kraken_bars(a.eth, "ETH/USDT"),
                "SOL/USDT": kraken_bars(a.sol, "SOL/USDT")})
    for name, m in (("majors_1h_fresh", m1), ("kraken_ethsol_1h", m2)):
        print(name, m["dataset_hash"])
        for s, e in m["symbols"].items():
            print(f"  {s}: {e['bars']} bars {e['first']} .. {e['last']}, {len(e['missing'])} missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
