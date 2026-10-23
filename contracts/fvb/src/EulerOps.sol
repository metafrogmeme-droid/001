// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

/// Euler vault-kit operation bits.
///
/// Read from euler-xyz/euler-vault-kit master `Constants.sol` on 23 Oct 2026:
/// `OP_LIQUIDATE = 1 << 11` and `OP_FLASHLOAN = 1 << 12`. An older commit
/// had liquidate at bit 12, after OP_LOOP shifted the list. This file and
/// `bot/fvb/euler.py` both pin bit 11. Neither imports the other: the
/// chain cannot load the Python module.
library EulerOps {
    uint256 internal constant OP_LIQUIDATE_BIT = 11;
    uint256 internal constant OP_LIQUIDATE = 1 << 11;
    uint256 internal constant OP_FLASHLOAN = 1 << 12;

    function liquidateEnabled(uint256 ops) internal pure returns (bool) {
        return ops & OP_LIQUIDATE != 0;
    }
}

/// A probe the in-process tests call. It publishes the constants and
/// nothing else: no market, no token, no mainnet address.
contract EulerOpsProbe {
    function liquidateBit() external pure returns (uint256) {
        return EulerOps.OP_LIQUIDATE_BIT;
    }

    function liquidateMask() external pure returns (uint256) {
        return EulerOps.OP_LIQUIDATE;
    }

    function flashloanMask() external pure returns (uint256) {
        return EulerOps.OP_FLASHLOAN;
    }

    function liquidateEnabled(uint256 ops) external pure returns (bool) {
        return EulerOps.liquidateEnabled(ops);
    }
}
