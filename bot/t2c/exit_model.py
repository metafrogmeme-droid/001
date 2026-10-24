"""Exit Swap accounting. wstETH only. Internal balances, no share token.

The fee applies only here:

    f = clamp(10 + 40·U², 5, 100) bps
    the looper receives q·P·(1−f)
    all of f accrues to LPs

Intake of gross notional q·P is capped at 2% of pool ETH per block. A trade
that would exceed the remaining room is refused whole.

This ledger does not move coins. A caller that wants the refusal for a
non-fork chain id goes through ``assert_may_move_funds`` first.
"""
from __future__ import annotations

from decimal import Decimal

from bot.t2c.chain_guard import LOCAL_FORK, assert_may_move_funds
from bot.t2c.consumers import unwind_target
from bot.t2c.numbers import as_decimal, json_num

WSTETH = "wsteth"
ACCEPTED = frozenset({"wsteth", "steth"})
IDLE = "idle"
QUOTING = "quoting"
HOLDING = "holding"
CLAIMABLE = "claimable"
ENTER_EDGE_BPS = Decimal(50)
EXIT_EDGE_BPS = Decimal(20)
INTAKE_NUMERATOR = 2
INTAKE_DENOMINATOR = 100
FEE_BASE_BPS = Decimal(10)
FEE_SLOPE_BPS = Decimal(40)
FEE_FLOOR_BPS = Decimal(5)
FEE_CEILING_BPS = Decimal(100)
BPS = Decimal(10000)


def clamp_fee_bps(raw: object) -> Decimal | None:
    number = as_decimal(raw)
    if number is None:
        return None
    if number < FEE_FLOOR_BPS:
        return FEE_FLOOR_BPS
    if number > FEE_CEILING_BPS:
        return FEE_CEILING_BPS
    return number


def exit_fee_bps(utilization: object) -> Decimal | None:
    """Fee in bps. Unreadable utilization is not the 10 bps at U = 0."""
    u = as_decimal(utilization)
    if u is None or u < 0:
        return None
    return clamp_fee_bps(FEE_BASE_BPS + FEE_SLOPE_BPS * u * u)


def new_ledger(pool_eth: object, block_number: int = 1) -> dict:
    pool = as_decimal(pool_eth)
    return {
        "pool_eth": pool,
        "committed_eth": Decimal(0),
        "lp_fee_eth": Decimal(0),
        "block_number": block_number,
        "block_intake_eth": Decimal(0),
        "claims": [],
        "next_claim": 1,
        "state": IDLE,
    }


def edge_bps(amm_bps: object, queue_bps: object) -> Decimal | None:
    amm = as_decimal(amm_bps)
    queue = as_decimal(queue_bps)
    if amm is None or queue is None:
        return None
    return amm - queue


def note_edge(ledger: dict, amm_bps: object, queue_bps: object) -> dict:
    """Idle to Quoting above 50 bp. Quoting back to Idle under 20 bp.

    An unread edge does not move the state. The band between 20 and 50 holds.
    """
    edge = edge_bps(amm_bps, queue_bps)
    state = ledger.get("state")
    if edge is None:
        return {"state": state, "moved": False, "reason": "unreadable"}
    if state == IDLE and edge > ENTER_EDGE_BPS:
        ledger["state"] = QUOTING
        return {"state": QUOTING, "moved": True, "reason": "edge_above_50bp", "edge_bps": edge}
    if state == QUOTING and edge < EXIT_EDGE_BPS:
        ledger["state"] = IDLE
        return {"state": IDLE, "moved": True, "reason": "edge_under_20bp", "edge_bps": edge}
    return {"state": state, "moved": False, "reason": "unchanged", "edge_bps": edge}


def _utilization(ledger: dict) -> Decimal | None:
    pool = as_decimal(ledger.get("pool_eth"))
    committed = as_decimal(ledger.get("committed_eth"))
    if pool is None or pool <= 0 or committed is None or committed < 0:
        return None
    return committed / pool


def _refuse(reason: str) -> dict:
    return {"filled": False, "reason": reason}


