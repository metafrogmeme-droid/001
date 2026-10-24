"""ether.fi redemption routes. The limit key is the output token.

eETH and weETH as keys read all zeros. That is a wrong key, not a zero
limit. Instant ETH is a measured zero only when the pool balance and the
floor were both read and the balance is below the floor.

The figures below are a snapshot dated 2026-10-23, not a live fallback.
"""
from __future__ import annotations

from decimal import Decimal

from bot.t2c.numbers import as_decimal, json_num

ETH = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
STETH = "0xae7ab96520de3a18e5e111b5eaab095312d7fe84"
WSTETH = "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0"
WEETH = "0xcd5fe23c85820f7b72d0926fc9b05b43e359b7ee"
EETH = "0x35fa164735182de50811e8e2e824cfb9b6118ac2"

WRONG_KEYS = frozenset({WEETH, EETH})

# Recorded 2026-10-23. Inputs to interpret_limit, not a hardcoded capacity.
RECORDED_AS_OF = "2026-10-23"
RECORDED_ETH_POOL = Decimal(2456)
RECORDED_ETH_FLOOR = Decimal(22647)
RECORDED_STETH_LIMIT = Decimal(5000)
RECORDED_STETH_FEE_BPS = Decimal(10)


def interpret_limit(
    output_token: object,
    raw_limit_eth: object = None,
    pool_eth: object = None,
    floor_eth: object = None,
) -> dict:
    token = str(output_token or "").strip().lower()
    if token in WRONG_KEYS:
        return {
            "capacity_eth": None,
            "reason": "wrong_key",
            "measured_zero": False,
        }
    if token == ETH:
        pool = as_decimal(pool_eth)
        floor = as_decimal(floor_eth)
        if pool is None or floor is None:
            return {"capacity_eth": None, "reason": "unreadable", "measured_zero": False}
        if pool < floor:
            return {"capacity_eth": Decimal(0), "reason": "below_floor", "measured_zero": True}
        return {
            "capacity_eth": pool - floor,
            "reason": "above_floor",
            "measured_zero": False,
        }
    if token == STETH:
        limit = as_decimal(raw_limit_eth)
        if limit is None or limit < 0:
            return {"capacity_eth": None, "reason": "unreadable", "measured_zero": False}
        return {
            "capacity_eth": limit,
            "reason": "limit",
            "measured_zero": limit == 0,
        }
    return {"capacity_eth": None, "reason": "unknown_output", "measured_zero": False}


def cheapest_weeth_exit(
    instant_eth: object,
    steth_limit_eth: object,
    steth_fee_bps: object,
) -> dict:
    """weETH leaves by its cheapest open exit.

    Instant ETH at a measured 0 leaves the stETH route, then Lido's queue.
    The restaking term is 0 on that path. An unread input does not become a fee.
    """
    instant = as_decimal(instant_eth)
    limit = as_decimal(steth_limit_eth)
    fee = as_decimal(steth_fee_bps)
    closed = {"route": None, "fee_bps": None, "restaked": False, "source": "unreadable"}
    if instant is None:
        return closed
    if instant > 0:
        return {"route": "instant_eth", "fee_bps": None, "restaked": False, "source": "instant_open"}
    if limit is None or fee is None or limit < 0 or fee < 0 or fee >= 10000:
        return closed
    if limit == 0:
        return {"route": None, "fee_bps": None, "restaked": False, "source": "steth_route_closed"}
    return {
        "route": "steth_then_lido_queue",
        "fee_bps": fee,
        "restaked": False,
        "source": "cheapest_open_exit",
    }


def recorded_etherfi() -> dict:
    """The 2026-10-23 snapshot, interpreted by the same rule as a live read."""
    eth = interpret_limit(ETH, pool_eth=RECORDED_ETH_POOL, floor_eth=RECORDED_ETH_FLOOR)
    steth = interpret_limit(STETH, raw_limit_eth=RECORDED_STETH_LIMIT)
    return {
        "as_of": RECORDED_AS_OF,
        "source": "recorded_snapshot",
        "eth": {
            "output": ETH,
            "pool_eth": json_num(RECORDED_ETH_POOL),
            "floor_eth": json_num(RECORDED_ETH_FLOOR),
            "instant_eth": json_num(eth["capacity_eth"]),
            "instant_source": eth["reason"],
            "measured_zero": eth["measured_zero"],
        },
        "steth": {
            "output": STETH,
            "limit_eth": json_num(steth["capacity_eth"]),
            "issuer_fee_bps": json_num(RECORDED_STETH_FEE_BPS),
            "issuer_fee_note": "not the Exit Swap fee",
        },
    }
