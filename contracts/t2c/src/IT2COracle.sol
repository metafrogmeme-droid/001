// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

/// A posted cap packet. This is not a liquidation oracle and it has no price().
/// A packet with ts == 0 was not posted. That is unread, not a price of zero.
interface IT2COracle {
    struct T2CPacket {
        address lst;
        uint256 sizeBucket;
        uint256 tSecs;
        uint256 rBorrowBps;
        uint256 yBps;
        uint256 ammBpsDisc;
        uint256 queueBpsDisc;
        uint256 pE18;
        uint256 ts;
    }

    event Posted(address indexed lst, uint256 indexed sizeBucket, uint256 pE18, uint256 ts);

    function latest(address lst, uint256 sizeBucket) external view returns (T2CPacket memory packet);
}
