// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

import "./IExitSwap.sol";

/// Fork-only accounting model. Fee bps are an input from the Python model,
/// which owns clamp(10 + 40·U^2, 5, 100). This contract checks the bounds
/// and the split. It does not hold a key and it refuses real ether.
contract ExitSwapModel is IExitSwap {
    uint256 public constant INTAKE_NUMERATOR = 2;
    uint256 public constant INTAKE_DENOMINATOR = 100;
    uint256 public constant LOCAL_FORK = 31337;
    uint256 public constant FEE_FLOOR_BPS = 5;
    uint256 public constant FEE_CEILING_BPS = 100;

    uint256 public lpFeeEth;

    error MainnetForbidden();
    error NotLocalFork(uint256 chainId);
    error Unreadable();
    error IntakeCap();
    error FeeBounds();
    error Shortfall();
    error RealFundsRefused();

    modifier forkOnly() {
        if (block.chainid == 1) revert MainnetForbidden();
        if (block.chainid != LOCAL_FORK) revert NotLocalFork(block.chainid);
        _;
    }

    receive() external payable {
        revert RealFundsRefused();
    }

    function unwind(
        uint256 quantity,
        uint256 priceWad,
        uint256 feeBps,
        uint256 debtEth,
        uint256 poolEth,
        uint256 takenThisBlock
    ) external forkOnly returns (uint256 looperEth, uint256 feeEth) {
        if (quantity == 0 || priceWad == 0 || poolEth == 0) revert Unreadable();
        if (feeBps < FEE_FLOOR_BPS || feeBps > FEE_CEILING_BPS) revert FeeBounds();
        uint256 gross = quantity * priceWad / 1e18;
        uint256 room = poolEth * INTAKE_NUMERATOR / INTAKE_DENOMINATOR;
        if (takenThisBlock >= room || gross > room - takenThisBlock) revert IntakeCap();
        feeEth = gross * feeBps / 10000;
        uint256 proceeds = gross - feeEth;
        if (proceeds < debtEth) revert Shortfall();
        lpFeeEth += feeEth;
        looperEth = proceeds - debtEth;
    }
}
