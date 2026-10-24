"""Vault V2 cap id, and the onboarding refusal.

``idData`` is ``abi.encode("collateralToken", token)``. The cap id is
``keccak256`` of that, the same key Vault V2 stores. Fork setup plans
``setIsSentinel`` for the owner only. A cap-increase timelock under 72
hours, or one that was not read, is a refusal: Vault V2 defaults the
timelock to 0, and a curator could otherwise undo a cap-down at once.
"""

from __future__ import annotations

MIN_INCREASE_DELAY_S = 72 * 60 * 60

_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
_ROT = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)
_MASK = (1 << 64) - 1


def _rotl(value: int, shift: int) -> int:
    shift %= 64
    if shift == 0:
        return value & _MASK
    return ((value << shift) | (value >> (64 - shift))) & _MASK


def _keccak_f(state: list[int]) -> None:
    for round_c in _RC:
        columns = [
            state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
            for x in range(5)
        ]
        mixed = [columns[(x - 1) % 5] ^ _rotl(columns[(x + 1) % 5], 1) for x in range(5)]
        for i in range(25):
            state[i] ^= mixed[i % 5]
        rotated = [0] * 25
        for x in range(5):
            for y in range(5):
                rotated[y + ((2 * x + 3 * y) % 5) * 5] = _rotl(state[x + 5 * y], _ROT[x][y])
        for y in range(5):
            for x in range(5):
                a = rotated[x + 5 * y]
                b = rotated[((x + 1) % 5) + 5 * y]
                c = rotated[((x + 2) % 5) + 5 * y]
                state[x + 5 * y] = (a ^ ((~b) & c)) & _MASK
        state[0] ^= round_c


def keccak256(data: bytes) -> bytes:
    """Ethereum keccak-256. Not NIST SHA3."""
    rate = 136
    state = [0] * 25
    padded = bytearray(data)
    padded.append(0x01)
    if len(padded) % rate == 0:
        padded[-1] |= 0x80
    else:
        padded.extend(b"\x00" * (rate - (len(padded) % rate)))
        padded[-1] |= 0x80
    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        _keccak_f(state)
    out = bytearray()
    for lane in state[:4]:
        out.extend(lane.to_bytes(8, "little"))
    return bytes(out)


def _address(token: str) -> bytes | None:
    text = token[2:] if token.startswith("0x") else token
    if len(text) != 40:
        return None
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        return None
    return raw if len(raw) == 20 else None


def collateral_id_data(token: str) -> bytes | None:
    """``abi.encode("collateralToken", token)``. None when the address is unreadable."""
    raw = _address(token)
    if raw is None:
        return None
    label = b"collateralToken"
    head = (64).to_bytes(32, "big") + (b"\x00" * 12 + raw)
    tail = len(label).to_bytes(32, "big") + label + b"\x00" * (32 - len(label))
    return head + tail


def cap_id_hex(id_data: bytes) -> str:
    return "0x" + keccak256(id_data).hex()


_DECREASE = {
    "absolute": "decreaseAbsoluteCap(bytes,uint256)",
    "relative": "decreaseRelativeCap(bytes,uint256)",
}


def _hex_bytes(value: object) -> bytes | None:
    if not isinstance(value, str) or not value.startswith("0x"):
        return None
    try:
        raw = bytes.fromhex(value[2:])
    except ValueError:
        return None
    return raw


def decrease_calldata(kind: str, id_data_hex: object, new_cap: object) -> str | None:
    """Unsigned calldata for a Vault V2 decrease. None when an input is unreadable.

    This is the transaction the owner's key or multisig would submit. It is
    not signed here.
    """
    sig = _DECREASE.get(kind)
    if sig is None or isinstance(new_cap, bool) or not isinstance(new_cap, int) or new_cap < 0:
        return None
    raw = _hex_bytes(id_data_hex)
    if raw is None:
        return None
    selector = keccak256(sig.encode())[:4]
    offset = (64).to_bytes(32, "big")
    cap = new_cap.to_bytes(32, "big")
    length = len(raw).to_bytes(32, "big")
    pad = (32 - (len(raw) % 32)) % 32
    body = selector + offset + cap + length + raw + (b"\x00" * pad)
    return "0x" + body.hex()


def delay_refusal(delay: object) -> str | None:
    """None when the timelock is at least 72 hours. ``0`` is the Vault V2 default, and it is refused."""
    if isinstance(delay, bool) or not isinstance(delay, int) or delay < 0:
        return "timelock_unreadable"
    if delay < MIN_INCREASE_DELAY_S:
        return "timelock_below_72h"
    return None


def onboard_vault(*, caller_role: str, increase_delay: object, token: str | None) -> dict:
    """The fork-setup plan. The owner calls setIsSentinel. A short timelock is not onboarded."""
    if caller_role != "owner":
        return {"accepted": False, "reason": "not_owner", "caller": "owner", "grant": "setIsSentinel"}
    refusal = delay_refusal(increase_delay)
    if refusal is not None:
        return {"accepted": False, "reason": refusal, "caller": "owner", "grant": "setIsSentinel"}
    if not isinstance(token, str):
        return {"accepted": False, "reason": "token_unreadable", "caller": "owner", "grant": "setIsSentinel"}
    data = collateral_id_data(token)
    if data is None:
        return {"accepted": False, "reason": "token_unreadable", "caller": "owner", "grant": "setIsSentinel"}
    return {
        "accepted": True,
        "reason": None,
        "caller": "owner",
        "grant": "setIsSentinel",
        "id_data": "0x" + data.hex(),
        "cap_id": cap_id_hex(data),
        "increase_delay": increase_delay,
    }
