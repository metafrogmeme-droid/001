// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

/// @title DrainTypes
/// @notice Shared constants for the Claw Firewall policy engine.
/// @dev Clean-room. Not derived from Zodiac Roles (BUSL-1.1). One singleton
///      per chain holds every account; nothing here is deployed per user.
library DrainTypes {
    uint8 internal constant ROLE_RECIPIENT = 1;
    uint8 internal constant ROLE_SPENDER = 2;
    uint8 internal constant ROLE_EXECUTOR = 4;

    uint8 internal constant MODE_OFF = 0;
    uint8 internal constant MODE_QUEUE = 1;
    uint8 internal constant MODE_STRICT = 2;

    uint8 internal constant PATH_SHORT = 0;
    uint8 internal constant PATH_LONG = 1;

    uint8 internal constant ST_NONE = 0;
    uint8 internal constant ST_QUEUED = 1;
    uint8 internal constant ST_EXECUTED = 2;
    uint8 internal constant ST_CANCELLED = 3;

    uint8 internal constant KIND_OUTFLOW = 0;
    uint8 internal constant KIND_SPENDER = 1;
    uint8 internal constant KIND_RECIPIENT = 2;
    uint8 internal constant KIND_POLICY = 3;
    uint8 internal constant KIND_UNINSTALL = 4;
    uint8 internal constant KIND_GUARDIAN = 5;
    uint8 internal constant KIND_VAULT = 6;
    uint8 internal constant KIND_HOT = 7;
    uint8 internal constant KIND_MODE = 8;
    uint8 internal constant KIND_EXECUTOR = 9;

    uint8 internal constant REV_APPROVE = 0;
    uint8 internal constant REV_FORALL = 1;
    uint8 internal constant REV_LOCKDOWN = 2;
    uint8 internal constant REV_NONCE = 3;
    uint8 internal constant REV_UNORDERED = 4;
    uint8 internal constant REV_DIGEST = 5;

    uint256 internal constant MIN_SHORT = 24 hours;
    uint256 internal constant MIN_LONG = 7 days;
    uint256 internal constant MAX_FREEZE = 72 hours;
    uint256 internal constant MIN_COOLDOWN = 7 days;
    uint256 internal constant EXEC_WINDOW = 14 days;
    uint256 internal constant MAX_PERMIT_LIFE = 30 days;
    uint256 internal constant APPROVE_MAX_LINE = 1 << 255;
    uint8 internal constant QUEUE_CAP = 32;

    bytes4 internal constant NCAP = 0x4e434150;
    bytes4 internal constant OCAP = 0x4f434150;
    bytes4 internal constant RCPT = 0x52435054;
    bytes4 internal constant SPND = 0x53504e44;
    bytes4 internal constant SELF = 0x53454c46;
    bytes4 internal constant DCAL = 0x4443414c;
    bytes4 internal constant FRZN = 0x46525a4e;
    bytes4 internal constant UNKN = 0x554e4b4e;
    bytes4 internal constant EXPY = 0x45585059;
    bytes4 internal constant HOTB = 0x484f5442;
    bytes4 internal constant AGNT = 0x41474e54;

    bytes4 internal constant SEL_TRANSFER = 0xa9059cbb;
    bytes4 internal constant SEL_APPROVE = 0x095ea7b3;
    bytes4 internal constant SEL_INCREASE = 0x39509351;
    bytes4 internal constant SEL_TRANSFER_FROM = 0x23b872dd;
    bytes4 internal constant SEL_FOR_ALL = 0xa22cb465;
    bytes4 internal constant SEL_SAFE_721 = 0x42842e0e;
    bytes4 internal constant SEL_SAFE_721_DATA = 0xb88d4fde;
    bytes4 internal constant SEL_SAFE_1155 = 0xf242432a;
    bytes4 internal constant SEL_SAFE_1155_BATCH = 0x2eb2c2d6;
    bytes4 internal constant SEL_PERMIT2_APPROVE = 0x87517c45;
    bytes4 internal constant SEL_LOCKDOWN = 0xcc53287f;
    bytes4 internal constant SEL_INVALIDATE_NONCES = 0x65d9723c;
    bytes4 internal constant SEL_INVALIDATE_UNORDERED = 0x3ff9dcb1;
    bytes4 internal constant SEL_CANCEL_AUTH = 0x5a049a70;
    bytes4 internal constant SEL_EXECUTE = 0xe9ae5c53;
    bytes4 internal constant SEL_EXECUTE_EXECUTOR = 0xd691c964;

    function mainnet(uint256 chainId) internal pure returns (bool) {
        return chainId == 1 || chainId == 8453 || chainId == 42161 || chainId == 43114 || chainId == 10
            || chainId == 137 || chainId == 56;
    }

    /// @dev Fuji has no EIP-7702. Callers that would publish a delegate use this.
    function supports7702(uint256 chainId) internal pure returns (bool) {
        return chainId == 84532 || chainId == 421614;
    }
}