def _block_intake(ledger: dict, block_number: int) -> Decimal | None:
    """ETH already taken in this block. A new block starts at a measured 0.

    An unreadable counter on the current block is None, not zero.
    """
    if block_number != ledger.get("block_number"):
        return Decimal(0)
    taken = as_decimal(ledger.get("block_intake_eth"))
    if taken is None or taken < 0:
        return None
    return taken


def _terms(
    ledger: dict,
    chain_id: int,
    quantity: object,
    price: object,
    block_number: int,
    asset: str,
) -> dict:
    """The quote arithmetic. It does not write the ledger."""
    assert_may_move_funds(chain_id)
    name = str(asset).strip().lower()
    if name not in ACCEPTED:
        return {"ok": False, "refuse": _refuse("lst_unsupported")}
    q = as_decimal(quantity)
    p = as_decimal(price)
    if q is None or p is None or q <= 0 or p <= 0:
        return {"ok": False, "refuse": _refuse("unreadable")}
    taken = _block_intake(ledger, block_number)
    pool = as_decimal(ledger.get("pool_eth"))
    if taken is None or pool is None or pool <= 0:
        return {"ok": False, "refuse": _refuse("unreadable")}
    cap = pool * Decimal(INTAKE_NUMERATOR) / Decimal(INTAKE_DENOMINATOR)
    room = cap - taken
    if room < 0:
        room = Decimal(0)
    fee_bps = exit_fee_bps(_utilization(ledger))
    if fee_bps is None:
        return {"ok": False, "refuse": _refuse("unreadable")}
    gross = q * p
    if gross > room:
        return {"ok": False, "refuse": _refuse("intake_cap")}
    fee = gross * fee_bps / BPS
    return {
        "ok": True,
        "asset": name,
        "gross": gross,
        "fee": fee,
        "fee_bps": fee_bps,
        "proceeds": gross - fee,
        "taken": taken,
    }


def _commit(ledger: dict, block_number: int, terms: dict) -> None:
    ledger["block_number"] = block_number
    ledger["block_intake_eth"] = terms["taken"] + terms["gross"]
    ledger["committed_eth"] = ledger["committed_eth"] + terms["gross"]
    ledger["lp_fee_eth"] = ledger["lp_fee_eth"] + terms["fee"]


def quote_trade(
    ledger: dict,
    chain_id: int,
    quantity: object,
    price: object,
    block_number: int,
    asset: str = WSTETH,
) -> dict:
    """Account one Exit Swap. Refused trades leave the ledger unchanged."""
    assert_may_move_funds(chain_id)
    if str(asset).strip().lower() != WSTETH:
        return _refuse("wsteth_only")
    terms = _terms(ledger, chain_id, quantity, price, block_number, asset)
    if not terms["ok"]:
        refused = terms["refuse"]
        if isinstance(refused, dict):
            return refused
        return _refuse("unreadable")
    _commit(ledger, block_number, terms)
    return {
        "filled": True,
        "reason": "filled",
        "gross_eth": terms["gross"],
        "fee_bps": terms["fee_bps"],
        "fee_eth": terms["fee"],
        "looper_eth": terms["proceeds"],
        "asset": WSTETH,
    }


def quote(
    ledger: dict,
    chain_id: int,
    lst: str,
    amt: object,
    price: object,
    block_number: int,
) -> dict:
    """IExitSwap.quote. A view: the ledger is unchanged."""
    terms = _terms(ledger, chain_id, amt, price, block_number, lst)
    if not terms["ok"]:
        refused = terms["refuse"]
        if isinstance(refused, dict):
            return refused
        return _refuse("unreadable")
    return {
        "filled": False,
        "reason": "quoted",
        "eth_out": terms["proceeds"],
        "fee_eth": terms["fee"],
        "fee_bps": terms["fee_bps"],
        "asset": terms["asset"],
    }


