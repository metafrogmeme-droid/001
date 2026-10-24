// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

import "./FVBOracle.sol";

/// Morpho-shaped price adapter.
///
/// `price()` is the confirmed fair value for one symbol. A confirmed
/// packet is the point at which the pricer sets the collateral price and
/// the fair value to the same number, so this adapter publishes that
/// number and reverts while it has not been stored. It does not answer
/// zero, and it holds no market address.
contract MorphoFvbAdapter {
    FVBOracle public immutable oracle;
    bytes32 public immutable symbol;

    constructor(FVBOracle oracle_, bytes32 symbol_) {
        uint256 id = block.chainid;
        if (id == 1 || id == 8453 || id == 42161 || id == 999 || id == 4663) {
            revert("testnet only");
        }
        require(address(oracle_) != address(0), "oracle");
        oracle = oracle_;
        symbol = symbol_;
    }

    function price() external view returns (uint256) {
        uint256 px = oracle.fvOf(symbol);
        require(px > 0, "unreadable");
        return px;
    }
}
