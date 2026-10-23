// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

/// Accounting shape for a later pool. No share token and no deposit.
/// Bought is the fill. claim returns the position to idle after finalization.
interface IExitSwap {
    event Bought(address indexed lst, uint256 amt, uint256 ethOut, uint256 requestId);

    function quote(address lst, uint256 amt) external view returns (uint256 ethOut);

    function sell(address lst, uint256 amt, uint256 minEthOut) external returns (uint256 requestId);

    function claim(uint256[] calldata requestIds) external returns (uint256 ethOut);
}
