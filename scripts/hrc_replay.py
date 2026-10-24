#!/usr/bin/env python3
"""Replay the HypeBasis tape and check the Day-1 CoreWriter record.

The record is not an input. The report is the CSV plus the frozen
mainnet precompile word. The record is checked on its own: its
operations encode, and depended_on stays false.

    python3 scripts/hrc_replay.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.hrc.backtest import build_report  # noqa: E402
from bot.hrc.corewriter_probe import encode_action, load_record  # noqa: E402

CSV = ROOT / "tests" / "fixtures" / "hypebasis_usdc_hourly.csv"
FIXTURE = ROOT / "tests" / "fixtures" / "hrc_mainnet_precompile.json"
REPORT = ROOT / "benchmark" / "hrc" / "backtest_2026-10-23.json"


def record_encodes(record: dict) -> bool:
    """Each recorded operation encodes. A missing record does not encode."""
    if record.get("readable") is False or record.get("depended_on") is not False:
        return False
    for name in ("supply", "withdraw"):
        row = record.get(name)
        if not isinstance(row, dict):
            return False
        operation = row.get("operation")
        token = row.get("token")
        wei = row.get("wei")
        if type(operation) is not int or type(token) is not int or type(wei) is not int:
            return False
        encoded = encode_action(operation, token, wei)
        if encoded is None or encoded[:4] != bytes([1, 0, 0, 15]):
            return False
    return True


def main() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    report = build_report(CSV, fixture)
    record = load_record()
    # The report was built before the record was read.
    if not record_encodes(record):
        raise SystemExit(2)
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