def sell(
    ledger: dict,
    chain_id: int,
    lst: str,
    amt: object,
    min_eth_out: object,
    price: object,
    block_number: int,
) -> dict:
    """Fill only from Quoting. Requests one Lido withdrawal and goes to Holding."""
    assert_may_move_funds(chain_id)
    if ledger.get("state") != QUOTING:
        return _refuse("wrong_state")
    minimum = as_decimal(min_eth_out)
    if minimum is None or minimum < 0:
        return _refuse("unreadable")
    terms = _terms(ledger, chain_id, amt, price, block_number, lst)
    if not terms["ok"]:
        refused = terms["refuse"]
        if isinstance(refused, dict):
            return refused
        return _refuse("unreadable")
    if terms["proceeds"] < minimum:
        return _refuse("min_eth_out")
    _commit(ledger, block_number, terms)
    opened = open_unsteth_claim(ledger, amt, None)
    if not opened["opened"]:
        return _refuse("unreadable")
    ledger["state"] = HOLDING
    return {
        "filled": True,
        "reason": "filled",
        "event": "Bought",
        "request_id": opened["claim"]["id"],
        "eth_out": terms["proceeds"],
        "fee_eth": terms["fee"],
        "fee_bps": terms["fee_bps"],
        "state": HOLDING,
    }


def mark_finalized(ledger: dict, request_id: int) -> dict:
    if ledger.get("state") != HOLDING:
        return {"finalized": False, "reason": "wrong_state"}
    for claim in ledger["claims"]:
        if claim["id"] == request_id and not claim["settled"]:
            ledger["state"] = CLAIMABLE
            return {"finalized": True, "state": CLAIMABLE}
    return {"finalized": False, "reason": "unknown_request"}


def claim(ledger: dict, request_ids: list[int]) -> dict:
    """Claim the open requests and return to Idle."""
    if ledger.get("state") != CLAIMABLE:
        return {"claimed": False, "reason": "wrong_state", "state": ledger.get("state")}
    if not request_ids:
        return {"claimed": False, "reason": "unreadable", "state": CLAIMABLE}
    open_ids = {item["id"] for item in ledger["claims"] if not item["settled"]}
    if set(request_ids) != open_ids:
        return {"claimed": False, "reason": "unknown_request", "state": CLAIMABLE}
    for request_id in request_ids:
        settle_unsteth_claim(ledger, request_id)
    ledger["state"] = IDLE
    return {"claimed": True, "reason": "idle", "state": IDLE}


def open_unsteth_claim(ledger: dict, wsteth_in: object, steth_out: object = None) -> dict:
    """Record wstETH in. stETH out stays null until a figure is actually given."""
    incoming = as_decimal(wsteth_in)
    if incoming is None or incoming <= 0:
        return {"opened": False, "reason": "unreadable"}
    outgoing = as_decimal(steth_out)
    claim = {
        "id": ledger["next_claim"],
        "wsteth_in": incoming,
        "steth_out": outgoing,
        "holder": "looper",
        "settled": False,
    }
    ledger["next_claim"] = ledger["next_claim"] + 1
    ledger["claims"].append(claim)
    return {"opened": True, "claim": claim}


def transfer_unsteth_claim(ledger: dict, claim_id: int, holder: str) -> dict:
    for claim in ledger["claims"]:
        if claim["id"] == claim_id and not claim["settled"]:
            claim["holder"] = holder
            return {"transferred": True, "holder": holder, "steth_out": claim["steth_out"]}
    return {"transferred": False, "reason": "no_such_claim"}


def settle_unsteth_claim(ledger: dict, claim_id: int) -> dict:
    for claim in ledger["claims"]:
        if claim["id"] == claim_id and not claim["settled"]:
            claim["settled"] = True
            return {
                "settled": True,
                "steth_out": claim["steth_out"],
                "wsteth_in": claim["wsteth_in"],
            }
    return {"settled": False, "reason": "no_such_claim"}


