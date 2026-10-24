"""Where the cap signal would be read. Names and assets on the public feed.

The dollar sizes are a research snapshot for an admin, dated 2026-10-23.
They are not a live read and they are not on the public payload.
Morpho is first on the price feed. Aave v3 Ethereum is the primary loop-unwind
target, and only as a signal: its prices are set by governance.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from bot.t2c.numbers import as_decimal, json_num

_SUPPLIED_USD = (
    {"venue": "Aave v3", "asset": "weETH", "usd": "3640000000"},
    {"venue": "Aave v3", "asset": "wstETH", "usd": "2420000000"},
    {"venue": "Aave v3", "asset": "rsETH", "usd": "798000000"},
)


def unwind_target() -> dict:
    """Aave v3 Ethereum, the book a loop unwind would read. Not a liquidation price."""
    return {
        "venue": "Aave v3 Ethereum",
        "role": "primary_loop_unwind",
        "signal_only": True,
        "liquidation_price": False,
        "reason": "aave_prices_are_set_by_governance",
    }


def public_consumers() -> dict:
    return {
        "signal_only": True,
        "writes": False,
        "price_feed": ["Morpho", "Aave v3 Core Ethereum", "Aave v3 Lido"],
        "venues": ["Morpho", "Aave v3 Core Ethereum", "Aave v3 Lido"],
        "unwind": unwind_target(),
        "assets": ["weETH", "wstETH", "rsETH", "osETH", "rETH", "cbETH"],
    }


def _collateral_usd(rows: tuple[dict, ...]) -> tuple[Decimal | None, Decimal | None]:
    total = Decimal(0)
    for row in rows:
        number = as_decimal(row.get("usd"))
        if number is None or number < 0:
            return None, None
        total += number
    billions = (total / (Decimal(10) ** 9)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return total, billions


def admin_consumer_context() -> dict:
    total, billions = _collateral_usd(_SUPPLIED_USD)
    return {
        "as_of": "2026-10-23",
        "source": "recorded_snapshot",
        "note": "Aave v3 Ethereum supplied, a research snapshot, not a live read",
        "supplied_usd": [dict(row) for row in _SUPPLIED_USD],
        "target_collateral_usd": json_num(total),
        "target_collateral_billions": json_num(billions),
    }
