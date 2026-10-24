// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

/// @notice Test stand-in for precompile 0x812. Etched at that address by the
///         suite. Not part of the market bytecode.
contract FixedPrecompile {
    uint256[8] public words;

    function set(uint256[8] calldata next) external {
        for (uint256 i = 0; i < 8; i++) words[i] = next[i];
    }

    fallback() external {
        uint256 w0 = words[0];
        uint256 w1 = words[1];
        uint256 w2 = words[2];
        uint256 w3 = words[3];
        uint256 w4 = words[4];
        uint256 w5 = words[5];
        uint256 w6 = words[6];
        uint256 w7 = words[7];
        assembly ("memory-safe") {
            mstore(0x00, w0)
            mstore(0x20, w1)
            mstore(0x40, w2)
            mstore(0x60, w3)
            mstore(0x80, w4)
            mstore(0xa0, w5)
            mstore(0xc0, w6)
            mstore(0xe0, w7)
            return(0x00, 0x100)
        }
    }
}
