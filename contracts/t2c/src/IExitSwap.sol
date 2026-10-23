// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

/// Accounting shape for a later pool. No share token, no deposit.
interface IExitSwap {
    function unwind(
        uint256 quantity,
        uint256 priceWad,
        uint256 feeBps,
        uint256 debtEth,
        uint256 poolEth,
        uint256 takenThisBlock
    ) external returns (uint256 looperEth, uint256 feeEth);
}
