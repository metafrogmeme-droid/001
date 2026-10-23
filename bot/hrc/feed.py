"""HRC's own 2-of-3 feed signatures.

The signers are this product's. Nothing here reads another product's key.
A signature is produced only after the chain id is HyperEVM testnet (998).
Any other id, including an unset one, refuses before the key is read.
Mainnet is never a default.

The digest is the one ``KinkCapMarket`` recovers:

    keccak256(abi.encode(keccak256("HRCFeed.v1"), chainId, hour,
                         supplied, borrowed, rateBps, fundingWad))

``fundingWad`` rounds the continuous funding APR up, so a carry-flip strike
is not signed below the reading. A missing funding rate is not signed as 0.
"""

from __future__ import annotations

from decimal import ROUND_UP, Decimal
from typing import Mapping, Optional, Sequence

from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.hashes import SHA256

from bot.hrc.keccak import keccak256
from bot.hrc.model import quote_matches, utilization_of

CHAIN_TESTNET = 998
CHAIN_MAINNET = 999

_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
_GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
_HALF_N = _N // 2
_TAG = keccak256(b"HRCFeed.v1")


class SigningRefused(Exception):
    """The feed was not signed. The message is not the reason a person sees."""


def confirmed_chain_id(env: Mapping[str, str]) -> Optional[int]:
    """The chain id the operator confirmed, or None when it was not set.

    Unset is not testnet. A value that is not an integer is not a chain.
    """
    raw = env.get("HRC_CHAIN_ID")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(str(raw).strip(), 10)
    except ValueError:
        return None


def funding_wad(apr: Decimal) -> int:
    """Continuous funding APR as a wad, rounded up so the strike is not low."""
    scaled = (apr * Decimal(10**18)).to_integral_value(rounding=ROUND_UP)
    return int(scaled)


def feed_digest(
    chain_id: int,
    hour: int,
    supplied: int,
    borrowed: int,
    rate_bps: int,
    funding_wad_value: int,
) -> bytes:
    """The 32-byte digest the contract hashes. Every field is one ABI word."""
    blob = b"".join((
        _TAG,
        int(chain_id).to_bytes(32, "big"),
        int(hour).to_bytes(32, "big"),
        int(supplied).to_bytes(32, "big"),
        int(borrowed).to_bytes(32, "big"),
        int(rate_bps).to_bytes(32, "big"),
        int(funding_wad_value).to_bytes(32, "big"),
    ))
    return keccak256(blob)


def _point_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1) * pow(2 * y1, -1, _P) % _P
    else:
        lam = (y2 - y1) * pow((x2 - x1) % _P, -1, _P) % _P
    x3 = (lam * lam - x1 - x2) % _P
    y3 = (lam * (x1 - x3) - y1) % _P
    return x3, y3


def _point_mul(k: int, point):
    result = None
    addend = point
    while k:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        k >>= 1
    return result


