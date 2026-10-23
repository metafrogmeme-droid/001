// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

/// The shape a later pool would call. This repository does not call it.
interface IMorphoFlash {
    function flashLoan(address token, uint256 assets, bytes calldata data) external;
}
