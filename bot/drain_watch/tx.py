"""Unsigned revokes. The user's wallet signs these. We never do.

The bytes match Panic Button: approve(spender, 0), setApprovalForAll(operator,
false), Permit2 lockdown of one pair, and a 7702 authorisation to the zero
address with no signature fields. Nothing returned here is signed, and
nothing in this module is stored.
"""

from __future__ import annotations

from typing import Optional

from bot.hrc.keccak import keccak256

PERMIT2 = "0x000000000022d473030f116ddee9f6b43ac78ba3"
ZERO = "0x" + "0" * 40


def selector(signature: str) -> str:
    digest = keccak256(signature.encode("utf-8"))
    return "0x" + digest[:4].hex()


def _word_addr(address: str) -> str:
    body = address.lower().removeprefix("0x")
    return body.rjust(64, "0")


def _word_uint(value: int) -> str:
    if value < 0:
        raise ValueError("uint")
    return f"{value:064x}"


def approve_revoke_data(spender: str) -> str:
    return selector("approve(address,uint256)") + _word_addr(spender) + _word_uint(0)


def approval_for_all_revoke_data(operator: str) -> str:
    return selector("setApprovalForAll(address,bool)") + _word_addr(operator) + _word_uint(0)


def lockdown_data(token: str, spender: str) -> str:
    """Permit2 lockdown of one (token, spender) pair."""
    body = (
        selector("lockdown((address,address)[])")
        + _word_uint(0x20)
        + _word_uint(1)
        + _word_addr(token)
        + _word_addr(spender)
    )
    return body


def erc721_revoke_data(token_id: int) -> str:
    return selector("approve(address,uint256)") + _word_addr(ZERO) + _word_uint(token_id)


def unsigned_tx(
    *,
    kind: str,
    chain_id: int,
    token: str = "",
    spender: str = "",
    nft_id: Optional[int] = None,
) -> Optional[dict]:
    """A plain unsigned transaction, or None when this kind has no revoke.

    A 7702 clear is an authorisation to the zero address. The nonce and the
    signature are the wallet's. They are not filled in here.
    """
    if kind == "delegation_7702":
        return {
            "kind": "authorization",
            "chain_id": chain_id,
            "address": ZERO,
            "unsigned": True,
        }
    value = "0x0"
    if kind == "erc20":
        return {
            "kind": "transaction",
            "to": token,
            "data": approve_revoke_data(spender),
            "value": value,
            "chain_id": chain_id,
            "unsigned": True,
        }
    if kind == "approval_for_all":
        return {
            "kind": "transaction",
            "to": token,
            "data": approval_for_all_revoke_data(spender),
            "value": value,
            "chain_id": chain_id,
            "unsigned": True,
        }
    if kind == "permit2":
        return {
            "kind": "transaction",
            "to": PERMIT2,
            "data": lockdown_data(token, spender),
            "value": value,
            "chain_id": chain_id,
            "unsigned": True,
        }
    if kind == "erc721_single" and nft_id is not None:
        return {
            "kind": "transaction",
            "to": token,
            "data": erc721_revoke_data(nft_id),
            "value": value,
            "chain_id": chain_id,
            "unsigned": True,
        }
    return None


def format_tx(tx: Optional[dict]) -> str:
    """The fields a wallet needs, one per line. No signature, no relay."""
    if not tx:
        return "No unsigned transaction was built for this event."
    if tx.get("kind") == "authorization":
        return "\n".join([
            "kind: authorization",
            f"chain_id: {tx.get('chain_id')}",
            f"address: {tx.get('address')}",
            "nonce: your wallet's current nonce",
            "signature: your wallet's, not ours",
        ])
    return "\n".join([
        "kind: transaction",
        f"to: {tx.get('to')}",
        f"value: {tx.get('value')}",
        f"chain_id: {tx.get('chain_id')}",
        f"data: {tx.get('data')}",
    ])
