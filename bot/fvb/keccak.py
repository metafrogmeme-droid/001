"""Keccak-256 as Ethereum uses it.

NIST SHA3-256 is a different padding. This is the hash EIP-712 and the
on-chain verifier both have to compute, so it lives once, with the empty
and ``abc`` vectors pinned in tests.
"""
from __future__ import annotations

_ROT = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)
_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

_MASK = (1 << 64) - 1


def _rotl(x: int, n: int) -> int:
    n %= 64
    return ((x << n) | (x >> (64 - n))) & _MASK


def _keccak_f(st: list[int]) -> None:
    for rc in _RC:
        c = [st[i] ^ st[i + 5] ^ st[i + 10] ^ st[i + 15] ^ st[i + 20] for i in range(5)]
        # D[x] = C[x - 1] xor rot(C[x + 1], 1). Rotating the other column
        # still mixes the state and still looks like a hash.
        d = [c[(i - 1) % 5] ^ _rotl(c[(i + 1) % 5], 1) for i in range(5)]
        for i in range(25):
            st[i] ^= d[i % 5]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl(st[x + 5 * y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                st[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y] & _MASK) & b[(x + 2) % 5 + 5 * y])
        st[0] ^= rc


def keccak256(data: bytes) -> bytes:
    """32-byte Keccak-256 digest of ``data``."""
    rate = 136
    st = [0] * 25
    msg = bytearray(data)
    msg.append(0x01)
    while (len(msg) % rate) != (rate - 1):
        msg.append(0x00)
    msg.append(0x80)
    for off in range(0, len(msg), rate):
        block = msg[off:off + rate]
        for i in range(rate // 8):
            st[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        _keccak_f(st)
    out = bytearray()
    while len(out) < 32:
        for i in range(rate // 8):
            out += st[i].to_bytes(8, "little")
            if len(out) >= 32:
                break
        if len(out) < 32:
            _keccak_f(st)
    return bytes(out[:32])
