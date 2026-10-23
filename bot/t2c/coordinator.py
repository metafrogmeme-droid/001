"""Strictest readable cap wins. This process does not apply the cap.

Recursion Breaker's coordinator is not in this tree. The stub keeps the
interface: unreadable proposals are omitted, never treated as zero, and
``applied`` stays false.
"""
from __future__ import annotations

from decimal import Decimal

from bot.t2c.numbers import as_decimal, json_num


def strictest_cap(proposals: list[dict] | None) -> dict:
    if not proposals:
        return {"cap": None, "applied": False, "sink": "strictest_cap_stub",
                "reason": "none_readable"}
    readable: list[tuple[Decimal, str]] = []
    for proposal in proposals:
        cap = as_decimal(proposal.get("cap"))
        if cap is None:
            continue
        source = str(proposal.get("source") or "unnamed")
        readable.append((cap, source))
    if not readable:
        return {"cap": None, "applied": False, "sink": "strictest_cap_stub",
                "reason": "none_readable"}
    winner = min(readable, key=lambda item: item[0])
    return {
        "cap": winner[0],
        "source": winner[1],
        "applied": False,
        "sink": "strictest_cap_stub",
        "reason": "strictest",
    }


def propose_cap_down(price: object, er: object) -> dict:
    """A cap-down is P/ER when both were read and P is below ER.

    An unread price does not propose 0.
    """
    p = as_decimal(price)
    exchange = as_decimal(er)
    if p is None or exchange is None or exchange <= 0 or p < 0:
        return {"cap": None, "applied": False, "sink": "strictest_cap_stub",
                "reason": "unreadable"}
    if p >= exchange:
        return {"cap": None, "applied": False, "sink": "strictest_cap_stub",
                "reason": "not_a_cap_down"}
    return strictest_cap([{"cap": p / exchange, "source": "t2c"}])


def cap_json(result: dict) -> dict:
    return {
        "ratio": json_num(as_decimal(result.get("cap"))),
        "applied": False,
        "sink": result.get("sink") or "strictest_cap_stub",
        "reason": result.get("reason"),
        "source": result.get("source"),
    }
