"""July 2025 backtest of P against observed stETH/ETH discounts.

P was not formed for that window: borrow rates and exit duration were not
read. The verdict is ``not_established``. The observed discount path is
still reported. weETH/ETH is a market ratio, not a discount against an
exchange rate that was not read.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from bot.t2c.numbers import as_decimal, json_num

PAIR_WINDOW_SECONDS = 10800
WINDOW_START = date(2025, 7, 10)
WINDOW_END = date(2025, 7, 31)
PRINTS = Path(__file__).resolve().parents[2] / "benchmark" / "t2c" / "july2025_prints.json"
_BP = Decimal("0.01")


def _corr(left: list[Decimal], right: list[Decimal]) -> Decimal | None:
    n = len(left)
    if n < 4 or n != len(right):
        return None
    mean_l = sum(left, Decimal(0)) / Decimal(n)
    mean_r = sum(right, Decimal(0)) / Decimal(n)
    var_l = Decimal(sum(((item - mean_l) ** 2 for item in left), Decimal(0)))
    var_r = Decimal(sum(((item - mean_r) ** 2 for item in right), Decimal(0)))
    if var_l == 0 or var_r == 0:
        return None
    cov = Decimal(sum(((a - mean_l) * (b - mean_r) for a, b in zip(left, right)), Decimal(0)))
    return cov / (var_l.sqrt() * var_r.sqrt())


def _aligned(series_a: list[Decimal], series_b: list[Decimal], lag: int) -> tuple[list[Decimal], list[Decimal]] | None:
    if lag >= 0:
        a = series_a[: len(series_a) - lag] if lag else series_a
        b = series_b[lag:]
    else:
        a = series_a[-lag:]
        b = series_b[: len(series_b) + lag]
    if len(a) < 4 or len(a) != len(b):
        return None
    return a, b


def lead_verdict(model: Sequence[object] | None, market: Sequence[object] | None) -> dict:
    """Whether the model discount moved before the market discount.

    A positive lag means the model at t lines up with the market at t+lag.
    Simultaneous movement is not a lead. Too few overlapping points, or no
    movement, is ``not_established``.
    """
    if model is None or market is None or len(model) != len(market):
        return {"verdict": "not_established", "reason": "unaligned"}
    pairs: list[tuple[Decimal, Decimal]] = []
    for left, right in zip(model, market):
        a = as_decimal(left)
        b = as_decimal(right)
        if a is None or b is None:
            continue
        pairs.append((a, b))
    if len(pairs) < 8:
        return {"verdict": "not_established", "reason": "too_few_points", "overlap": len(pairs)}
    d_model = [pairs[i][0] - pairs[i - 1][0] for i in range(1, len(pairs))]
    d_market = [pairs[i][1] - pairs[i - 1][1] for i in range(1, len(pairs))]
    if all(item == 0 for item in d_model) or all(item == 0 for item in d_market):
        return {"verdict": "not_established", "reason": "no_movement"}
    best: Decimal | None = None
    best_lag: int | None = None
    at_zero: Decimal | None = None
    for lag in range(-3, 4):
        aligned = _aligned(d_model, d_market, lag)
        if aligned is None:
            continue
        score = _corr(aligned[0], aligned[1])
        if score is None:
            continue
        if lag == 0:
            at_zero = score
        if best is None or abs(score) > abs(best):
            best = score
            best_lag = lag
    if best is None or best_lag is None or abs(best) < Decimal("0.3"):
        return {"verdict": "not_established", "reason": "no_relation"}
    leads = best_lag > 0 and best > 0 and (at_zero is None or best > at_zero)
    return {
        "verdict": "led" if leads else "did_not_lead",
        "lag": best_lag,
        "corr": json_num(best),
    }


def _nearest(rows: list[dict], timestamp: int) -> dict | None:
    best: dict | None = None
    best_gap: int | None = None
    for row in rows:
        gap = abs(int(row["timestamp"]) - timestamp)
        if gap <= PAIR_WINDOW_SECONDS and (best_gap is None or gap < best_gap):
            best = row
            best_gap = gap
    return best


def _day(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()


def _load_prints() -> dict | None:
    try:
        raw = PRINTS.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def july_window() -> dict:
    """Observed discounts, and why P's lead was not established."""
    loaded = _load_prints()
    empty_lead = lead_verdict(None, None)
    base = {
        "verdict": "not_established",
        "reason": (
            "r_borrow and the exit duration for 10-31 Jul 2025 were not read, "
            "so P was not computed and a lead was not established"
        ),
        "lead": empty_lead,
        "window": {"start": WINDOW_START.isoformat(), "end": WINDOW_END.isoformat()},
        "july_31": "absent",
        "weeth_note": (
            "weETH/ETH is the market price in ETH, not a discount against "
            "the exchange rate. The exchange rate was not read for this window."
        ),
        "rows": [],
        "missing_days": [],
        "widest_steth_discount_bp": None,
        "widest_day": None,
    }
    if loaded is None:
        base["reason"] = "the July 2025 prints could not be read"
        return base
    series = loaded.get("series")
    if not isinstance(series, dict):
        base["reason"] = "the July 2025 prints could not be read"
        return base
    eth = series.get("eth")
    steth = series.get("steth")
    weeth = series.get("weeth")
    wsteth = series.get("wsteth")
    if (not isinstance(eth, list) or not isinstance(steth, list)
            or not isinstance(weeth, list) or not isinstance(wsteth, list)):
        base["reason"] = "the July 2025 prints could not be read"
        return base
    rows: list[dict] = []
    for print_row in eth:
        if not isinstance(print_row, dict):
            continue
        try:
            timestamp = int(print_row["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        eth_px = as_decimal(print_row.get("price"))
        st = _nearest(steth, timestamp)
        if eth_px is None or eth_px <= 0 or st is None:
            continue
        st_px = as_decimal(st.get("price"))
        if st_px is None:
            continue
        ratio = st_px / eth_px
        discount_bp = (Decimal(1) - ratio) * Decimal(10000)
        we = _nearest(weeth, timestamp)
        ws = _nearest(wsteth, timestamp)
        we_ratio = None
        ws_ratio = None
        if we is not None:
            we_px = as_decimal(we.get("price"))
            if we_px is not None:
                we_ratio = we_px / eth_px
        if ws is not None:
            ws_px = as_decimal(ws.get("price"))
            if ws_px is not None:
                ws_ratio = ws_px / eth_px
        rows.append({
            "day": _day(timestamp),
            "timestamp": timestamp,
            "steth_over_eth": json_num(ratio),
            "steth_discount_bp": json_num(discount_bp),
            "weeth_over_eth": json_num(we_ratio),
            "wsteth_over_eth": json_num(ws_ratio),
        })
    present = {row["day"] for row in rows}
    missing: list[str] = []
    cursor = WINDOW_START
    while cursor <= WINDOW_END:
        iso = cursor.isoformat()
        if iso not in present:
            missing.append(iso)
        cursor += timedelta(days=1)
    widest: dict | None = None
    widest_bp: Decimal | None = None
    for row in rows:
        bp = as_decimal(row["steth_discount_bp"])
        if bp is None:
            continue
        if widest_bp is None or bp > widest_bp:
            widest = row
            widest_bp = bp
    model = [None for _ in rows]
    market = [row["steth_discount_bp"] for row in rows]
    base["rows"] = rows
    base["missing_days"] = missing
    base["lead"] = lead_verdict(model, market)
    base["july_31"] = "absent" if "2025-07-31" in missing else "present"
    if widest is not None:
        exact = as_decimal(widest["steth_discount_bp"])
        shown = None if exact is None else exact.quantize(_BP, rounding=ROUND_HALF_UP)
        base["widest_steth_discount_bp"] = json_num(shown)
        base["widest_day"] = widest["day"]
    return base
