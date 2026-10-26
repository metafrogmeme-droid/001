"""Claw Firewall install scan.

Read-only. The flag is off unless DRAIN_FIREWALL_ENABLED is ``1`` or ``true``.
The web route is the door: a preview may set DRAIN_FIREWALL_FROM_ROUTE=1 in
the child only after flag_on_for has already allowed the caller. This process
never signs and never broadcasts.

The four chains the approval reader already walks come from read_panic. Base
Sepolia, Arbitrum Sepolia and Avalanche Fuji are read through the reader's
``_chain``. Fuji has no EIP-7702, so this module does not ask for a delegation
there. An unreadable chain is named. It is not an empty list.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Mapping, Optional

from bot.approval_hygiene import CHAINS, INVALID_ADDRESS, ChainRead, normalize_address
from bot.approval_reader import ApprovalReader, ExploitDirectory
from bot.panic_button import (
    classify_approval,
    classify_delegation,
    read_panic,
    refuses_secret,
)
from bot.panic_button import (
    flag_on as _panic_flag_shape,
)

FLAG = "DRAIN_FIREWALL_ENABLED"
FROM_ROUTE = "DRAIN_FIREWALL_FROM_ROUTE"
NOT_AVAILABLE = "not available yet"
SECRET_REPLY = (
    "Nothing was stored. We never ask for your seed phrase or private key."
)
UNREAD_LINE = (
    "The approval lists could not be read just now, so there is no list. "
    "This is not a clean result."
)
PARTIAL_LINE = "An unread chain is not a clean result."
COULD_READ = "These are the approvals we could read."

# (chain id, name, whether a 7702 delegation read is meaningful here).
# Fuji is a smart-account chain for this module. The delegation test is Base Sepolia.
TESTNETS: tuple[tuple[int, str, bool], ...] = (
    (84532, "Base Sepolia", True),
    (421614, "Arbitrum Sepolia", True),
    (43113, "Avalanche Fuji", False),
)
SUPPORTS_7702 = frozenset({84532, 421614})


def flag_on(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only for ``1`` or ``true``. ``yes`` and ``on`` stay off."""
    src = env if env is not None else os.environ
    raw = src.get(FLAG)
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true")


def from_route(env: Optional[Mapping[str, str]] = None) -> bool:
    src = env if env is not None else os.environ
    return str(src.get(FROM_ROUTE) or "").strip() == "1"


def _panic_shape_matches() -> bool:
    """The flag words are the panic button's. A drift is a second answer."""
    return _panic_flag_shape({"PANIC_BUTTON": "1"}) is True


def scan_account(
    address: str,
    *,
    reader: ApprovalReader,
    exploits: ExploitDirectory,
    now: int,
) -> dict[str, Any]:
    """Open approvals for one address. The address is not retained here.

    The library does not read the flag. The route and ``main`` do.
    """
    if not _panic_shape_matches():
        raise RuntimeError("flag shape")
    base = read_panic(address, reader=reader, exploits=exploits, now=now)
    items: list[dict[str, Any]] = list(base.get("items") or [])
    unread: list[str] = list(base.get("unread_chains") or [])
    listed: Optional[set[tuple[int, str]]] = None
    if base.get("exploit_list_read") is True:
        try:
            listed = exploits.get()
        except Exception:
            listed = None
            base["exploit_list_read"] = False
    owner = address.lower()
    for chain_id, name, allow_7702 in TESTNETS:
        chain = _read_testnet(reader, chain_id, name, owner)
        if chain is None or chain.status != "ok":
            if name not in unread:
                unread.append(name)
            continue
        for row in chain.approvals:
            rank, why = classify_approval(row, now=now, exploits=listed)
            nft = str(row.nft_id) if row.nft_id is not None else None
            items.append({
                "rank": rank,
                "chain_id": row.chain_id,
                "chain": row.chain_name,
                "kind": "approval_for_all" if row.standard == "erc721_all" else row.standard,
                "token": row.token.lower() if row.token else "",
                "spender": row.spender.lower(),
                "why": why,
                "updated": None,
                "permit2_expires": None,
                "nft_id": nft,
            })
        if allow_7702 and chain_id in SUPPORTS_7702:
            _delegation(reader, chain_id, name, owner, listed, items, unread)
    attempted = [name for _, name in CHAINS] + [name for _, name, _ in TESTNETS]
    every_failed = all(name in unread for name in attempted)
    items.sort(key=lambda it: (it["rank"], it["chain"], it["kind"], it["token"], it["spender"]))
    return {
        "items": items,
        "unread_chains": unread,
        "exploit_list_read": base.get("exploit_list_read") is True,
        "read_failed": every_failed,
        "message": UNREAD_LINE if every_failed else COULD_READ,
        "partial_line": None if every_failed or not unread else PARTIAL_LINE,
    }


def _read_testnet(reader: ApprovalReader, chain_id: int, name: str, owner: str) -> Optional[ChainRead]:
    method = getattr(reader, "_chain", None)
    if not callable(method):
        return None
    try:
        chain = method(chain_id, name, owner)
    except Exception:
        return None
    if not isinstance(chain, ChainRead):
        return None
    return chain


def _delegation(
    reader: ApprovalReader,
    chain_id: int,
    name: str,
    owner: str,
    listed: Optional[set[tuple[int, str]]],
    items: list[dict[str, Any]],
    unread: list[str],
) -> None:
    try:
        status, delegate = reader.delegation(chain_id, owner)
    except Exception:
        status, delegate = "unreadable", None
    if status == "unreadable":
        if name not in unread:
            unread.append(name)
        return
    if status == "delegated" and delegate:
        rank, why = classify_delegation(
            chain_id, delegate, exploits=listed, known_good=frozenset(),
        )
        items.append({
            "rank": rank,
            "chain_id": chain_id,
            "chain": name,
            "kind": "delegation_7702",
            "token": "",
            "spender": delegate.lower(),
            "why": why,
            "updated": None,
            "permit2_expires": None,
            "nft_id": None,
        })


def _stdin_body() -> dict[str, Any]:
    try:
        body = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {"error": "bad request"}
    if not isinstance(body, dict):
        return {"error": "bad request"}
    return body


def main(env: Optional[Mapping[str, str]] = None, stdin: Optional[str] = None) -> int:
    src = env if env is not None else os.environ
    if stdin is not None:
        try:
            body = json.loads(stdin or "{}")
        except json.JSONDecodeError:
            body = {"error": "bad request"}
        if not isinstance(body, dict):
            body = {"error": "bad request"}
    else:
        body = _stdin_body()
    if body.get("error"):
        json.dump({"error": "bad request"}, sys.stdout)
        return 0
    if not flag_on(src) and not from_route(src):
        json.dump({"available": False, "message": NOT_AVAILABLE}, sys.stdout)
        return 0
    got = body.get("address")
    raw = got if isinstance(got, str) else ""
    if refuses_secret(raw):
        json.dump({"available": True, "refused": "secret", "message": SECRET_REPLY}, sys.stdout)
        return 0
    address = normalize_address(raw)
    if address is None:
        json.dump({
            "available": True, "refused": "address", "message": INVALID_ADDRESS,
        }, sys.stdout)
        return 0
    result = scan_account(
        address, reader=ApprovalReader(), exploits=ExploitDirectory(), now=int(time.time()),
    )
    result["available"] = True
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
