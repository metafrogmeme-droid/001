"""Public signal rows.

Built from an allowlist. A price is a feed-unit number. Dollar keys,
a currency sign, and the project token name are refused.
"""
from __future__ import annotations

import math
import re
from typing import Mapping

from bot.fvb.chains import public_chains

LABEL = "Public signals, not advice."
SCHEMA = 1
PRICE_SCALE = 100_000_000

PUBLIC_KEYS = (
    "label", "symbol", "session", "confirmed", "forced",
    "fv", "band_lo", "band_hi", "external", "last_trade",
    "w_ext", "n_eff_min", "borrow_power", "liquidation",
    "oi_scale", "ts", "v", "unit", "basis",
)

SESSION_CODE = {
    "regular": 0,
    "pre": 1,
    "post": 2,
    "overnight": 3,
    "closed": 4,
}

_BANNED_KEYS = {
    "pnl", "pnl_usd", "net_pnl_usd", "realized_pnl", "unrealized_pnl",
    "equity", "equity_usd", "balance", "balance_usd",
    "margin", "margin_usd", "notional", "notional_usd",
    "amount_usd", "usd_value", "vusdt", "collateral_usd",
}
_MONEY_KEY = re.compile(r"(^|_)(usd|usdt)$|^usd_|_usd_")


class PayloadRefused(ValueError):
    """A public row carried a dollar key or a banned string."""


def scale_price(price: float | None) -> int | None:
    """Feed price as an integer at 1e8. ``None`` when there is no price.

    Zero is not a stand-in for a missing price: a non-positive input is
    omitted, and the aggregator reverts rather than answering zero.
    """
    if price is None or not isinstance(price, (int, float)):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return int(round(float(price) * PRICE_SCALE))


def _walk(obj: object, where: str) -> None:
    if isinstance(obj, str):
        if "$" in obj or "rclaw" in obj.lower():
            raise PayloadRefused(where)
        return
    if isinstance(obj, Mapping):
        for key, val in obj.items():
            name = str(key)
            if name in _BANNED_KEYS or _MONEY_KEY.search(name):
                raise PayloadRefused(name)
            _walk(val, name)
        return
    if isinstance(obj, (list, tuple)):
        for item in obj:
            _walk(item, where)


def public_signal(row: Mapping[str, object]) -> dict[str, object]:
    """Copy ``row`` down to the allowlist and refuse a banned payload.

    Extra keys are dropped. They are not forwarded.
    """
    out: dict[str, object] = {}
    for key in PUBLIC_KEYS:
        if key == "label":
            out[key] = LABEL
        elif key == "v":
            out[key] = SCHEMA
        elif key in row:
            out[key] = row[key]
    _walk(out, "row")
    chains = public_chains()
    _walk(chains, "chains")
    return out
