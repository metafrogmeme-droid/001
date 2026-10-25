// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

/// @notice HyperEVM mainnet (999), Ethereum, Base, Arbitrum One and
///         Robinhood Chain mainnet are refused. Testnet and local chain
///         ids are accepted. There is no deploy script in this tree.
abstract contract XNAVTestnet {
    constructor() {
        uint256 id = block.chainid;
        if (id == 1 || id == 8453 || id == 42161 || id == 999 || id == 4663) {
            revert("testnet only");
        }
    }
}
