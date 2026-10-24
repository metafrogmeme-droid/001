"""A vault exit haircut as an RXB look-through node.

The haircut is a weight on ``solve_graph``. This module does not walk a
book. The walk lives in ``app/lib/xnav/depth.js`` and uses the SaR cost
step. A missing haircut is an unreadable node, not a zero look-through.
"""
from __future__ import annotations

from fractions import Fraction

from bot.rxb.metrics import GEdge, GToken, solve_graph

_BPS = 10_000


def _blank(address: str | None, reason: str) -> dict:
    return {
        "address": address,
        "readable": False,
        "lts": None,
        "uf": None,
        "cbf": None,
        "reason": reason,
        "haircut_above_one": None,
    }


def vault_exit_node(address: str, haircut_bps: int | None) -> dict:
    """One ``vault_exit`` node.

    ``haircut_bps`` is the exit cost in basis points of account value.
    Above 10_000 the look-through shortfall caps at one and
    ``haircut_above_one`` says the reading was larger. ``None`` does not
    become zero.
    """
    if not isinstance(address, str) or len(address) != 42 or not address.startswith("0x"):
        return _blank(None, "invalid_vault")
    body = address[2:]
    if len(body) != 40 or any(c not in "0123456789abcdefABCDEF" for c in body):
        return _blank(address, "invalid_vault")
    name = "vault_exit_" + body[:8].lower()
    if haircut_bps is None or isinstance(haircut_bps, bool) or not isinstance(haircut_bps, int):
        solved = solve_graph(
            [GToken(name)],
            [GEdge(src=name, dst=None, usd=None, kind="vault_exit", verif="none", offchain_id="exit")],
        )
        row = dict(solved[name])
        row["address"] = address
        row["haircut_above_one"] = None
        row["reason"] = "haircut_unreadable"
        row["readable"] = False
        return row
    if haircut_bps < 0:
        return _blank(address, "haircut_unreadable")
    above = haircut_bps > _BPS
    capped = _BPS if above else haircut_bps
    haircut = Fraction(capped, _BPS)
    rest = Fraction(1) - haircut
    solved = solve_graph(
        [GToken(name)],
        [
            GEdge(
                src=name, dst=None, usd=None, kind="vault_exit", verif="none",
                weight=haircut, offchain_id="exit_cost",
            ),
            GEdge(
                src=name, dst=None, usd=None, kind="vault_equity", verif="onchain",
                weight=rest, offchain_id="equity",
            ),
        ],
    )
    row = dict(solved[name])
    row["address"] = address
    row["haircut_above_one"] = above
    return row
