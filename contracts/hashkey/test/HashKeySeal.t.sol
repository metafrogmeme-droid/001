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
    address internal second = address(0xD0D);
    address internal stranger = address(0xCAFE);

    bytes32 internal constant CLAW_GUARD = keccak256("claw-guard");
    bytes32 internal constant AGENT_LEASH = keccak256("agent-leash");

    HashKeySeal internal seal;

    event Anchored(address indexed sender, bytes32 indexed hash, bytes32 indexed kind, uint256 timestamp);
    event PublisherSet(address indexed publisher, bool allowed);
    event OwnershipTransferStarted(address indexed previousOwner, address indexed newOwner);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event OwnershipRenounced(address indexed previousOwner);

    function setUp() public {
        vm.prank(owner);
        seal = new HashKeySeal(publisher);
    }

    function _eq(uint256 a, uint256 b) internal pure {
        require(a == b, "mismatch");
    }

    // ---- construction ------------------------------------------------------

    function testOwnerIsNotAPublisher() public view {
        require(seal.owner() == owner, "owner");
        require(seal.pendingOwner() == address(0), "no pending owner");
        require(seal.publishers(publisher), "publisher");
        require(!seal.publishers(owner), "owner is not a publisher");
    }

    function testConstructorEmitsTheOwnerAndTheFirstPublisher() public {
        vm.expectEmit(true, true, false, true);
        emit OwnershipTransferred(address(0), owner);
        vm.expectEmit(true, false, false, true);
        emit PublisherSet(publisher, true);
        vm.prank(owner);
        new HashKeySeal(publisher);
    }

    function testZeroPublisherReverts() public {
        vm.expectRevert(HashKeySeal.ZeroPublisher.selector);
        new HashKeySeal(address(0));
    }

    // ---- anchor ------------------------------------------------------------

    function testAnchorEmitsSenderHashKindAndTimestamp() public {
        bytes32 hash = keccak256("receipt");
        uint256 when = 1_700_000_000;
        vm.warp(when);
        vm.expectEmit(true, true, true, true);
        emit Anchored(publisher, hash, CLAW_GUARD, when);
        vm.prank(publisher);
        seal.anchor(hash, CLAW_GUARD);
        require(seal.isAnchored(hash), "anchored");
        (address who, bytes32 kind) = seal.anchorOf(hash);
        require(who == publisher && kind == CLAW_GUARD, "anchorOf names the publisher and the kind");
    }

    function testAnchorOfAnUnknownHashIsTwoZeroValues() public view {
        (address who, bytes32 kind) = seal.anchorOf(keccak256("never"));
        require(who == address(0) && kind == bytes32(0), "absent");
        require(!seal.isAnchored(keccak256("never")), "not anchored");
    }

    function testDuplicateHashRevertsEvenWithAnotherKind() public {
        bytes32 hash = keccak256("once");
        vm.prank(publisher);
        seal.anchor(hash, CLAW_GUARD);
        vm.expectRevert(HashKeySeal.DuplicateHash.selector);
        vm.prank(publisher);
        seal.anchor(hash, AGENT_LEASH);
        require(seal.isAnchored(hash), "still anchored");
        (, bytes32 kind) = seal.anchorOf(hash);
        require(kind == CLAW_GUARD, "the first kind stands");
    }

    function testASecondPublisherSquatsAHashAndRevocationDoesNotFreeIt() public {
        bytes32 hash = keccak256("squat");
        vm.prank(owner);
        seal.setPublisher(second, true);
        vm.prank(second);
        seal.anchor(hash, AGENT_LEASH);
        vm.expectRevert(HashKeySeal.DuplicateHash.selector);
        vm.prank(publisher);
        seal.anchor(hash, CLAW_GUARD);
        (address who, bytes32 kind) = seal.anchorOf(hash);
        require(who == second && kind == AGENT_LEASH, "the first writer's record stands and names the writer");
        vm.prank(owner);
        seal.setPublisher(second, false);
        vm.expectRevert(HashKeySeal.DuplicateHash.selector);
        vm.prank(publisher);
        seal.anchor(hash, CLAW_GUARD);
        require(seal.isAnchored(hash), "revocation does not free the hash");
    }

    function testZeroHashReverts() public {
        vm.expectRevert(HashKeySeal.ZeroHash.selector);
        vm.prank(publisher);
        seal.anchor(bytes32(0), CLAW_GUARD);
        require(!seal.isAnchored(bytes32(0)), "zero stays unset");
    }

    function testZeroKindReverts() public {
        bytes32 hash = keccak256("zero-kind");
        vm.expectRevert(HashKeySeal.ZeroKind.selector);
        vm.prank(publisher);
        seal.anchor(hash, bytes32(0));
        require(!seal.isAnchored(hash), "not stored");
        vm.prank(publisher);
        seal.anchor(hash, CLAW_GUARD);
        require(seal.isAnchored(hash), "same hash lands with a kind");
    }

    function testStrangerCannotAnchor() public {
        bytes32 hash = keccak256("nope");
        vm.expectRevert(HashKeySeal.NotPublisher.selector);
        vm.prank(stranger);
        seal.anchor(hash, CLAW_GUARD);
        require(!seal.isAnchored(hash), "not stored");
    }

    function testOwnerCannotAnchorUntilAllowlisted() public {
        bytes32 hash = keccak256("owner");
        vm.expectRevert(HashKeySeal.NotPublisher.selector);
        vm.prank(owner);
        seal.anchor(hash, CLAW_GUARD);
        vm.prank(owner);
        seal.setPublisher(owner, true);
        vm.prank(owner);
        seal.anchor(hash, CLAW_GUARD);
        require(seal.isAnchored(hash), "owner after allowlist");
    }

    // ---- anchorMany --------------------------------------------------------

    function testAnchorManyLandsEveryHashWithOneKind() public {
        bytes32[] memory hashes = new bytes32[](3);
        hashes[0] = keccak256("a");
        hashes[1] = keccak256("b");
        hashes[2] = keccak256("c");
        for (uint256 i = 0; i < 3; i++) {
            vm.expectEmit(true, true, true, true);
            emit Anchored(publisher, hashes[i], AGENT_LEASH, block.timestamp);
        }
        vm.prank(publisher);
        seal.anchorMany(hashes, AGENT_LEASH);
        for (uint256 i = 0; i < 3; i++) {
            (address who, bytes32 kind) = seal.anchorOf(hashes[i]);
            require(who == publisher && kind == AGENT_LEASH, "each element stored");
        }
    }

    function testAnchorManyRevertsWholeOnOneBadElement() public {
        bytes32[] memory hashes = new bytes32[](2);
        hashes[0] = keccak256("fresh");
        hashes[1] = keccak256("fresh");
        vm.expectRevert(HashKeySeal.DuplicateHash.selector);
        vm.prank(publisher);
        seal.anchorMany(hashes, CLAW_GUARD);
        require(!seal.isAnchored(keccak256("fresh")), "nothing from a reverted batch lands");

        hashes[1] = bytes32(0);
        vm.expectRevert(HashKeySeal.ZeroHash.selector);
        vm.prank(publisher);
        seal.anchorMany(hashes, CLAW_GUARD);
        require(!seal.isAnchored(keccak256("fresh")), "still nothing");

        hashes[1] = keccak256("other");
        vm.expectRevert(HashKeySeal.ZeroKind.selector);
        vm.prank(publisher);
        seal.anchorMany(hashes, bytes32(0));

        vm.expectRevert(HashKeySeal.NotPublisher.selector);
        vm.prank(stranger);
        seal.anchorMany(hashes, CLAW_GUARD);

        bytes32[] memory none = new bytes32[](0);
        vm.expectRevert(HashKeySeal.EmptyBatch.selector);
        vm.prank(publisher);
        seal.anchorMany(none, CLAW_GUARD);

        vm.prank(publisher);
        seal.anchorMany(hashes, CLAW_GUARD);
        require(seal.isAnchored(keccak256("fresh")) && seal.isAnchored(keccak256("other")), "the good batch lands");
    }

    // ---- allowlist ---------------------------------------------------------

    function testOnlyOwnerSetsPublisher() public {
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(stranger);
        seal.setPublisher(stranger, true);
        require(!seal.publishers(stranger), "stranger stayed out");
        vm.expectEmit(true, false, false, true);
        emit PublisherSet(stranger, true);
        vm.prank(owner);
        seal.setPublisher(stranger, true);
        require(seal.publishers(stranger), "added");
        vm.prank(stranger);
        seal.anchor(keccak256("by-stranger"), AGENT_LEASH);
    }

    function testSetPublisherZeroRevertsOnBothArms() public {
        vm.expectRevert(HashKeySeal.ZeroPublisher.selector);
        vm.prank(owner);
        seal.setPublisher(address(0), true);
        vm.expectRevert(HashKeySeal.ZeroPublisher.selector);
        vm.prank(owner);
        seal.setPublisher(address(0), false);
        require(!seal.publishers(address(0)), "zero never allowlisted");
    }

    function testRemovedPublisherCannotAnchorAndRemovalEmits() public {
        vm.expectEmit(true, false, false, true);
        emit PublisherSet(publisher, false);
        vm.prank(owner);
        seal.setPublisher(publisher, false);
        vm.expectRevert(HashKeySeal.NotPublisher.selector);
        vm.prank(publisher);
        seal.anchor(keccak256("removed"), CLAW_GUARD);
    }

    function testARemovedPublisherCanBeReAdded() public {
        vm.prank(owner);
        seal.setPublisher(publisher, false);
        vm.prank(owner);
        seal.setPublisher(publisher, true);
        vm.prank(publisher);
        seal.anchor(keccak256("again"), CLAW_GUARD);
        require(seal.isAnchored(keccak256("again")), "re-added publisher anchors");
    }

    // ---- ownership ---------------------------------------------------------

    function testOwnershipMovesInTwoSteps() public {
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(stranger);
        seal.transferOwnership(stranger);

        vm.expectRevert(HashKeySeal.ZeroOwner.selector);
        vm.prank(owner);
        seal.transferOwnership(address(0));

        vm.expectEmit(true, true, false, true);
        emit OwnershipTransferStarted(owner, second);
        vm.prank(owner);
        seal.transferOwnership(second);
        require(seal.owner() == owner, "owner unchanged until accepted");
        require(seal.pendingOwner() == second, "pending recorded");

        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(second);
        seal.setPublisher(stranger, true);

        vm.expectRevert(HashKeySeal.NotPendingOwner.selector);
        vm.prank(stranger);
        seal.acceptOwnership();

        vm.expectEmit(true, true, false, true);
        emit OwnershipTransferred(owner, second);
        vm.prank(second);
        seal.acceptOwnership();
        require(seal.owner() == second, "accepted");
        require(seal.pendingOwner() == address(0), "pending cleared");

        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(owner);
        seal.setPublisher(stranger, true);
        vm.prank(second);
        seal.setPublisher(stranger, true);
        require(seal.publishers(stranger), "the new owner sets publishers");

        vm.expectRevert(HashKeySeal.NotPendingOwner.selector);
        vm.prank(second);
        seal.acceptOwnership();
    }

    function testALaterProposalReplacesThePendingOwner() public {
        vm.prank(owner);
        seal.transferOwnership(second);
        vm.prank(owner);
        seal.transferOwnership(stranger);
        vm.expectRevert(HashKeySeal.NotPendingOwner.selector);
        vm.prank(second);
        seal.acceptOwnership();
        vm.prank(stranger);
        seal.acceptOwnership();
        require(seal.owner() == stranger, "the last proposal wins");
    }

    function testRenounceFreezesTheAllowlistAndLeavesPublishers() public {
        vm.prank(owner);
        seal.transferOwnership(second);
        vm.prank(owner);
        vm.expectEmit(true, false, false, true);
        emit OwnershipRenounced(owner);
        vm.expectEmit(true, true, false, true);
        emit OwnershipTransferred(owner, address(0));
        seal.renounceOwnership();
        _eq(uint256(uint160(seal.owner())), 0);
        require(seal.pendingOwner() == address(0), "a pending transfer dies with the owner");
        vm.expectRevert(HashKeySeal.NotPendingOwner.selector);
        vm.prank(second);
        seal.acceptOwnership();
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(owner);
        seal.setPublisher(stranger, true);
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(owner);
        seal.setPublisher(publisher, false);
        require(seal.publishers(publisher), "allowlist frozen, publisher stays");
        vm.prank(publisher);
        seal.anchor(keccak256("after-renounce"), CLAW_GUARD);
        require(seal.isAnchored(keccak256("after-renounce")), "publisher remains");
    }

    function testStrangerCannotRenounceAndRenounceTwiceReverts() public {
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(stranger);
        seal.renounceOwnership();
        require(seal.owner() == owner, "owner kept");
        vm.prank(owner);
        seal.renounceOwnership();
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(owner);
        seal.renounceOwnership();
    }

    function testAfterRenounceNotEvenAddressZeroIsTheOwner() public {
        vm.prank(owner);
        seal.renounceOwnership();
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(address(0));
        seal.setPublisher(stranger, true);
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(address(0));
        seal.renounceOwnership();
        vm.expectRevert(HashKeySeal.NotPendingOwner.selector);
        vm.prank(address(0));
        seal.acceptOwnership();
        require(seal.owner() == address(0) && seal.pendingOwner() == address(0), "nothing moved");
    }

    /// With nothing pending, pendingOwner is address(0); the accept check
    /// refuses that sender explicitly rather than letting it match.
    function testNobodyAcceptsWhileNothingIsPending() public {
        vm.expectRevert(HashKeySeal.NotPendingOwner.selector);
        vm.prank(address(0));
        seal.acceptOwnership();
        vm.expectRevert(HashKeySeal.NotPendingOwner.selector);
        vm.prank(stranger);
        seal.acceptOwnership();
        vm.expectRevert(HashKeySeal.NotPendingOwner.selector);
        vm.prank(owner);
        seal.acceptOwnership();
        require(seal.owner() == owner && seal.pendingOwner() == address(0), "unchanged");
    }

    // ---- value -------------------------------------------------------------

    function testACallCarryingValueDoesNotLandAndTheSameCallWithoutValueDoes() public {
        bytes32 hash = keccak256("value");
        vm.deal(publisher, 1 ether);
        vm.prank(publisher);
        (bool bare,) = address(seal).call{value: 1 ether}("");
        require(!bare, "bare value");
        bytes memory data = abi.encodeCall(HashKeySeal.anchor, (hash, CLAW_GUARD));
        vm.prank(publisher);
        (bool paid, bytes memory why) = address(seal).call{value: 1}(data);
        require(!paid, "anchor with value");
        require(why.length == 0, "rejected by the value guard, not by a custom error");
        require(address(seal).balance == 0, "balance");
        require(!seal.isAnchored(hash), "not stored");
        vm.prank(publisher);
        (bool free,) = address(seal).call(data);
        require(free, "the same calldata without value lands");
        require(seal.isAnchored(hash), "stored");
    }

    function testEverySelectorRejectsValue() public {
        vm.deal(owner, 10 ether);
        vm.deal(publisher, 10 ether);
        bytes32[] memory many = new bytes32[](1);
        many[0] = keccak256("m");
        bytes[] memory calls = new bytes[](8);
        calls[0] = abi.encodeCall(HashKeySeal.anchor, (keccak256("v"), CLAW_GUARD));
        calls[1] = abi.encodeCall(HashKeySeal.anchorMany, (many, CLAW_GUARD));
        calls[2] = abi.encodeCall(HashKeySeal.setPublisher, (stranger, true));
        calls[3] = abi.encodeCall(HashKeySeal.transferOwnership, (second));
        calls[4] = abi.encodeCall(HashKeySeal.acceptOwnership, ());
        calls[5] = abi.encodeCall(HashKeySeal.renounceOwnership, ());
        calls[6] = abi.encodeCall(HashKeySeal.isAnchored, (keccak256("v")));
        calls[7] = abi.encodeCall(HashKeySeal.anchorOf, (keccak256("v")));
        for (uint256 i = 0; i < calls.length; i++) {
            vm.prank(i < 2 ? publisher : owner);
            (bool ok, bytes memory ret) = address(seal).call{value: 1}(calls[i]);
            require(!ok && ret.length == 0, "every selector refuses value before any check");
        }
        require(address(seal).balance == 0, "balance");
        require(seal.owner() == owner && !seal.isAnchored(keccak256("v")), "nothing changed");
    }

    // ---- bytecode ----------------------------------------------------------

    /// The executable region of the runtime (the CBOR metadata trailer at the
    /// end is data, its length is in the last two bytes) carries no opcode
    /// that creates, calls into, or destroys another account.
    function testRuntimeHasNoCallCreateOrSelfdestruct() public view {
        bytes memory code = address(seal).code;
        uint256 meta = (uint256(uint8(code[code.length - 2])) << 8) | uint8(code[code.length - 1]);
        uint256 end = code.length - meta - 2;
        uint256 seen = 0;
        for (uint256 i = 0; i < end;) {
            uint8 op = uint8(code[i]);
            if (op >= 0x60 && op <= 0x7f) {
                i += 1 + (op - 0x5f);
                continue;
            }
            require(op != 0xf0 && op != 0xf5, "CREATE");
            require(op != 0xf1 && op != 0xf2 && op != 0xf4 && op != 0xfa, "CALL family");
            require(op != 0xff, "SELFDESTRUCT");
            require(op != 0x5c && op != 0x5d, "transient storage");
            // MCOPY is Cancun-only; foundry.toml says the runtime needs no Cancun opcode.
            require(op != 0x5e, "MCOPY");
            if (op == 0x34) seen++;
            i++;
        }
        require(seen >= 1, "the CALLVALUE guard is present");
    }

    // ---- fuzz --------------------------------------------------------------

    function testFuzz_publisherAnchorsOnce(bytes32 hash, bytes32 kind) public {
        vm.assume(hash != bytes32(0));
        vm.assume(kind != bytes32(0));
        vm.prank(publisher);
        seal.anchor(hash, kind);
        require(seal.isAnchored(hash), "anchored");
        (address who, bytes32 stored) = seal.anchorOf(hash);
        require(who == publisher && stored == kind, "attributed");
        vm.expectRevert(HashKeySeal.DuplicateHash.selector);
        vm.prank(publisher);
        seal.anchor(hash, keccak256(abi.encode(kind)));
    }

    function testFuzz_strangerCannotAnchor(address caller, bytes32 hash, bytes32 kind) public {
        vm.assume(caller != publisher);
        vm.assume(hash != bytes32(0));
        vm.assume(kind != bytes32(0));
        vm.expectRevert(HashKeySeal.NotPublisher.selector);
        vm.prank(caller);
        seal.anchor(hash, kind);
        require(!seal.isAnchored(hash), "untouched");
    }

    function testFuzz_onlyTheOwnerChangesTheAllowlist(address caller, address who, bool allowed) public {
        vm.assume(caller != owner);
        vm.assume(who != address(0));
        vm.assume(who != publisher);
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(caller);
        seal.setPublisher(who, allowed);
        require(!seal.publishers(who), "unchanged");
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(caller);
        seal.setPublisher(publisher, false);
        require(seal.publishers(publisher), "the publisher stays");
    }

    function testFuzz_afterRenounceNoCallerChangesTheAllowlist(address caller, address who) public {
        vm.assume(who != address(0));
        vm.assume(who != publisher);
        vm.prank(owner);
        seal.renounceOwnership();
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(caller);
        seal.setPublisher(who, true);
        vm.expectRevert(HashKeySeal.NotOwner.selector);
        vm.prank(caller);
        seal.setPublisher(publisher, false);
        require(seal.publishers(publisher) && !seal.publishers(who), "frozen");
    }

    function testFuzz_onlyThePendingOwnerAccepts(address caller) public {
        vm.assume(caller != second);
        vm.prank(owner);
        seal.transferOwnership(second);
        vm.expectRevert(HashKeySeal.NotPendingOwner.selector);
        vm.prank(caller);
        seal.acceptOwnership();
        require(seal.owner() == owner, "unchanged");
    }
}
