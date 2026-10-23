"""Morpho Blue reader, and Euler vaults on Ethereum.

Morpho's live endpoint is ``https://api.morpho.org/graphql``. Euler's is
``https://v3.euler.finance/v3``. A directory on another machine is not a
source. Transports are injected so a test never opens a socket, and a
failed read is unreadable — an empty item list is only returned when the
API said the page was empty.

Morpho build order is Ethereum, then Robinhood Chain, then Base. Euler is
Ethereum only, and the xUSD asset comes from the issuer registry. Hidden
and pending-review vaults are included: the xUSD book is not in the
default visible set. A failed oracle is left out of the oracle sum and
named, not counted as zero. ``y: null`` on a Morpho history point is
unreadable, not zero.
"""

from __future__ import annotations

import json
import urllib.request
from decimal import Decimal
from typing import Callable

from bot.rxb.chains import READ_ORDER
from bot.rxb.registry import load_registry

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


EULER_V3 = "https://v3.euler.finance/v3"
_EULER_VISIBILITY = "visible,warning,hidden,pending_review"


def _xusd_ethereum() -> str | None:
    loaded = load_registry()
    if not loaded["readable"]:
        return None
    for row in loaded["issuers"] or []:
        if not isinstance(row, dict) or row.get("token") != "xUSD":
            continue
        chains = row.get("chains")
        if not isinstance(chains, list):
            return None
        for chain in chains:
            if not isinstance(chain, dict) or chain.get("chain_id") != 1:
                continue
            address = chain.get("address")
            if isinstance(address, str):
                return address
        return None
    return None


def euler_vaults_url(asset: str, offset: int = 0) -> str:
    return (
        f"{EULER_V3}/evk/vaults?chainId=1&asset={asset}&limit=100&offset={offset}"
        f"&visibility={_EULER_VISIBILITY}"
    )


def euler_vault_url(address: str) -> str:
    return f"{EULER_V3}/evk/vaults/1/{address}"


def _blank_euler(reason: str) -> dict:
    return {
        "readable": False,
        "chain": "ethereum",
        "vaults": None,
        "quoted_supply_value": None,
        "oracle_value": None,
        "oracle_complete": False,
        "oracle_omitted": None,
        "reason": reason,
    }


def _decimal_text(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and not value:
        return None
    try:
        return format(Decimal(str(value)), "f")
    except (ArithmeticError, ValueError):
        return None


def _oracle_value(vault: dict) -> str | None:
    oracle = vault.get("oraclePriceRaw")
    if not isinstance(oracle, dict) or oracle.get("queryFailure") is not False:
        return None
    assets = _decimal_text(vault.get("totalAssets"))
    amount_in = _decimal_text(oracle.get("amountIn"))
    amount_out = _decimal_text(oracle.get("amountOutMid"))
    if assets is None or amount_in is None or amount_out is None:
        return None
    base = Decimal(amount_in)
    if base == 0:
        return None
    # Euler's raw oracle quotes amountOut in 1e18 for amountIn raw asset units.
    scaled = Decimal(assets) * Decimal(amount_out) / base / (Decimal(10) ** 18)
    return format(scaled, "f")


def _parse_euler_vault(vault: dict) -> dict | None:
    address = vault.get("address")
    decimals = vault.get("decimals")
    if not isinstance(address, str) or not address:
        return None
    assets = vault.get("totalAssets")
    if isinstance(assets, bool) or not isinstance(assets, (int, str)):
        return None
    return {
        "address": address,
        "symbol": vault.get("symbol") if isinstance(vault.get("symbol"), str) else None,
        "decimals": decimals if isinstance(decimals, int) and not isinstance(decimals, bool) else None,
        "assets": str(assets),
        "supply_value": _decimal_text(vault.get("totalSupplyUsd")),
        "oracle_value": _oracle_value(vault),
    }


def _sum_values(vaults: list[dict], field: str) -> tuple[str | None, bool, list[str]]:
    total = Decimal(0)
    omitted: list[str] = []
    for vault in vaults:
        value = vault.get(field)
        if value is None:
            name = vault.get("symbol") or vault.get("address") or "?"
            omitted.append(str(name))
            continue
        total += Decimal(value)
    if omitted:
        return (None if not any(v.get(field) is not None for v in vaults) else format(total, "f"), False, omitted)
    return format(total, "f"), True, []


def fetch_euler_ethereum(transport: Callable[[str], dict | None], asset: str | None = None) -> dict:
    """xUSD EVK vaults on Ethereum. A failed GET is unreadable, not an empty book."""
    if asset is None:
        asset = _xusd_ethereum()
    if not isinstance(asset, str) or not asset:
        return _blank_euler("asset_unreadable")
    listed = transport(euler_vaults_url(asset))
    if not isinstance(listed, dict):
        return _blank_euler("unreadable")
    rows = listed.get("data")
    meta = listed.get("meta") if isinstance(listed.get("meta"), dict) else None
    if not isinstance(rows, list) or meta is None:
        return _blank_euler("unreadable")
    total = meta.get("total")
    if isinstance(total, bool) or not isinstance(total, int) or total != len(rows):
        longer = isinstance(total, int) and not isinstance(total, bool) and total > len(rows)
        return _blank_euler("incomplete" if longer else "unreadable")
    vaults: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("address"), str):
            return _blank_euler("unreadable")
        detail = transport(euler_vault_url(row["address"]))
        body = detail.get("data") if isinstance(detail, dict) else None
        if not isinstance(body, dict):
            return _blank_euler("unreadable")
        parsed = _parse_euler_vault(body)
        if parsed is None:
            return _blank_euler("unreadable")
        vaults.append(parsed)
    quoted, quoted_complete, quoted_omitted = _sum_values(vaults, "supply_value")
    oracle, oracle_complete, oracle_omitted = _sum_values(vaults, "oracle_value")
    return {
        "readable": True,
        "chain": "ethereum",
        "asset": asset,
        "vaults": vaults,
        "quoted_supply_value": quoted if quoted_complete else None,
        "quoted_supply_complete": quoted_complete,
        "quoted_supply_omitted": quoted_omitted,
        "oracle_value": oracle,
        "oracle_complete": oracle_complete,
        "oracle_omitted": oracle_omitted,
        "read_at": meta.get("timestamp") if isinstance(meta.get("timestamp"), str) else None,
        "reason": None,
    }


def live_euler_get(url: str) -> dict | None:
    return _get(url)


def _get(url: str, timeout: int = 20) -> dict | None:
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "runeclaw-rxb"})
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
    if not isinstance(parsed, dict) or parsed.get("error"):
        return None
    return parsed


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
