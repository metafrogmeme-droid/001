// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.24;

/// Anchors a hash. Holds no balance and has no function that sends value.
/// The allowlist is the deployer's, and renounce freezes it.
contract HashKeySeal {
    address public owner;
    mapping(address => bool) public publishers;
    mapping(bytes32 => bool) private _anchored;

    event Anchored(
        address indexed sender,
        bytes32 indexed hash,
        bytes32 indexed kind,
        uint256 timestamp
    );
    event PublisherSet(address indexed publisher, bool allowed);
    event OwnershipRenounced(address indexed previousOwner);

    error NotOwner();
    error NotPublisher();
    error DuplicateHash();
    error ZeroHash();
    error ZeroPublisher();

    constructor(address firstPublisher) {
        if (firstPublisher == address(0)) revert ZeroPublisher();
        owner = msg.sender;
        publishers[firstPublisher] = true;
        emit PublisherSet(firstPublisher, true);
    }

    function setPublisher(address publisher, bool allowed) external {
        if (msg.sender != owner) revert NotOwner();
        if (publisher == address(0)) revert ZeroPublisher();
        publishers[publisher] = allowed;
        emit PublisherSet(publisher, allowed);
    }

    function renounceOwnership() external {
        if (msg.sender != owner) revert NotOwner();
        emit OwnershipRenounced(owner);
        owner = address(0);
    }

    /// `kind` is opaque. A hash already stored reverts, whatever kind is passed.
    function anchor(bytes32 hash, bytes32 kind) external {
        if (!publishers[msg.sender]) revert NotPublisher();
        if (hash == bytes32(0)) revert ZeroHash();
        if (_anchored[hash]) revert DuplicateHash();
        _anchored[hash] = true;
        emit Anchored(msg.sender, hash, kind, block.timestamp);
    }

    function isAnchored(bytes32 hash) external view returns (bool) {
        return _anchored[hash];
    }
}
