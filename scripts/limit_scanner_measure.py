#!/usr/bin/env python3
"""Post-hoc rows for the Limit Entry Scanner. Not the forward test.

Prints one JSON object. The window through 2026-10-10 was already read.
Nothing here can pass the pre-registered bar.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.core.limit_scanner import params_from_preset  # noqa: E402
from bot.core.limit_shadow import (  # noqa: E402
    CARD_TOUCH,
    FORWARD_CUTOFF_MS,
    HOLDOUT_START_MS,
    REALISTIC,
    FillSpec,
    book_overlap,
    evaluate_forward,
    load_bars,
    simulate,
    slice_trades,
    summarize,
    variant_params,
)
from bot.core.stock_trading import is_stock_symbol  # noqa: E402
from bot.skills.skill_registry import RunStrategySkill  # noqa: E402

DATASET = ROOT / "benchmark" / "limit_scanner_1h"


def _row(name, params, spec, bars, *, cancel_adverse_bp=0.0):
    out = simulate(bars, params, spec, cancel_adverse_bp=cancel_adverse_bp)
    trades = out["trades"]
    dev = slice_trades(trades, 0, HOLDOUT_START_MS)
    hold = slice_trades(trades, HOLDOUT_START_MS, FORWARD_CUTOFF_MS + 1)
    forward = [t for t in trades if int(t["fill_ms"]) > FORWARD_CUTOFF_MS]
    return {
        "name": name,
        "fill": spec.name,
        "orders": len(out["orders"]),
        "dev": summarize(dev),
        "holdout": summarize(hold),
        "forward": evaluate_forward(forward, funding_priced=False),
        "holdout_symbols": hold,
    }


def main() -> None:
    bars, failed = load_bars(DATASET)
    base = params_from_preset(RunStrategySkill.PRESETS["limit scanner"])
    if base is None:
        raise SystemExit("preset unreadable")
    old = replace(base, volume_rank="minmax", scale_unread_book=False)
    maker = FillSpec("maker_touch")
    rows = [
        _row("old_minmax_touch_card_fees", old, CARD_TOUCH, bars),
        _row("old_minmax_touch_maker", old, maker, bars),
        _row("fixed_touch_maker", base, maker, bars),
        _row("V0_fixed_realistic", *variant_params(base, "V0"), bars),
        _row("V1_reclaim", *variant_params(base, "V1"), bars),
        _row("V2_vol_offset", *variant_params(base, "V2"), bars),
        _row("neutral_tie", replace(base, neutral_on_tie=True), REALISTIC, bars),
        _row("imbalance", replace(base, imbalance_filter=True), REALISTIC, bars),
        _row("cancel_adverse_10bp", base, REALISTIC, bars, cancel_adverse_bp=10.0),
        _row("V3_reclaim_neutral", *variant_params(base, "V3"), bars),
    ]
    from bot.config import US_STOCK_SYMBOLS
    stocks = [s for s in bars if is_stock_symbol(s) or is_stock_symbol(s.split(":")[0])]
    b1v = RunStrategySkill.PRESETS["daily trend"]["symbols"]
    for row in rows:
        held = row.pop("holdout_symbols")
        row["overlap"] = book_overlap(
            [t["symbol"] for t in held], b1v=b1v, stocks=list(US_STOCK_SYMBOLS))
    payload = {
        "label": "POST-HOC. The window through 2026-10-10 was already read. "
                 "These rows cannot pass the forward bar.",
        "dataset_failed": failed,
        "funding": "unpriced",
        "stocks_in_dataset": stocks,
        "variants": rows,
    }
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
