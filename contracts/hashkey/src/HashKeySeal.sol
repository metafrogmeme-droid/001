// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.24;

/// Anchors a hash and records which publisher anchored it and under which
/// kind. Rejects any call that carries value and has no function that sends
/// value. A balance forced in from outside (a selfdestruct beneficiary, an
/// address funded before deployment) is unreachable and stays stuck; nothing
/// here moves it.
///
/// The allowlist is the owner's. Ownership moves in two steps (transfer,
/// then accept by the new owner) and renounce freezes the allowlist: after
/// renounce a publisher can be neither added nor removed, so renounce only
/// when no hot key is a publisher. A hash is stored once. Whichever
/// publisher anchors it first fixes its publisher and kind, and a later
/// anchor of the same hash reverts, whatever kind or publisher it comes from.
contract HashKeySeal {
    struct Anchor {
        address publisher;
        bytes32 kind;
    }

    address public owner;
    address public pendingOwner;
    mapping(address => bool) public publishers;
    mapping(bytes32 => Anchor) private _anchors;

    event Anchored(
        address indexed sender,
        bytes32 indexed hash,
        bytes32 indexed kind,
        uint256 timestamp
    );
    event PublisherSet(address indexed publisher, bool allowed);
    event OwnershipTransferStarted(address indexed previousOwner, address indexed newOwner);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event OwnershipRenounced(address indexed previousOwner);

    error NotOwner();
    error NotPendingOwner();
    error NotPublisher();
    error DuplicateHash();
    error ZeroHash();
    error ZeroKind();
    error ZeroPublisher();
    error ZeroOwner();
    error EmptyBatch();

    constructor(address firstPublisher) {
        if (firstPublisher == address(0)) revert ZeroPublisher();
        owner = msg.sender;
        emit OwnershipTransferred(address(0), msg.sender);
        publishers[firstPublisher] = true;
        emit PublisherSet(firstPublisher, true);
    }

    function setPublisher(address publisher, bool allowed) external {
        _onlyOwner();
        if (publisher == address(0)) revert ZeroPublisher();
        publishers[publisher] = allowed;
        emit PublisherSet(publisher, allowed);
    }

    /// Step one of two. The current owner keeps every right until the new
    /// owner calls acceptOwnership. A later proposal replaces the pending one.
    function transferOwnership(address newOwner) external {
        _onlyOwner();
        if (newOwner == address(0)) revert ZeroOwner();
        pendingOwner = newOwner;
        emit OwnershipTransferStarted(owner, newOwner);
    }

    /// Step two of two, by the proposed owner only.
    function acceptOwnership() external {
        address pending = pendingOwner;
        if (pending == address(0) || msg.sender != pending) revert NotPendingOwner();
        address previous = owner;
        owner = pending;
        pendingOwner = address(0);
        emit OwnershipTransferred(previous, pending);
    }

    /// Freezes the allowlist for good and drops any pending transfer.
    function renounceOwnership() external {
        _onlyOwner();
        address previous = owner;
        owner = address(0);
        pendingOwner = address(0);
        emit OwnershipRenounced(previous);
        emit OwnershipTransferred(previous, address(0));
    }

    /// `kind` is opaque apart from zero, which is refused like a zero hash.
    function anchor(bytes32 hash, bytes32 kind) external {
        if (!publishers[msg.sender]) revert NotPublisher();
        _anchor(hash, kind);
    }

    /// Every element is checked the way anchor checks one; one bad element
    /// reverts the whole batch, so a batch lands whole or not at all.
    function anchorMany(bytes32[] calldata hashes, bytes32 kind) external {
        if (!publishers[msg.sender]) revert NotPublisher();
        uint256 n = hashes.length;
        if (n == 0) revert EmptyBatch();
        for (uint256 i = 0; i < n; ++i) {
            _anchor(hashes[i], kind);
        }
    }

    function isAnchored(bytes32 hash) external view returns (bool) {
        return _anchors[hash].publisher != address(0);
    }

    /// The publisher that anchored the hash and the kind it passed, or two
    /// zero values when the hash is not stored. No stored anchor has a zero
    /// publisher: anchor requires an allowlisted sender, and address(0) is
    /// never allowlisted because setPublisher rejects it.
    function anchorOf(bytes32 hash) external view returns (address publisher, bytes32 kind) {
        Anchor storage a = _anchors[hash];
        return (a.publisher, a.kind);
    }

    function _anchor(bytes32 hash, bytes32 kind) internal {
        if (hash == bytes32(0)) revert ZeroHash();
        if (kind == bytes32(0)) revert ZeroKind();
        if (_anchors[hash].publisher != address(0)) revert DuplicateHash();
        _anchors[hash] = Anchor({publisher: msg.sender, kind: kind});
        emit Anchored(msg.sender, hash, kind, block.timestamp);
    }

    /// After renounce the owner is address(0). No key signs for address(0),
    /// and this check refuses it anyway, so the comparison can never pass
    /// for a zero owner.
    function _onlyOwner() internal view {
        address current = owner;
        if (current == address(0) || msg.sender != current) revert NotOwner();
    }
}
