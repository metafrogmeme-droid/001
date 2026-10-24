"""Euler Vault Kit operation bits.

The current vault-kit ``Constants.sol`` defines ``OP_LIQUIDATE`` as
``1 << 11``. An older revision numbered it ``1 << 12``; on the current
kit that bit is ``OP_FLASHLOAN``. Callers that still test bit 12 are
testing the flash-loan bit.
"""
from __future__ import annotations

OP_LIQUIDATE_BIT = 11
OP_LIQUIDATE = 1 << OP_LIQUIDATE_BIT
OP_FLASHLOAN_BIT = 12
OP_FLASHLOAN = 1 << OP_FLASHLOAN_BIT


def liquidate_enabled(ops: int) -> bool:
    """True when the vault's operation mask includes liquidate.

    A mask of zero is "not set", which is a reading, not a failure to
    read the mask. Callers that could not read the mask should not call
    this with a stand-in.
    """
    return (int(ops) & OP_LIQUIDATE) != 0
