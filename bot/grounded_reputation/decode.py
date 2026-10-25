"""Payment logs for a cited transaction.

EIP-3009 ``transferWithAuthorization`` emits ``AuthorizationUsed`` and an
ERC-20 ``Transfer`` in the same transaction. The transaction sender is the
facilitator. The payer is ``AuthorizationUsed.authorizer``, which must equal
``Transfer.from``.

Permit2 through ``x402ExactPermit2Proxy`` is the transaction's ``to``. The
payer is ``Transfer.from`` and the payee is ``Transfer.to``. The facilitator
is again the transaction sender.

A receipt whose status is not 1 is returned with ``status_ok`` false so a
failed transaction is not reported as a missing one.
"""
from __future__ import annotations

from typing import Optional

from bot.grounded_reputation.addrs import as_int, norm_addr, norm_word
from bot.grounded_reputation.chains import PERMIT2_PROXY
from bot.hrc.keccak import keccak256

_TRANSFER = "0x" + keccak256(b"Transfer(address,address,uint256)").hex()
_AUTH = "0x" + keccak256(b"AuthorizationUsed(address,bytes32)").hex()
_PROXY = norm_addr(PERMIT2_PROXY)


def transfer_topic() -> str:
    return _TRANSFER


def authorization_topic() -> str:
    return _AUTH


def decode_receipt(
    receipt: object,
    *,
    chain_id: int,
    block_time: Optional[int] = None,
) -> dict[str, object]:
    """Payments in one receipt. ``readable`` False means the receipt had no status."""
    if not isinstance(receipt, dict):
        return {"readable": False, "status_ok": None, "payments": []}
    status = as_int(receipt.get("status"))
    if status is None:
        return {"readable": False, "status_ok": None, "payments": []}
    status_ok = status == 1
    tx_hash = norm_word(receipt.get("transactionHash"))
    if tx_hash is None:
        tx_hash = norm_word(receipt.get("tx_hash"))
    block = as_int(receipt.get("blockNumber"))
    if block is None:
        block = as_int(receipt.get("block"))
    sender = norm_addr(receipt.get("from"))
    callee = norm_addr(receipt.get("to"))
    logs = receipt.get("logs")
    if not isinstance(logs, list) or tx_hash is None:
        return {"readable": False, "status_ok": status_ok, "payments": []}
    auths = []
    transfers = []
    for raw in logs:
        if not isinstance(raw, dict):
            continue
        topics = raw.get("topics")
        if not isinstance(topics, list) or not topics:
            continue
        topic0 = norm_word(topics[0])
        if topic0 == _AUTH and len(topics) >= 3:
            authorizer = _topic_addr(topics[1])
            nonce = norm_word(topics[2])
            if authorizer is not None and nonce is not None:
                auths.append({"authorizer": authorizer, "nonce": nonce})
        elif topic0 == _TRANSFER and len(topics) == 3:
            payer = _topic_addr(topics[1])
            payee = _topic_addr(topics[2])
            units = _word_int(raw.get("data"))
            token = norm_addr(raw.get("address"))
            log_index = as_int(raw.get("logIndex"))
            if log_index is None:
                log_index = as_int(raw.get("log_index"))
            if payer is None or payee is None or units is None or token is None or log_index is None:
                continue
            transfers.append({
                "payer": payer,
                "payee": payee,
                "raw_units": units,
                "token": token,
                "log_index": log_index,
            })
    used: set[int] = set()
    payments: list[dict[str, object]] = []
    for auth in auths:
        for transfer in transfers:
            index = transfer["log_index"]
            if not isinstance(index, int) or index in used:
                continue
            if transfer["payer"] != auth["authorizer"]:
                continue
            used.add(index)
            payments.append(_payment(
                chain_id=chain_id,
                tx_hash=tx_hash,
                transfer=transfer,
                block=block,
                block_time=block_time,
                method="eip3009",
                facilitator=sender,
                authorizer=auth["authorizer"],
                auth_nonce=auth["nonce"],
                status_ok=status_ok,
            ))
    permit2 = callee is not None and callee == _PROXY
    for transfer in transfers:
        index = transfer["log_index"]
        if not isinstance(index, int) or index in used:
            continue
        method = "permit2" if permit2 else "plain"
        facilitator = sender if permit2 else None
        payments.append(_payment(
            chain_id=chain_id,
            tx_hash=tx_hash,
            transfer=transfer,
            block=block,
            block_time=block_time,
            method=method,
            facilitator=facilitator,
            authorizer=None,
            auth_nonce=None,
            status_ok=status_ok,
        ))
    return {"readable": True, "status_ok": status_ok, "payments": payments}


def _payment(
    *,
    chain_id: int,
    tx_hash: str,
    transfer: dict[str, object],
    block: Optional[int],
    block_time: Optional[int],
    method: str,
    facilitator: Optional[str],
    authorizer: Optional[str],
    auth_nonce: Optional[str],
    status_ok: bool,
) -> dict[str, object]:
    return {
        "chain_id": chain_id,
        "tx_hash": tx_hash,
        "log_index": transfer["log_index"],
        "token": transfer["token"],
        "payer": transfer["payer"],
        "payee": transfer["payee"],
        "raw_units": transfer["raw_units"],
        "decimals": None,
        "block": block,
        "block_time": block_time,
        "method": method,
        "facilitator": facilitator,
        "authorizer": authorizer,
        "auth_nonce": auth_nonce,
        "status_ok": status_ok,
    }


def _topic_addr(topic: object) -> Optional[str]:
    word = norm_word(topic)
    if word is None:
        return None
    return norm_addr("0x" + word[-40:])


def _word_int(data: object) -> Optional[int]:
    if not isinstance(data, str):
        return None
    text = data.strip()
    if text.startswith(("0x", "0X")):
        text = text[2:]
    if text == "" or len(text) > 64:
        return None
    try:
        return int(text, 16)
    except ValueError:
        return None
