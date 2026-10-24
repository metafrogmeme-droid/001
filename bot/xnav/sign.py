"""One testnet signature over an XNAV update.

A mainnet chain id, a missing chain, or a flag that is off is refused
before ``XNAV_SIGNING_KEY`` is read. ML-DSA and Falcon are named and
rejected: this build checks secp256k1 only and will not pretend otherwise.
"""
from __future__ import annotations

from typing import Mapping

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey

from bot.fvb.chains import MAINNET_CHAIN_IDS, MainnetRefused, assert_testnet_chain
from bot.fvb.keccak import keccak256
from bot.fvb.sign import SchemeRejected, _recovery_bit, require_scheme
from bot.xnav.config import placeholders
from bot.xnav.flag import flag_on

_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_TESTNET_IDS = frozenset({998, 31337, 1337})


def _u256(n: int) -> bytes:
    if n < 0:
        raise ValueError("negative word")
    return int(n).to_bytes(32, "big")


def _addr(raw: bytes) -> bytes:
    if len(raw) != 20:
        raise ValueError("address")
    return raw.rjust(32, b"\x00")


def parse_address(text: str) -> bytes:
    body = text.strip().lower().removeprefix("0x")
    if len(body) != 40:
        raise ValueError("address")
    return bytes.fromhex(body)


def typehash() -> bytes:
    table = placeholders()
    return keccak256(str(table["eip712_type"]).encode("ascii"))


def digest(
    vault: bytes,
    xnav_bps: int,
    stress_bps: int,
    ts: int,
    chain_id: int,
    verifying: bytes,
) -> bytes:
    """EIP-712 digest. Does not sign and does not read a key."""
    table = placeholders()
    struct_hash = keccak256(
        typehash() + _addr(vault) + _u256(xnav_bps) + _u256(stress_bps) + _u256(ts)
    )
    domain = keccak256(b"".join((
        keccak256(str(table["eip712_domain"]).encode("ascii")),
        keccak256(str(table["eip712_name"]).encode("ascii")),
        keccak256(str(table["eip712_version"]).encode("ascii")),
        _u256(chain_id),
        _addr(verifying),
    )))
    return keccak256(b"\x19\x01" + domain + struct_hash)


def load_signing_key(env: Mapping[str, str]) -> EllipticCurvePrivateKey:
    """Refuse mainnet, an unset chain, and a flag that is off before the key."""
    if not flag_on(env):
        raise MainnetRefused("flag off")
    raw_chain = env.get("XNAV_CHAIN_ID")
    if raw_chain is None or str(raw_chain).strip() == "":
        raise MainnetRefused("no chain")
    chain = int(str(raw_chain).strip())
    if chain in MAINNET_CHAIN_IDS:
        raise MainnetRefused(chain)
    assert_testnet_chain(chain)
    if chain not in _TESTNET_IDS:
        raise MainnetRefused(chain)
    armed = str(env.get("XNAV_SIGN_TESTNET", "")).strip().lower()
    if armed not in ("1", "true"):
        raise MainnetRefused("signing off")
    raw = env.get("XNAV_SIGNING_KEY")
    if raw is None or str(raw).strip() == "":
        raise MainnetRefused("no key")
    return _key_from_hex(str(raw))


def _key_from_hex(raw: str) -> EllipticCurvePrivateKey:
    text = raw.strip().removeprefix("0x").removeprefix("0X")
    number = int(text, 16)
    if number <= 0 or number >= _N:
        raise MainnetRefused("bad key")
    return ec.derive_private_key(number, ec.SECP256K1())


def sign_update(
    vault: bytes,
    xnav_bps: int,
    stress_bps: int,
    ts: int,
    chain_id: int,
    verifying: bytes,
    private: EllipticCurvePrivateKey,
    scheme: str = "secp256k1",
) -> bytes:
    """65-byte secp256k1 signature. The scheme check runs before the signature."""
    chosen = require_scheme(scheme)
    if chosen != "secp256k1":
        raise SchemeRejected(chosen)
    assert_testnet_chain(chain_id)
    if chain_id in MAINNET_CHAIN_IDS or chain_id not in _TESTNET_IDS:
        raise MainnetRefused(chain_id)
    message = digest(vault, xnav_bps, stress_bps, ts, chain_id, verifying)
    der = private.sign(message, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    r, s = utils.decode_dss_signature(der)
    if s > _N // 2:
        s = _N - s
    nums = private.public_key().public_numbers()
    v = _recovery_bit(message, r, s, nums.x, nums.y)
    if v is None:
        raise SchemeRejected("recovery")
    return r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes((v,))
