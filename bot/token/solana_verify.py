"""Verify a Solana wallet's ``signMessage()`` proof — Python side.

The Node app already does this at ``app/lib/solana_verify.js``; this is the same
check for the Telegram bot, so ``/linkwallet`` can require proof of key control
instead of trusting a pasted string.

Solana wallets (Phantom/Backpack) sign with ed25519 and a Solana address *is* the
raw 32-byte ed25519 public key, base58-encoded. ``cryptography`` is already a
dependency (``bot/utils/attestation.py``, ``bot/proofofpnl/erc8004.py``), so no
new package is needed.

Non-custodial: this only ever verifies a signed *login message*. It never sees a
private key and never authorizes a transaction.
"""

from __future__ import annotations

import base64
import hmac
import secrets
from typing import Optional

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_MAP = {c: i for i, c in enumerate(_B58_ALPHABET)}

#: A Solana address is a 32-byte ed25519 public key.
PUBKEY_BYTES = 32
#: ed25519 signatures are always 64 bytes.
SIGNATURE_BYTES = 64


def base58_decode(value: str) -> bytes:
    """Decode a base58 string (Bitcoin/Solana alphabet) to bytes.

    Raises ``ValueError`` on any character outside the alphabet.
    """
    s = str(value or "")
    n = 0
    for ch in s:
        v = _B58_MAP.get(ch)
        if v is None:
            raise ValueError(f"invalid base58 character: {ch!r}")
        n = n * 58 + v
    body = bytearray()
    while n > 0:
        body.insert(0, n & 0xFF)
        n >>= 8
    # Each leading '1' encodes one leading zero byte.
    leading_zeros = len(s) - len(s.lstrip("1"))
    return bytes(leading_zeros) + bytes(body)


def is_valid_address(address: str) -> bool:
    """Whether ``address`` decodes to exactly 32 bytes.

    A regex over the base58 alphabet is *not* sufficient: a 32-44 character
    base58 string can decode to anywhere from 23 to 33 bytes, so shape-checking
    alone admits values that are not public keys at all.
    """
    try:
        return len(base58_decode(address)) == PUBKEY_BYTES
    except ValueError:
        return False


def new_nonce() -> str:
    """A single-use, unguessable nonce for a link challenge."""
    return secrets.token_urlsafe(24)


def challenge_message(address: str, nonce: str) -> str:
    """The exact UTF-8 text the user must sign.

    Binds the proof to this wallet, this purpose, and this one nonce, so a
    signature captured from another context cannot be replayed here.
    """
    return (
        "RUNECLAW wallet verification\n"
        f"address: {address}\n"
        f"nonce: {nonce}\n"
        "Signing this proves you control this wallet. "
        "It authorizes no transaction and moves no funds."
    )


def verify_signed_message(
    message: str, signature_b64: str, address_b58: str
) -> bool:
    """Whether ``signature_b64`` over ``message`` was produced by ``address_b58``.

    Returns False rather than raising on any malformed input — a caller cannot
    usefully distinguish "bad base58" from "wrong signature", and both must deny.
    """
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except Exception:  # pragma: no cover - cryptography is a declared dependency
        return False
    try:
        pub = base58_decode(address_b58)
        if len(pub) != PUBKEY_BYTES:
            return False
        sig = base64.b64decode(str(signature_b64 or ""), validate=True)
        if len(sig) != SIGNATURE_BYTES:
            return False
        Ed25519PublicKey.from_public_bytes(pub).verify(sig, message.encode("utf-8"))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
    except Exception:
        return False


def nonce_matches(expected: Optional[str], supplied: Optional[str]) -> bool:
    """Constant-time nonce comparison."""
    if not expected or not supplied:
        return False
    return hmac.compare_digest(str(expected), str(supplied))
