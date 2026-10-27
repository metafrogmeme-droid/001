// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.24;

import {HashKeySeal} from "../src/HashKeySeal.sol";

interface Vm {
    function prank(address) external;
    function deal(address, uint256) external;
}

/// The fuzzer picks an actor and an action; ghosts record what succeeded.
/// Every call is a low-level call so a revert is a recorded fact, not a
/// failed run. forge-std is absent: the invariant runner needs only the
/// `invariant_` prefix and a `targetContracts()` getter.
contract SealHandler {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    HashKeySeal public seal;
    address public deployer;
    address[4] public actors; // deployer, publisher, second, stranger

    bytes32[] public anchoredList;
    mapping(bytes32 => address) public ghostPublisher;
    mapping(bytes32 => bytes32) public ghostKind;
    uint256 public anchorsByNonPublisher;
    uint256 public allowlistChangesByNonOwner;
    uint256 public ownerChangesWithoutAccept;
    uint256 public successfulSets;
    uint256 public successfulAnchors;
    bool public renounced;
    address public lastAcceptedOwner;

    constructor(HashKeySeal s, address d, address p, address q, address x) {
        seal = s;
        deployer = d;
        actors = [d, p, q, x];
    }

    function anchor(uint8 who, bytes32 h, bytes32 k) external {
        address a = actors[who % 4];
        bool wasPublisher = seal.publishers(a);
        vm.prank(a);
        (bool ok,) = address(seal).call(abi.encodeCall(HashKeySeal.anchor, (h, k)));
        if (ok) {
            successfulAnchors++;
            if (!wasPublisher) anchorsByNonPublisher++;
            if (ghostPublisher[h] == address(0)) {
                ghostPublisher[h] = a;
                ghostKind[h] = k;
                anchoredList.push(h);
            }
        }
    }

    function anchorMany(uint8 who, bytes32 h1, bytes32 h2, bytes32 k) external {
        address a = actors[who % 4];
        bool wasPublisher = seal.publishers(a);
        bytes32[] memory hs = new bytes32[](2);
        hs[0] = h1;
        hs[1] = h2;
        vm.prank(a);
        (bool ok,) = address(seal).call(abi.encodeCall(HashKeySeal.anchorMany, (hs, k)));
        if (ok) {
            successfulAnchors += 2;
            if (!wasPublisher) anchorsByNonPublisher++;
            for (uint256 i = 0; i < 2; i++) {
                if (ghostPublisher[hs[i]] == address(0)) {
                    ghostPublisher[hs[i]] = a;
                    ghostKind[hs[i]] = k;
                    anchoredList.push(hs[i]);
                }
            }
        }
    }

    function setPublisher(uint8 who, uint8 target, bool allowed) external {
        address a = actors[who % 4];
        address t = actors[target % 4];
        bool wasOwner = seal.owner() == a;
        vm.prank(a);
        (bool ok,) = address(seal).call(abi.encodeCall(HashKeySeal.setPublisher, (t, allowed)));
        if (ok) {
            successfulSets++;
            if (!wasOwner) allowlistChangesByNonOwner++;
        }
    }

    function transfer(uint8 who, uint8 target) external {
        address a = actors[who % 4];
        address t = actors[target % 4];
        bool wasOwner = seal.owner() == a;
        vm.prank(a);
        (bool ok,) = address(seal).call(abi.encodeCall(HashKeySeal.transferOwnership, (t)));
        if (ok && !wasOwner) allowlistChangesByNonOwner++;
    }

    function accept(uint8 who) external {
        address a = actors[who % 4];
        bool wasPending = seal.pendingOwner() == a;
        vm.prank(a);
        (bool ok,) = address(seal).call(abi.encodeCall(HashKeySeal.acceptOwnership, ()));
        if (ok) {
            if (!wasPending) ownerChangesWithoutAccept++;
            lastAcceptedOwner = a;
        }
    }

    function renounce(uint8 who) external {
        address a = actors[who % 4];
        bool wasOwner = seal.owner() == a;
        vm.prank(a);
        (bool ok,) = address(seal).call(abi.encodeCall(HashKeySeal.renounceOwnership, ()));
        if (ok) {
            renounced = true;
            if (!wasOwner) allowlistChangesByNonOwner++;
        }
    }

    function sendValue(uint8 who, uint96 v) external {
        address a = actors[who % 4];
        vm.deal(a, uint256(v) + 1);
        vm.prank(a);
        (bool ok,) = address(seal).call{value: v}("");
        ok;
    }

    function count() external view returns (uint256) {
        return anchoredList.length;
    }
}

contract HashKeySealInvariants {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    address internal deployer = address(0xA11CE);
    address internal publisher = address(0xB0B);
    address internal second = address(0xD0D);
    address internal stranger = address(0xCAFE);

    HashKeySeal internal seal;
    SealHandler internal handler;
    address[] private _targets;

    function setUp() public {
        vm.prank(deployer);
        seal = new HashKeySeal(publisher);
        handler = new SealHandler(seal, deployer, publisher, second, stranger);
        _targets.push(address(handler));
    }

    function targetContracts() external view returns (address[] memory) {
        return _targets;
    }

    function invariant_noCallLandsValue() public view {
        require(address(seal).balance == 0, "seal holds value");
    }

    function invariant_ownerIsAnActorOrZeroAndZeroOnlyAfterRenounce() public view {
        address o = seal.owner();
        bool actor = o == deployer || o == publisher || o == second || o == stranger;
        require(actor || o == address(0), "owner is someone else");
        if (handler.renounced()) require(o == address(0), "renounce did not zero the owner");
        if (o == address(0)) require(handler.renounced(), "owner zero without a renounce");
    }

    function invariant_ownerOnlyChangesByAccept() public view {
        require(handler.ownerChangesWithoutAccept() == 0, "owner changed without the pending owner accepting");
        address o = seal.owner();
        if (o != deployer && o != address(0)) require(handler.lastAcceptedOwner() == o, "owner is not the last acceptor");
    }

    function invariant_nonPublisherNeverAnchored() public view {
        require(handler.anchorsByNonPublisher() == 0, "non-publisher anchored");
    }

    function invariant_nonOwnerNeverChangedTheAllowlistOrTheOwner() public view {
        require(handler.allowlistChangesByNonOwner() == 0, "non-owner changed allowlist or ownership");
    }

    function invariant_everyGhostAnchorStaysWithItsFirstWriter() public view {
        uint256 n = handler.count();
        for (uint256 i = 0; i < n; i++) {
            bytes32 h = handler.anchoredList(i);
            (address who, bytes32 kind) = seal.anchorOf(h);
            require(seal.isAnchored(h), "anchor lost");
            require(who == handler.ghostPublisher(h) && kind == handler.ghostKind(h), "attribution changed");
        }
    }

    /// Every successful anchor call stored exactly one fresh hash per
    /// element: a contract that skipped or double-wrote a duplicate inside a
    /// batch would break this before any unit test saw it.
    function invariant_everySuccessfulAnchorStoredOneFreshHash() public view {
        require(handler.successfulAnchors() == handler.count(), "anchors and stored hashes disagree");
    }

    function invariant_zeroHashAndZeroPublisherNeverStored() public view {
        require(!seal.isAnchored(bytes32(0)), "zero hash");
        require(!seal.publishers(address(0)), "zero publisher");
    }

    function invariant_strangerIsAPublisherOnlyAfterAnOwnerSet() public view {
        if (seal.publishers(stranger) || seal.publishers(second) || seal.publishers(deployer)) {
            require(handler.successfulSets() > 0, "allowlisted without a set");
        }
    }
}
