// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.24;

import {HashKeySeal} from "../src/HashKeySeal.sol";

interface Vm {
    function prank(address) external;
    function deal(address, uint256) external;
    function warp(uint256) external;
    function assume(bool) external;
    function expectRevert(bytes4) external;
    function expectEmit(bool, bool, bool, bool) external;
}

contract HashKeySealTest {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    address internal owner = address(0xA11CE);
    address internal publisher = address(0xB0B);
    address internal stranger = address(0xCAFE);

    HashKeySeal internal seal;

    event Anchored(address indexed sender, bytes32 indexed hash, bytes32 indexed kind, uint256 timestamp);
    event PublisherSet(address indexed publisher, bool allowed);
    event OwnershipRenounced(address indexed previousOwner);

    function setUp() public {
        vm.prank(owner);
        seal = new HashKeySeal(publisher);
    }

    function _eq(uint256 a, uint256 b) internal pure {
        require(a == b, "mismatch");
    }

    function testOwnerIsNotAPublisher() public view {
        require(seal.owner() == owner, "owner");
        require(seal.publishers(publisher), "publisher");
        require(!seal.publishers(owner), "owner is not a publisher");
    }

    function testZeroPublisherReverts() public {
        vm.expectRevert(HashKeySeal.ZeroPublisher.selector);
        new HashKeySeal(address(0));
    }

    function testAnchorEmitsSenderHashKindAndTimestamp() public {
        bytes32 hash = keccak256("receipt");
        bytes32 kind = keccak256("claw-guard");
        uint256 when = 1_700_000_000;
        vm.warp(when);
        vm.expectEmit(true, true, true, true);
        emit Anchored(publisher, hash, kind, when);
        vm.prank(publisher);
        seal.anchor(hash, kind);
        require(seal.isAnchored(hash), "anchored");
    }

    function testDuplicateHashRevertsEvenWithAnotherKind() public {
        bytes32 hash = keccak256("once");
        vm.prank(publisher);
        seal.anchor(hash, keccak256("claw-guard"));
        vm.expectRevert(HashKeySeal.DuplicateHash.selector);
        vm.prank(publisher);
        seal.anchor(hash, keccak256("agent-leash"));
        require(seal.isAnchored(hash), "still anchored");
    }

    function testZeroHashReverts() public {
        vm.expectRevert(HashKeySeal.ZeroHash.selector);
        vm.prank(publisher);
        seal.anchor(bytes32(0), keccak256("claw-guard"));
        require(!seal.isAnchored(bytes32(0)), "zero stays unset");
    }

    function testStrangerCannotAnchor() public {
        bytes32 hash = keccak256("nope");
        vm.expectRevert(HashKeySeal.NotPublisher.selector);
        vm.prank(stranger);
        seal.anchor(hash, keccak256("claw-guard"));
        require(!seal.isAnchored(hash), "not stored");
    }

    function testOwnerCannotAnchorUntilAllowlisted() public {
        bytes32 hash = keccak256("owner");
        vm.expectRevert(HashKeySeal.NotPublisher.selector);
        vm.prank(owner);
        seal.anchor(hash, keccak256("claw-guard"));
        vm.prank(owner);
        seal.setPublisher(owner, true);
        vm.prank(owner);
        seal.anchor(hash, keccak256("claw-guard"));
        require(seal.isAnchored(hash), "owner after allowlist");
    }

    function testOnlyOwnerSetsPublisher() public {
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(stranger);
        seal.setPublisher(stranger, true);
        require(!seal.publishers(stranger), "stranger stayed out");
        vm.expectEmit(true, true, false, true);
        emit PublisherSet(stranger, true);
        vm.prank(owner);
        seal.setPublisher(stranger, true);
        require(seal.publishers(stranger), "added");
        vm.prank(stranger);
        seal.anchor(keccak256("by-stranger"), keccak256("agent-leash"));
    }

    function testRemovedPublisherCannotAnchor() public {
        vm.prank(owner);
        seal.setPublisher(publisher, false);
        vm.expectRevert(HashKeySeal.NotPublisher.selector);
        vm.prank(publisher);
        seal.anchor(keccak256("removed"), keccak256("claw-guard"));
    }

    function testRenounceFreezesTheAllowlistAndLeavesPublishers() public {
        vm.prank(owner);
        vm.expectEmit(true, false, false, true);
        emit OwnershipRenounced(owner);
        seal.renounceOwnership();
        _eq(uint256(uint160(seal.owner())), 0);
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(owner);
        seal.setPublisher(stranger, true);
        require(!seal.publishers(stranger), "allowlist frozen");
        vm.prank(publisher);
        seal.anchor(keccak256("after-renounce"), keccak256("claw-guard"));
        require(seal.isAnchored(keccak256("after-renounce")), "publisher remains");
    }

    function testACallCarryingValueDoesNotLand() public {
        bytes32 hash = keccak256("value");
        vm.deal(publisher, 1 ether);
        vm.prank(publisher);
        (bool bare,) = address(seal).call{value: 1 ether}("");
        require(!bare, "bare value");
        vm.prank(publisher);
        (bool paid,) = address(seal).call{value: 1}(
            abi.encodeCall(HashKeySeal.anchor, (hash, keccak256("claw-guard")))
        );
        require(!paid, "anchor with value");
        require(address(seal).balance == 0, "balance");
        require(!seal.isAnchored(hash), "not stored");
    }

    function testFuzz_publisherAnchorsOnce(bytes32 hash, bytes32 kind) public {
        vm.assume(hash != bytes32(0));
        vm.prank(publisher);
        seal.anchor(hash, kind);
        require(seal.isAnchored(hash), "anchored");
        vm.expectRevert(HashKeySeal.DuplicateHash.selector);
        vm.prank(publisher);
        seal.anchor(hash, keccak256(abi.encode(kind)));
    }

    function testFuzz_strangerCannotAnchor(address caller, bytes32 hash, bytes32 kind) public {
        vm.assume(caller != publisher);
        vm.assume(hash != bytes32(0));
        vm.expectRevert(HashKeySeal.NotPublisher.selector);
        vm.prank(caller);
        seal.anchor(hash, kind);
        require(!seal.isAnchored(hash), "untouched");
    }

    function testFuzz_onlyTheOwnerChangesTheAllowlist(address caller, address who) public {
        vm.assume(caller != owner);
        vm.assume(who != address(0));
        vm.assume(who != publisher);
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(caller);
        seal.setPublisher(who, true);
        require(!seal.publishers(who), "unchanged");
    }
}