def _decompress(x: int, odd: bool):
    y2 = (pow(x, 3, _P) + 7) % _P
    y = pow(y2, (_P + 1) // 4, _P)
    if pow(y, 2, _P) != y2:
        return None
    if (y % 2 == 1) != odd:
        y = _P - y
    return x, y


def recover_address(digest: bytes, r: int, s: int, v: int) -> Optional[str]:
    """The checksum-free address that signed ``digest``, or None.

    High ``s`` is None. ``v`` is 27 or 28. A signature that does not land
    on the curve is None, not an address.
    """
    if v not in (27, 28):
        return None
    if type(r) is not int or type(s) is not int:
        return None
    if not (1 <= r < _N and 1 <= s <= _HALF_N):
        return None
    if len(digest) != 32:
        return None
    point = _decompress(r, odd=bool(v - 27))
    if point is None:
        return None
    e = int.from_bytes(digest, "big") % _N
    r_inv = pow(r, -1, _N)
    s_r = _point_mul(s, point)
    e_g = _point_mul(e, (_GX, _GY))
    if s_r is None or e_g is None:
        return None
    neg = (e_g[0], (-e_g[1]) % _P)
    combined = _point_add(s_r, neg)
    pub = _point_mul(r_inv, combined)
    if pub is None:
        return None
    raw = pub[0].to_bytes(32, "big") + pub[1].to_bytes(32, "big")
    return "0x" + keccak256(raw)[-20:].hex()


def _key_int(raw: str) -> int:
    text = str(raw).strip()
    if text.startswith(("0x", "0X")):
        text = text[2:]
    value = int(text, 16)
    if not (1 <= value < _N):
        raise ValueError
    return value


def address_of(secret: int) -> str:
    """The address of a testnet signer key. Used when a signature is checked."""
    key = ec.derive_private_key(secret, ec.SECP256K1())
    numbers = key.public_key().public_numbers()
    raw = numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")
    return "0x" + keccak256(raw)[-20:].hex()


def sign_digest(secret: int, digest: bytes) -> tuple[int, int, int]:
    """ECDSA over the 32-byte digest. ``s`` is at most ``n/2``. ``v`` is 27 or 28."""
    key = ec.derive_private_key(secret, ec.SECP256K1())
    sig = key.sign(digest, ec.ECDSA(utils.Prehashed(SHA256())))
    r, s = utils.decode_dss_signature(sig)
    if s > _HALF_N:
        s = _N - s
    want = address_of(secret)
    v = None
    for candidate in (27, 28):
        if recover_address(digest, r, s, candidate) == want:
            v = candidate
            break
    if v is None:
        raise SigningRefused()
    return r, s, v


def _sample_ok(sample: Mapping) -> bool:
    hour = sample.get("hour")
    supplied = sample.get("supplied")
    borrowed = sample.get("borrowed")
    rate_bps = sample.get("rate_bps")
    funding = sample.get("funding_apr")
    if type(hour) is not int or hour < 0:
        return False
    if type(supplied) is not int or supplied <= 0:
        return False
    if type(borrowed) is not int or borrowed < 0:
        return False
    if type(rate_bps) is not int or rate_bps < 0:
        return False
    if not isinstance(funding, Decimal) or funding < 0:
        return False
    return quote_matches(utilization_of(supplied, borrowed), rate_bps)


def sign_feed(sample: Mapping, env: Mapping[str, str], chain_id: Optional[int]) -> dict:
    """Sign one feed word. The key is read only after every refusal above it.

    ``chain_id`` is the id a read already confirmed. None is a refusal, not
    a guess of 998. A sample whose quote does not match the curve is a
    refusal, and so is a missing funding rate.
    """
    if chain_id != CHAIN_TESTNET:
        raise SigningRefused()
    if not _sample_ok(sample):
        raise SigningRefused()
    try:
        secret = _key_int(env["HRC_FEED_SIGNER_KEY"])
    except (KeyError, TypeError, ValueError):
        raise SigningRefused() from None
    wad = funding_wad(sample["funding_apr"])
    digest = feed_digest(
        CHAIN_TESTNET,
        sample["hour"],
        sample["supplied"],
        sample["borrowed"],
        sample["rate_bps"],
        wad,
    )
    r, s, v = sign_digest(secret, digest)
    return {
        "digest": "0x" + digest.hex(),
        "r": r,
        "s": s,
        "v": v,
        "address": address_of(secret),
        "funding_wad": wad,
        "chain_id": CHAIN_TESTNET,
    }


def verify_feed(
    digest: bytes,
    signatures: Sequence[tuple[int, int, int]],
    signers: Sequence[str],
) -> bool:
    """True when two distinct registered signers produced ``digest``.

    A repeated signer counts once. A signature that does not recover is
    skipped. One signer is not enough.
    """
    allowed = {a.lower() for a in signers}
    seen: set[str] = set()
    for item in signatures:
        if not isinstance(item, tuple) or len(item) != 3:
            continue
        got = recover_address(digest, item[0], item[1], item[2])
        if got is None or got.lower() not in allowed:
            continue
        seen.add(got.lower())
    return len(seen) >= 2
