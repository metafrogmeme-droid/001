"""Tests for bot/token/solana_verify.py — the /linkwallet ownership proof.

The gate's whole value rests on this: without a verified signature an address is
just a string the user typed, and anyone can paste a staker's address to inherit
their tier. These pin the properties that make the check meaningful.
"""

import base64

import pytest

from bot.token import solana_verify as sv

# Real, well-known Solana addresses — each is a 32-byte ed25519 public key.
SYSTEM_PROGRAM = "11111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\x00"))) + out


def _keypair():
    """A fresh ed25519 keypair as (private_key, base58_address)."""
    crypto = pytest.importorskip("cryptography")  # noqa: F841
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return priv, _b58encode(raw)


def test_base58_decodes_known_addresses():
    assert len(sv.base58_decode(SYSTEM_PROGRAM)) == 32
    assert len(sv.base58_decode(WSOL)) == 32


def test_base58_roundtrips():
    raw = bytes(range(32))
    assert sv.base58_decode(_b58encode(raw)) == raw


def test_base58_preserves_leading_zeros():
    raw = b"\x00\x00" + bytes(range(30))
    assert sv.base58_decode(_b58encode(raw)) == raw


def test_invalid_base58_characters_rejected():
    for bad in ("0OIl", "not+base58", "hello world"):
        with pytest.raises(ValueError):
            sv.base58_decode(bad)


def test_is_valid_address_requires_exactly_32_bytes():
    """A base58 shape check is not enough — this is the F-43 regression.

    A 32-44 character base58 string can decode to anywhere from 23 to 33 bytes,
    so the old `re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}")` admitted values
    that are not public keys at all.
    """
    assert sv.is_valid_address(WSOL)
    # 33 chars, passes the old regex, decodes to only 24 bytes.
    decoy = "ARJrg3Ti675tnwWkiW84C1f2omYtz7Dx7"
    assert len(decoy) >= 32 and len(sv.base58_decode(decoy)) == 24
    assert not sv.is_valid_address(decoy)
    assert not sv.is_valid_address("0OIl")
    assert not sv.is_valid_address("")


def test_valid_signature_verifies():
    priv, addr = _keypair()
    msg = sv.challenge_message(addr, sv.new_nonce())
    sig = base64.b64encode(priv.sign(msg.encode())).decode()
    assert sv.verify_signed_message(msg, sig, addr) is True


def test_signature_is_bound_to_the_message():
    priv, addr = _keypair()
    msg = sv.challenge_message(addr, sv.new_nonce())
    sig = base64.b64encode(priv.sign(msg.encode())).decode()
    assert sv.verify_signed_message(msg + "x", sig, addr) is False


def test_signature_is_bound_to_the_address():
    """The core property: a signature cannot be replayed under another address."""
    priv, addr = _keypair()
    _, other = _keypair()
    msg = sv.challenge_message(addr, sv.new_nonce())
    sig = base64.b64encode(priv.sign(msg.encode())).decode()
    assert sv.verify_signed_message(msg, sig, other) is False


def test_nonce_binds_the_challenge():
    """A signature for one nonce must not satisfy a different challenge."""
    priv, addr = _keypair()
    msg_a = sv.challenge_message(addr, sv.new_nonce())
    msg_b = sv.challenge_message(addr, sv.new_nonce())
    sig_a = base64.b64encode(priv.sign(msg_a.encode())).decode()
    assert sv.verify_signed_message(msg_b, sig_a, addr) is False


def test_malformed_input_denies_rather_than_raises():
    _, addr = sv.new_nonce(), WSOL
    msg = sv.challenge_message(addr, "n")
    for bad in ("", "!!!not-base64", base64.b64encode(b"short").decode()):
        assert sv.verify_signed_message(msg, bad, addr) is False
    assert sv.verify_signed_message(msg, "AA==", "not-an-address") is False


def test_nonces_are_unique():
    assert len({sv.new_nonce() for _ in range(200)}) == 200


def test_nonce_matches_is_constant_time_and_strict():
    assert sv.nonce_matches("abc", "abc") is True
    assert sv.nonce_matches("abc", "abd") is False
    assert sv.nonce_matches("", "") is False
    assert sv.nonce_matches(None, "abc") is False


def test_challenge_message_states_it_authorizes_nothing():
    """Users are being asked to sign something; it must say what it does."""
    msg = sv.challenge_message(WSOL, "nonce123")
    assert WSOL in msg and "nonce123" in msg
    assert "authorizes no transaction" in msg
