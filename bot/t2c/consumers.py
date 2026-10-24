"""Where the cap signal would be read. Names and assets on the public feed.

The dollar sizes are a research snapshot for an admin, dated 2026-10-23.
They are not a live read and they are not on the public payload.
"""
from __future__ import annotations


def public_consumers() -> dict:
    return {
        "signal_only": True,
        "writes": False,
        "venues": ["Aave v3 Core Ethereum", "Aave v3 Lido", "Morpho"],
        "assets": ["weETH", "wstETH", "rsETH", "osETH", "rETH", "cbETH"],
    }


def admin_consumer_context() -> dict:
    return {
        "as_of": "2026-10-23",
        "source": "recorded_snapshot",
        "note": "Aave v3 Ethereum supplied, a research snapshot, not a live read",
        "supplied_usd": [
            {"venue": "Aave v3", "asset": "weETH", "usd": "3640000000"},
            {"venue": "Aave v3", "asset": "wstETH", "usd": "2420000000"},
            {"venue": "Aave v3", "asset": "rsETH", "usd": "798000000"},
        ],
    }
