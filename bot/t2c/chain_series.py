"""Daily Curve, Aave borrow, and Lido queue readings from 1 Jul 2025.

The file stores what an archive call returned. Stake yield for those days
was not in the file, so P is not computed from a zero yield.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from bot.t2c.backtest import lead_verdict
from bot.t2c.numbers import as_decimal, json_num
from bot.t2c.obs import obs_row

ROOT = Path(__file__).resolve().parents[2] / "benchmark" / "t2c" / "daily_chain.json"
RAY = Decimal(10) ** 27
WEI = Decimal(10) ** 18
BPS = Decimal(10000)
RHO = Decimal("0.05")
SIZES = ("100", "1000", "10000")
ABOUT_PCT = Decimal("0.1")


def amm_discount_bps(dx_wei: object, out_wei: object) -> Decimal | None:
    """(dx − eth_out) / dx in bps. A premium is negative and is still a reading."""
    dx = as_decimal(dx_wei)
    out = as_decimal(out_wei)
    if dx is None or out is None or dx <= 0 or out < 0:
        return None
    return (dx - out) / dx * BPS


def borrow_apr(ray: object) -> Decimal | None:
    number = as_decimal(ray)
    if number is None or number < 0:
        return None
    return number / RAY


def queue_eth(wei: object) -> Decimal | None:
    number = as_decimal(wei)
    if number is None or number < 0:
        return None
    return number / WEI


def _load() -> dict | None:
    try:
        document = json.loads(ROOT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict) or not isinstance(document.get("days"), list):
        return None
    return document


def _day_rows(day: dict) -> list[dict]:
    curve_raw = day.get("curve")
    queue_raw = day.get("queue")
    curve = curve_raw if isinstance(curve_raw, dict) else {}
    queue = queue_raw if isinstance(queue_raw, dict) else {}
    onchain = queue_eth(queue.get("unfinalized_wei"))
    borrow = borrow_apr(day.get("borrow_ray"))
    rows = []
    for size in SIZES:
        out = curve.get(size)
        out_dec = as_decimal(out)
        quote = None if out_dec is None else out_dec / WEI
        rows.append(obs_row(
            ts=day.get("day"),
            lst="stETH",
            size=size,
            t_secs_api=None,
            queue_onchain=json_num(onchain),
            r_borrow=json_num(borrow),
            y=None,
            amm_quote=json_num(quote),
            route_best=None if quote is None else "curve_steth_eth",
            p=None,
        ))
    return rows


def exit_result_bps(amm_bps: object, queue_bps: object, t_secs: object) -> Decimal | None:
    """AMM discount minus queue discount minus rho · T, in bps. rho is 5%."""
    amm = as_decimal(amm_bps)
    queue = as_decimal(queue_bps)
    seconds = as_decimal(t_secs)
    if amm is None or queue is None or seconds is None or seconds < 0:
        return None
    charge = RHO * seconds / Decimal(86400) / Decimal(365) * BPS
    return amm - queue - charge


def _widest_10k(days: list[dict]) -> tuple[Decimal | None, str | None]:
    best: Decimal | None = None
    best_day: str | None = None
    dx = Decimal(10000) * WEI
    for day in days:
        curve_raw = day.get("curve")
        curve = curve_raw if isinstance(curve_raw, dict) else {}
        cost = amm_discount_bps(dx, curve.get("10000"))
        if cost is None:
            continue
        if best is None or cost > best:
            best = cost
            day_name = day.get("day")
            best_day = day_name if isinstance(day_name, str) else None
    return best, best_day


def _sanity(days: list[dict]) -> dict:
    ten_k = None
    for day in days:
        if day.get("day") != "2025-07-21":
            continue
        curve_raw = day.get("curve")
        curve = curve_raw if isinstance(curve_raw, dict) else {}
        ten_k = amm_discount_bps(Decimal(10000) * WEI, curve.get("10000"))
    stated = {
        "day": "2025-07-21",
        "size_eth": "10000",
        "stated_sale_cost_pct": "9.8",
        "stated_queue_cost_pct": "0.4 to 0.6",
        "tolerance_pct_points": json_num(ABOUT_PCT),
    }
    if ten_k is None:
        stated["sale_status"] = "not_read"
        stated["measured_sale_pct"] = None
    else:
        measured = ten_k / Decimal(100)
        stated["measured_sale_pct"] = json_num(measured)
        if abs(measured - Decimal("9.8")) <= ABOUT_PCT:
            stated["sale_status"] = "matches_stated"
        else:
            stated["sale_status"] = "outside_stated"
    stated["queue_cost_status"] = "not_read"
    stated["queue_cost_reason"] = (
        "stake yield for 21 Jul 2025 was not read, and the epoch churn for that "
        "day was not read, so the queue cost in bps was not computed"
    )
    return stated


def daily_report() -> dict:
    document = _load()
    if document is None:
        return {
            "verdict": "not_established",
            "reason": "the daily chain series was not read",
            "window": ["2025-07-01", "2026-10-23"],
            "rho": json_num(RHO),
            "rows": [],
            "sanity": _sanity([]),
            "lead": lead_verdict(None, None),
        }
    days = [day for day in document["days"] if isinstance(day, dict)]
    rows: list[dict] = []
    for day in days:
        rows.extend(_day_rows(day))
    ten_k = [row for row in rows if row["size"] == "10000"]
    borrow_days = sum(1 for row in ten_k if row["r_borrow"] is not None)
    curve_days = sum(1 for row in ten_k if row["amm_quote"] is not None)
    not_mined = sum(1 for day in days if day.get("status") == "not_mined")
    error_days = sum(1 for day in days if day.get("status") == "error")
    model: list[object] = [None for _row in ten_k]
    market: list[object] = [row["amm_quote"] for row in ten_k]
    scored = exit_result_bps(None, None, None)
    widest, widest_day = _widest_10k(days)
    return {
        "verdict": "not_established",
        "reason": (
            "Stake yield was not read, so P was not computed and the exit-swap "
            "result at rho 5% was not computed. Curve get_dy and the Aave variable "
            "borrow rate were read where the call returned."
        ),
        "window": ["2025-07-01", "2026-10-23"],
        "queried_at": document.get("queried_at"),
        "days": len(days),
        "curve_10k_days": curve_days,
        "borrow_days": borrow_days,
        "not_mined": not_mined,
        "error_days": error_days,
        "rho": json_num(RHO),
        "result_bps": json_num(scored),
        "widest_10k_discount_bps": json_num(widest),
        "widest_10k_day": widest_day,
        "lead": lead_verdict(model, market),
        "sanity": _sanity(days),
        "july_21": [row for row in rows if row["ts"] == "2025-07-21"],
    }
