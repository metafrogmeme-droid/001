"""Consumers of a fair-value reading.

The keeper calls on ``oracle_cadence_s`` (default 3s, the HIP-3
deployer-actions interval). A mark older than ``oracle_stale_s``
(default 10s) is a fallback, not a fresh ``setOracle``. An unconfirmed
reading is a halt: the blend may already be carrying the print at full
weight, and ``confirmed`` is still false so a consumer can refuse it.

No address and no dollar field is emitted.
"""
from __future__ import annotations

import math

from bot.fvb.chains import TEST_NETWORKS, assert_testnet_chain
from bot.fvb.euler import OP_LIQUIDATE, liquidate_enabled
from bot.fvb.packet import PRICE_SCALE, scale_price


def clamp_to_external(fv: float | None, external: float | None,
                      band_lo: float | None, band_hi: float | None) -> float | None:
    """Clamp a fair value to the external print, plus or minus the band.

    A missing input is omitted. The clamp does not invent a zero.
    """
    if fv is None or external is None or band_lo is None or band_hi is None:
        return None
    if not all(math.isfinite(x) for x in (fv, external, band_lo, band_hi)):
        return None
    if fv <= 0 or external <= 0 or band_hi < band_lo:
        return None
    half = (band_hi - band_lo) / 2.0
    lo = external - half
    hi = external + half
    if fv < lo:
        return lo
    if fv > hi:
        return hi
    return fv


def aggregator_round(fv: float | None) -> int | None:
    """Chainlink-shaped answer. ``None`` so the contract reverts.

    Returning zero would publish a price of zero. This does not.
    """
    return scale_price(fv)


def oracle_proposal(*, mark: float | None, confirmed: bool, reading_ts: float | None,
                    now: float, cadence_s: float, stale_s: float,
                    last_emit_ts: float | None,
                    chain_id: int | None = None) -> dict[str, object] | None:
    """One keeper decision, or ``None`` when the cadence has not elapsed.

    ``chain_id`` defaults to the recorded HyperEVM testnet id. A mainnet
    id raises before any action is returned.
    """
    if cadence_s <= 0 or stale_s <= 0:
        return None
    if last_emit_ts is not None and (now - last_emit_ts) < cadence_s:
        return None
    target = _hyperevm_testnet_id() if chain_id is None else int(chain_id)
    assert_testnet_chain(target)
    fresh = reading_ts is not None and (now - reading_ts) < stale_s
    chain = "hyper_evm_testnet"
    if not confirmed:
        return {"action": "halt", "confirmed": False, "stale": not fresh, "chain": chain,
                "reason": "unconfirmed"}
    if not fresh:
        return {"action": "fallback", "confirmed": True, "stale": True, "chain": chain,
                "reason": "stale"}
    if mark is None or not math.isfinite(mark) or mark <= 0:
        return {"action": "halt", "confirmed": True, "stale": False, "chain": chain,
                "reason": "no mark"}
    return {"action": "setOracle", "mark": mark, "confirmed": True, "stale": False,
            "chain": chain, "scale": PRICE_SCALE}


def morpho_price(collateral_liquidation: float | None) -> int | None:
    """Morpho ``IOracle.price`` input, scaled. ``None`` when unreadable."""
    return scale_price(collateral_liquidation)


def euler_price(collateral_liquidation: float | None, ops: int | None) -> int | None:
    """A liquidation price for an Euler vault, or ``None``.

    ``ops`` is the vault operation mask. When it is missing the price is
    omitted. When liquidate is not enabled the price is omitted too:
    publishing a mark the vault will not liquidate against would read as
    a working adapter. Bit 12 is not liquidate; see ``OP_LIQUIDATE``.
    """
    if ops is None:
        return None
    if not liquidate_enabled(ops):
        return None
    if (ops & OP_LIQUIDATE) == 0:
        return None
    return scale_price(collateral_liquidation)


def _hyperevm_testnet_id() -> int:
    for net in TEST_NETWORKS:
        if net.chain == "hyperliquid" and net.chain_id is not None:
            return net.chain_id
    raise RuntimeError("hyperliquid testnet id missing")
