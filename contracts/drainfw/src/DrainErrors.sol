// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

/// @title DrainErrors
/// @notice Shared reverts for the Claw Firewall engine.
/// @dev Clean-room. Not derived from Zodiac Roles (BUSL-1.1).
library DrainErrors {
    error Mainnet();
    error ChainRefused();
    error NotInstalled();
    error AlreadyInit();
    error FrontRun();
    error BadCaller();
    error NeedsQueue(bytes4 reason);
    error OverCap(bytes4 reason);
    error Queued(bytes32 opId, bytes4 reason);
    error Denied(bytes4 reason);
    error NotGuardian();
    error BadPath();
    error QueueFull();
    error OpMissing();
    error NotReady();
    error Cooldown();
    error BadTarget();
    error AgentNoQueue();
}
