"""Morpho Blue / MetaMorpho reader.

The live endpoint is ``https://api.morpho.org/graphql``. A directory on
another machine is not a source. The transport is injected so a test never
opens a socket, and a failed read is unreadable — an empty item list is
only returned when the API said the page was empty.

Build order is Ethereum, then Robinhood Chain, then Base. A page whose
item count equals the page size is not the end of the book: the read stays
incomplete until a short page arrives. ``y: null`` on a history point is
unreadable, not zero. A borrower count the payload does not carry is
``unreadable``, not "many borrowers".
"""

from __future__ import annotations

import json
import urllib.request
from typing import Callable

from bot.rxb.chains import READ_ORDER

MORPHO_URL = "https://api.morpho.org/graphql"
_MAX_BODY = 2_000_000

_MARKETS = """
query RxbMarkets($chainId: Int!, $first: Int!, $skip: Int!) {
  markets(first: $first, skip: $skip, where: { chainId_in: [$chainId] }) {
    items {
      marketId
      collateralAsset { address symbol }
      loanAsset { address symbol }
      state { supplyAssetsUsd borrowAssetsUsd collateralAssetsUsd }
      supplyingVaults { address }
      supplyingVaultV2s { address }
    }
  }
}
"""

Transport = Callable[[str, dict], dict | None]


def _usd(state: object, key: str) -> dict:
    if not isinstance(state, dict) or key not in state or state[key] is None:
        return {"readable": False, "value": None}
    return {"readable": True, "value": str(state[key])}


def _addresses(raw: dict, key: str) -> dict:
    if key not in raw or raw[key] is None:
        return {"readable": False, "values": None}
    rows = raw[key]
    if not isinstance(rows, list):
        return {"readable": False, "values": None}
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("address"), str):
            return {"readable": False, "values": None}
        out.append(row["address"])
    return {"readable": True, "values": out}


def _concentration(raw: dict) -> str:
    if "borrowerCount" not in raw or raw["borrowerCount"] is None:
        return "unreadable"
    count = raw["borrowerCount"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return "unreadable"
    if count == 1:
        return "single_borrower"
    return "not_single"


def parse_market(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    state = raw.get("state") if isinstance(raw.get("state"), dict) else None
    return {
        "market_id": raw.get("marketId") if isinstance(raw.get("marketId"), str) else None,
        "collateral": raw.get("collateralAsset") if isinstance(raw.get("collateralAsset"), dict) else None,
        "loan": raw.get("loanAsset") if isinstance(raw.get("loanAsset"), dict) else None,
        "supply_usd": _usd(state, "supplyAssetsUsd"),
        "borrow_usd": _usd(state, "borrowAssetsUsd"),
        "collateral_usd": _usd(state, "collateralAssetsUsd"),
        "supplying_vaults": _addresses(raw, "supplyingVaults"),
        "supplying_vault_v2s": _addresses(raw, "supplyingVaultV2s"),
        "concentration": _concentration(raw),
    }


def parse_history_point(raw: object) -> dict:
    """One historical point. A null ``y`` is unreadable, not a measured zero."""
    if not isinstance(raw, dict) or "y" not in raw:
        return {"t": None, "y": None, "readable": False}
    y = raw.get("y")
    t = raw.get("x")
    if y is None:
        return {"t": t, "y": None, "readable": False}
    return {"t": t, "y": str(y), "readable": True}


def live_transport(query: str, variables: dict) -> dict | None:
    return _post(MORPHO_URL, query, variables)


def _post(url: str, query: str, variables: dict, timeout: int = 20) -> dict | None:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"content-type": "application/json", "accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_MAX_BODY + 1)
    except Exception:
        return None
    if len(raw) > _MAX_BODY:
        return None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict) or parsed.get("errors"):
        return None
    return parsed


def fetch_markets(chain_id: int, transport: Transport, page_size: int = 100) -> dict:
    """One chain. ``complete`` is false when the last page was full."""
    if page_size <= 0:
        return {"readable": False, "complete": False, "items": None, "reason": "bad_page"}
    items: list[dict] = []
    skip = 0
    while True:
        body = transport(_MARKETS, {"chainId": chain_id, "first": page_size, "skip": skip})
        if not isinstance(body, dict):
            return {"readable": False, "complete": False, "items": None, "reason": "unreadable"}
        markets = body.get("data")
        batch = None
        if isinstance(markets, dict):
            node = markets.get("markets")
            if isinstance(node, dict):
                batch = node.get("items")
        if not isinstance(batch, list):
            return {"readable": False, "complete": False, "items": None, "reason": "unreadable"}
        parsed: list[dict] = []
        for row in batch:
            one = parse_market(row)
            if one is None:
                return {"readable": False, "complete": False, "items": None, "reason": "unreadable"}
            parsed.append(one)
        items.extend(parsed)
        if len(batch) < page_size:
            return {"readable": True, "complete": True, "items": items, "reason": None}
        skip += page_size
        if skip > 10_000:
            return {"readable": True, "complete": False, "items": items, "reason": "page_cap"}


def fetch_all(transport: Transport, page_size: int = 100) -> dict:
    """Ethereum, then Robinhood Chain, then Base. One failure blanks no chain."""
    chains = []
    for name, chain_id in READ_ORDER:
        got = fetch_markets(chain_id, transport, page_size=page_size)
        got["chain"] = name
        got["chain_id"] = chain_id
        chains.append(got)
    readable = all(c["readable"] and c["complete"] for c in chains)
    return {"readable": readable, "chains": chains, "order": [name for name, _cid in READ_ORDER]}
