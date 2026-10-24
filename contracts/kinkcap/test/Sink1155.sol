// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

/// @notice ERC-1155 receiver for the market tests. Not a deployment.
contract Sink1155 {
    bytes4 public constant RECEIVED = 0xf23a6e61;
    bytes4 public constant BATCH_RECEIVED = 0xbc197c81;
    uint256 public lastId;
    bool public accept = true;

    function setAccept(bool next) external {
        accept = next;
    }

    function onERC1155Received(address, address, uint256 id, uint256, bytes calldata)
        external
        returns (bytes4)
    {
        lastId = id;
        if (!accept) return bytes4(0);
        return RECEIVED;
    }

    function onERC1155BatchReceived(address, address, uint256[] calldata ids, uint256[] calldata, bytes calldata)
        external
        returns (bytes4)
    {
        if (ids.length > 0) lastId = ids[0];
        if (!accept) return bytes4(0);
        return BATCH_RECEIVED;
    }
}
