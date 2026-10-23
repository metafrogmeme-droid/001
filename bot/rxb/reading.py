"""One token, as an admin reading. Ratios only. No dollar amounts."""

from __future__ import annotations

from bot.rxb.backtest import run_backtest
from bot.rxb.oracle_math import morpho_price, quote_price
from bot.rxb.registry import load_registry, registry_tokens


def price_reading(packet: dict | None) -> dict:
    """A signer packet, or an explicit absence. Missing is not a zero price."""
    if not packet:
        return {"readable": False, "price_wad": None, "morpho_price": None, "reason": "no_packet"}
    wad = quote_price(
        nav=packet.get("nav"),
        v_chain=packet.get("v_chain"),
        v_att=packet.get("v_att"),
        supply=packet.get("supply"),
        p_mkt=packet.get("p_mkt"),
        include_mkt=packet.get("include_mkt"),
        depth_usd=packet.get("depth_usd"),
        delta_s=packet.get("delta_s"),
    )
    if wad is None:
        return {"readable": False, "price_wad": None, "morpho_price": None, "reason": "unreadable"}
    scaled = morpho_price(wad)
    return {
        "readable": True,
        "price_wad": str(wad),
        "morpho_price": None if scaled is None else str(scaled),
        "reason": None,
    }


def node_view(token: str, packet: dict | None = None) -> dict:
    """The look-through for one registry token. Unknown and unread stay null."""
    if not isinstance(token, str) or not token.isascii() or not token.isalnum() or len(token) > 32:
        return {"token": None, "readable": False, "reason": "invalid_token", "price": price_reading(None)}
    reg = load_registry()
    known = {row.get("token"): row for row in (reg["issuers"] or []) if isinstance(row, dict)}
    back = run_backtest()
    found = None
    for row in back.get("published") or []:
        if row.get("token") == token:
            found = row
            break
    price = price_reading(packet)
    if found is not None:
        out = dict(found)
        # Absolute residuals are dollar quantities. The reading is ratios.
        out.pop("v", None)
        out.pop("excess", None)
        out["basis"] = "published_comparison"
        out["in_registry"] = token in known
        out["price"] = price
        out["address"] = _address(known.get(token))
        return out
    if token in known:
        return {
            "token": token,
            "readable": False,
            "reason": "no_lookthrough_inputs",
            "basis": None,
            "cbf": None,
            "uf": None,
            "stale": None,
            "tripped": None,
            "concentration": "unreadable",
            "scc": [],
            "edges": [],
            "in_registry": True,
            "price": price,
            "address": _address(known.get(token)),
        }
    return {
        "token": token,
        "readable": False,
        "reason": "unknown_token",
        "cbf": None,
        "uf": None,
        "tripped": None,
        "price": price_reading(None),
        "in_registry": False,
    }


def _address(row: dict | None) -> str | None:
    if not isinstance(row, dict):
        return None
    chains = row.get("chains")
    if not isinstance(chains, list) or not chains or not isinstance(chains[0], dict):
        return None
    addr = chains[0].get("address")
    return addr if isinstance(addr, str) else None


def listed_tokens() -> list[str]:
    return registry_tokens()
