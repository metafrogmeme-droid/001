"""Off-chain feedback file: hash first, then the payment proof.

``keccak256(file bytes)`` must equal the on-chain ``feedbackHash``. A mismatch
is ``proof_tampered`` and the proof inside the file is not used. IPFS URIs are
the exception: a hash mismatch there is recorded and is not treated as tampering.

A file that cannot be read is unresolved. It is not an empty proof.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from bot.grounded_reputation.addrs import as_int, norm_addr, norm_word
from bot.hrc.keccak import keccak256

MAX_BYTES = 65536


def is_ipfs(uri: object) -> bool:
    if not isinstance(uri, str):
        return False
    text = uri.strip().lower()
    return text.startswith("ipfs://") or "/ipfs/" in text


def content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def keccak_hex(content: bytes) -> str:
    return "0x" + keccak256(content).hex()


def _proof_from_body(body: object) -> Optional[dict[str, object]]:
    if not isinstance(body, dict):
        return None
    raw = body.get("proofOfPayment")
    if raw is None:
        raw = body.get("proof_of_payment")
    if not isinstance(raw, dict):
        return None
    tx = raw.get("txHash")
    if tx is None:
        tx = raw.get("tx_hash")
    nonce = raw.get("x402Nonce")
    if nonce is None:
        nonce = raw.get("nonce")
    chain = raw.get("chainId")
    if chain is None:
        chain = raw.get("chain_id")
    sender = raw.get("fromAddress")
    if sender is None:
        sender = raw.get("from_address")
    receiver = raw.get("toAddress")
    if receiver is None:
        receiver = raw.get("to_address")
    tx_word = norm_word(tx)
    nonce_word = norm_word(nonce)
    if tx_word is None and nonce_word is None:
        return None
    return {
        "tx_hash": tx_word,
        "nonce": nonce_word,
        "chain_id": as_int(chain),
        "from_address": norm_addr(sender),
        "to_address": norm_addr(receiver),
    }


def inspect_file(content: Optional[bytes], feedback_hash: object, uri: object) -> dict[str, object]:
    """Hash the bytes before the proof is read.

    ``resolved`` False means the file was not read. ``keccak_ok`` False on a
    non-IPFS URI means the bytes are not the file the rating committed to.
    """
    ipfs = is_ipfs(uri)
    if content is None:
        return {
            "resolved": False,
            "ipfs": ipfs,
            "keccak_ok": None,
            "proof": None,
            "content_sha": None,
            "reason": "fetch_failed",
        }
    if len(content) > MAX_BYTES:
        return {
            "resolved": False,
            "ipfs": ipfs,
            "keccak_ok": None,
            "proof": None,
            "content_sha": None,
            "reason": "too_large",
        }
    digest = keccak_hex(content)
    expected = norm_word(feedback_hash)
    match = expected is not None and digest == expected
    proof = _proof_from_body(_loads(content))
    if not ipfs and not match:
        return {
            "resolved": True,
            "ipfs": False,
            "keccak_ok": False,
            "proof": None,
            "content_sha": content_sha256(content),
            "reason": "hash_mismatch",
        }
    return {
        "resolved": True,
        "ipfs": ipfs,
        "keccak_ok": True if match else None,
        "proof": proof,
        "content_sha": content_sha256(content),
        "reason": None,
    }


def _loads(content: bytes) -> object:
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
