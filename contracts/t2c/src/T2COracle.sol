// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

import "./IT2COracle.sol";

/// Stores packets on a local fork. Posting a zero price or a zero timestamp
/// is refused: unread is not a price of zero.
contract T2COracle is IT2COracle {
    uint256 public constant LOCAL_FORK = 31337;

    mapping(address => mapping(uint256 => T2CPacket)) private _packets;

    error MainnetForbidden();
    error NotLocalFork(uint256 chainId);
    error UnreadableIsNotZero();

    modifier forkOnly() {
        if (block.chainid == 1) revert MainnetForbidden();
        if (block.chainid != LOCAL_FORK) revert NotLocalFork(block.chainid);
        _;
    }

    function latest(address lst, uint256 sizeBucket) external view returns (T2CPacket memory packet) {
        return _packets[lst][sizeBucket];
    }

    function post(T2CPacket calldata packet) external forkOnly {
        if (packet.ts == 0 || packet.pE18 == 0) revert UnreadableIsNotZero();
        _packets[packet.lst][packet.sizeBucket] = packet;
        emit Posted(packet.lst, packet.sizeBucket, packet.pE18, packet.ts);
    }
}
