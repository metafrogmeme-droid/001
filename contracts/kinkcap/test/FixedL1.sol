// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

/// @notice Test stand-in for the L1 block precompile at 0x809.
///         Etched there by the suite. Not part of the market bytecode.
contract FixedL1 {
    uint256 public blockNo;

    function set(uint256 next) external {
        blockNo = next;
    }

    fallback() external {
        uint256 n = blockNo;
        assembly ("memory-safe") {
            mstore(0x00, n)
            return(0x00, 0x20)
        }
    }
}
