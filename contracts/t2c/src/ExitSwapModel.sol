// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

import "./IExitSwap.sol";

/// Fork-only accounting model. The Python model owns P, the fee curve, and
/// the edge. This contract checks the phase, the 50/20 bp band, the intake
/// cap, and the chain id. It does not hold a key and it refuses real ether.
contract ExitSwapModel is IExitSwap {
    uint256 public constant INTAKE_NUMERATOR = 2;
    uint256 public constant INTAKE_DENOMINATOR = 100;
    uint256 public constant LOCAL_FORK = 31337;
    uint256 public constant FEE_FLOOR_BPS = 5;
    uint256 public constant FEE_CEILING_BPS = 100;
    uint256 public constant ENTER_EDGE_BPS = 50;
    uint256 public constant EXIT_EDGE_BPS = 20;
    address public constant STETH = 0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84;
    address public constant WSTETH = 0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0;

    enum Phase { Idle, Quoting, Holding, Claimable }

    uint256 public lpFeeEth;
    uint256 public priceWad;
    Phase public phase;
    uint256 public nextRequest;
    mapping(uint256 => uint256) public openEth;

    error MainnetForbidden();
    error NotLocalFork(uint256 chainId);
    error Unreadable();
    error IntakeCap();
    error FeeBounds();
    error Shortfall();
    error RealFundsRefused();
    error WrongPhase();
    error UnsupportedLst();
    error MinEth();
    error UnknownRequest();

    modifier forkOnly() {
        if (block.chainid == 1) revert MainnetForbidden();
        if (block.chainid != LOCAL_FORK) revert NotLocalFork(block.chainid);
        _;
    }

    constructor(uint256 proceedsPerLstWad) {
        if (proceedsPerLstWad == 0) revert Unreadable();
        priceWad = proceedsPerLstWad;
    }

    receive() external payable {
        revert RealFundsRefused();
    }

    function noteEdge(uint256 ammBps, uint256 queueBps) external forkOnly {
        if (phase == Phase.Idle) {
            if (ammBps > queueBps && ammBps - queueBps > ENTER_EDGE_BPS) phase = Phase.Quoting;
            return;
        }
        if (phase == Phase.Quoting) {
            if (ammBps <= queueBps || ammBps - queueBps < EXIT_EDGE_BPS) phase = Phase.Idle;
        }
    }

    function quote(address lst, uint256 amt) public view returns (uint256 ethOut) {
        if (lst != STETH && lst != WSTETH) revert UnsupportedLst();
        if (amt == 0 || priceWad == 0) revert Unreadable();
        ethOut = amt * priceWad / 1e18;
        if (ethOut == 0) revert Unreadable();
    }

    function sell(address lst, uint256 amt, uint256 minEthOut) external forkOnly returns (uint256 requestId) {
        if (phase != Phase.Quoting) revert WrongPhase();
        uint256 ethOut = quote(lst, amt);
        if (ethOut < minEthOut) revert MinEth();
        requestId = ++nextRequest;
        openEth[requestId] = ethOut;
        phase = Phase.Holding;
        emit Bought(lst, amt, ethOut, requestId);
    }

    function markFinalized(uint256 requestId) external forkOnly {
        if (phase != Phase.Holding || openEth[requestId] == 0) revert UnknownRequest();
        phase = Phase.Claimable;
    }

    function claim(uint256[] calldata requestIds) external forkOnly returns (uint256 ethOut) {
        if (phase != Phase.Claimable || requestIds.length == 0) revert WrongPhase();
        for (uint256 i = 0; i < requestIds.length; i++) {
            uint256 part = openEth[requestIds[i]];
            if (part == 0) revert UnknownRequest();
            ethOut += part;
            delete openEth[requestIds[i]];
        }
        phase = Phase.Idle;
    }

    function unwind(
        uint256 quantity,
        uint256 priceWadIn,
        uint256 feeBps,
        uint256 debtEth,
        uint256 poolEth,
        uint256 takenThisBlock
    ) external forkOnly returns (uint256 looperEth, uint256 feeEth) {
        if (quantity == 0 || priceWadIn == 0 || poolEth == 0) revert Unreadable();
        if (feeBps < FEE_FLOOR_BPS || feeBps > FEE_CEILING_BPS) revert FeeBounds();
        uint256 gross = quantity * priceWadIn / 1e18;
        uint256 room = poolEth * INTAKE_NUMERATOR / INTAKE_DENOMINATOR;
        if (takenThisBlock >= room || gross > room - takenThisBlock) revert IntakeCap();
        feeEth = gross * feeBps / 10000;
        uint256 proceeds = gross - feeEth;
        if (proceeds < debtEth) revert Shortfall();
        lpFeeEth += feeEth;
        looperEth = proceeds - debtEth;
    }
}