def flash_unwind(
    ledger: dict,
    chain_id: int,
    debt_eth: object,
    quantity: object,
    price: object,
    block_number: int,
    steth_out: object = None,
) -> dict:
    """Sell the unwound wstETH at P and repay the flash from the proceeds.

    A shortfall is a refusal. The ledger, including LP fees, is unchanged.
    """
    assert_may_move_funds(chain_id)
    debt = as_decimal(debt_eth)
    if debt is None or debt < 0:
        return _refuse("unreadable")
    snapshot = (
        ledger["lp_fee_eth"],
        ledger["block_intake_eth"],
        ledger["committed_eth"],
        ledger["block_number"],
        ledger["next_claim"],
        len(ledger["claims"]),
    )
    quoted = quote_trade(ledger, chain_id, quantity, price, block_number, WSTETH)
    if not quoted["filled"]:
        return quoted
    proceeds = quoted["looper_eth"]
    if proceeds < debt:
        _restore(ledger, snapshot)
        return _refuse("shortfall")
    claim = open_unsteth_claim(ledger, quantity, steth_out)
    if not claim["opened"]:
        _restore(ledger, snapshot)
        return _refuse("unreadable")
    return {
        "filled": True,
        "reason": "filled",
        "gross_eth": quoted["gross_eth"],
        "fee_eth": quoted["fee_eth"],
        "fee_bps": quoted["fee_bps"],
        "looper_eth": proceeds - debt,
        "debt_eth": debt,
        "claim_id": claim["claim"]["id"],
        "steth_out": claim["claim"]["steth_out"],
    }


def _restore(ledger: dict, snapshot: tuple) -> None:
    (fee, intake, committed, block, nxt, n_claims) = snapshot
    ledger["lp_fee_eth"] = fee
    ledger["block_intake_eth"] = intake
    ledger["committed_eth"] = committed
    ledger["block_number"] = block
    ledger["next_claim"] = nxt
    ledger["claims"] = ledger["claims"][:n_claims]


def exit_cycle() -> dict:
    """Idle, quote, sell, finalize, claim. A separate ledger from the flash demo."""
    ledger = new_ledger(Decimal(1000), block_number=1)
    noted = note_edge(ledger, Decimal(80), Decimal(10))
    viewed = quote(ledger, LOCAL_FORK, "steth", Decimal(1), Decimal("0.99"), 1)
    sold = sell(
        ledger, LOCAL_FORK, "steth", Decimal(1),
        viewed.get("eth_out"), Decimal("0.99"), 1,
    )
    request_id = sold.get("request_id")
    if sold.get("filled") and isinstance(request_id, int):
        mark_finalized(ledger, request_id)
        claimed = claim(ledger, [request_id])
    else:
        claimed = {"claimed": False}
    return {
        "noted": noted["reason"],
        "event": sold.get("event"),
        "claimed": claimed.get("claimed"),
        "state": ledger.get("state"),
    }


def fork_demo() -> dict:
    """One accounted unwind on chain 31337. Nothing is signed or broadcast."""
    ledger = new_ledger(Decimal(1000), block_number=1)
    unwound = flash_unwind(
        ledger, LOCAL_FORK,
        debt_eth=Decimal("0.5"),
        quantity=Decimal(1),
        price=Decimal("0.99"),
        block_number=1,
    )
    steth_out = None
    if unwound.get("filled"):
        transfer_unsteth_claim(ledger, int(unwound["claim_id"]), "buyer")
        settled = settle_unsteth_claim(ledger, int(unwound["claim_id"]))
        steth_out = settled.get("steth_out")
    return {
        "broadcast": False,
        "chain_id": LOCAL_FORK,
        "trade": trade_json(unwound),
        "lp_fee_eth": json_num(as_decimal(ledger.get("lp_fee_eth"))),
        "steth_out": json_num(as_decimal(steth_out)),
        "cycle": exit_cycle(),
        "unwind": unwind_target(),
    }


def trade_json(result: dict) -> dict:
    out = {
        "filled": bool(result.get("filled")),
        "reason": result.get("reason"),
    }
    for key in ("gross_eth", "fee_eth", "looper_eth", "debt_eth", "fee_bps"):
        if key in result:
            out[key] = json_num(as_decimal(result.get(key)))
    if "claim_id" in result:
        out["claim_id"] = result["claim_id"]
    if "steth_out" in result:
        out["steth_out"] = json_num(as_decimal(result.get("steth_out")))
    return out
