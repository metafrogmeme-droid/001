"""Ethereum keccak256.

Solidity's ``keccak256`` is the original Keccak, with the domain byte
``0x01`` and a rate of 136 bytes. When that ``0x01`` lands on the last
byte of a block it shares the byte with the rate bit, and the byte is
``0x81``. A fresh block there would not be the hash the contract computes.
"""

from __future__ import annotations

_MASK = (1 << 64) - 1
_RATE = 136

# r[x][y], x = lane % 5, y = lane // 5.
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


def _rotl(x: int, n: int) -> int:
    n %= 64
    if n == 0:
        return x & _MASK
    return ((x << n) | (x >> (64 - n))) & _MASK


def _keccak_f(state: list[int]) -> None:
    for rc in _RC:
        col = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
               for x in range(5)]
        diag = [col[(x - 1) % 5] ^ _rotl(col[(x + 1) % 5], 1) for x in range(5)]
        for i in range(25):
            state[i] ^= diag[i % 5]
        mixed = [0] * 25
        for x in range(5):
            for y in range(5):
                mixed[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl(state[x + 5 * y], _ROT[x][y])
        for y in range(5):
            for x in range(5):
                i = x + 5 * y
                state[i] = (mixed[i] ^ ((~mixed[((x + 1) % 5) + 5 * y]) & mixed[((x + 2) % 5) + 5 * y])) & _MASK
        state[0] ^= rc


def keccak256(data: bytes) -> bytes:
    """32-byte keccak digest of ``data``."""
    buf = bytearray(data)
    buf.append(0x01)
    if len(buf) % _RATE == 0:
        buf[-1] = 0x81
    else:
        while len(buf) % _RATE != 0:
            buf.append(0)
        buf[-1] |= 0x80
    state = [0] * 25
    for off in range(0, len(buf), _RATE):
        block = buf[off:off + _RATE]
        for i in range(_RATE // 8):
            lane = int.from_bytes(block[i * 8:(i + 1) * 8], "little")
            state[(i % 5) + 5 * (i // 5)] ^= lane
        _keccak_f(state)
    out = bytearray()
    i = 0
    while len(out) < 32:
        out += state[(i % 5) + 5 * (i // 5)].to_bytes(8, "little")
        i += 1
    return bytes(out[:32])
