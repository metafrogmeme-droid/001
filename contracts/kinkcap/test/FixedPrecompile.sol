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
        bytes memory out = abi.encode(
            words[0], words[1], words[2], words[3], words[4], words[5], words[6], words[7]
        );
        assembly ("memory-safe") {
            return(add(out, 0x20), 256)
        }
    }
}
