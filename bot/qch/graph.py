"""A ``btc_custody`` node in the shape the RXB graph already stores.

``edge_kind`` is one of the ``edges.kind`` checks in ``bot/rxb/schema.sql``.
``verif`` is one of that table's ``verif`` checks. This module does not
open the graph database and does not write it.
"""

from __future__ import annotations

_KINDS = frozenset({"holding", "loan", "collateral", "mint_backing"})
_VERIF = frozenset({"onchain", "por", "zk", "signed", "none"})


def custody_node(asset: str, qes_bps: int | None, verif: str, data_source: str, method: str) -> dict:
    """One reserve node. An unknown verif is ``none``, not a made-up class."""
    kind = "mint_backing"
    if kind not in _KINDS:
        kind = "mint_backing"
    checked = verif if verif in _VERIF else "none"
    return {
        "node": "btc_custody",
        "asset": asset,
        "edge_kind": kind,
        "verif": checked,
        "qes_bps": qes_bps,
        "estimate": True,
        "label": "estimate",
        "method": method,
        "data_source": data_source,
    }
