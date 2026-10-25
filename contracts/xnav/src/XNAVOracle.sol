// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

import {XNAVTestnet} from "./XNAVTestnet.sol";

/// @notice 2-of-3 exit-haircut feed. An unset or stale vault reverts.
///         It does not answer zero. secp256k1 only: scheme id 0 is the
///         one this contract can check. Any other scheme id reverts.
contract XNAVOracle is XNAVTestnet {
    bytes32 public constant DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
    bytes32 public constant UPDATE_TYPEHASH = keccak256(
        "XNAVUpdate(address vault,uint32 xnavBps,uint32 stressBps,uint64 ts)"
    );

    string public constant NAME = "XNAV";
    string public constant VERSION = "1";

    struct Reading {
        uint32 xnavBps;
        uint32 stressBps;
        uint64 ts;
        bool set;
    }

    mapping(address => bool) public isSigner;
    mapping(address => Reading) public readings;
    uint256 public immutable threshold;
    uint256 public immutable maxStaleness;
    uint256 public immutable maxDeltaBps;
    uint256 public immutable maxFeedBps;
    uint256 public immutable l1GasBase;
    uint256 public immutable l1GasPerWord;

    constructor(
        address[] memory signers,
        uint256 threshold_,
        uint256 maxStaleness_,
        uint256 maxDeltaBps_,
        uint256 maxFeedBps_,
        uint256 l1GasBase_,
        uint256 l1GasPerWord_
    ) {
        require(threshold_ >= 2 && threshold_ <= signers.length, "threshold");
        require(maxStaleness_ > 0 && maxFeedBps_ > 0, "bounds");
        uint256 n = 0;
        for (uint256 i = 0; i < signers.length; i++) {
            address who = signers[i];
            require(who != address(0) && !isSigner[who], "signer");
            isSigner[who] = true;
            n += 1;
        }
        require(n >= threshold_, "threshold");
        threshold = threshold_;
        maxStaleness = maxStaleness_;
        maxDeltaBps = maxDeltaBps_;
        maxFeedBps = maxFeedBps_;
        l1GasBase = l1GasBase_;
        l1GasPerWord = l1GasPerWord_;
    }

    /// @notice Documented L1 read schedule. The factors are constructor
    ///         arguments, taken from the placeholder file, not a measurement.
    function l1ReadGas(uint256 inWords, uint256 outWords) external view returns (uint256) {
        return l1GasBase + l1GasPerWord * (inWords + outWords);
    }

    /// @notice Scheme 0 is secp256k1. Anything else reverts.
    function rejectScheme(uint8 scheme) external pure {
        require(scheme == 0, "scheme");
    }

    function digestFor(
        uint256 chainId,
        address verifying,
        address vault,
        uint32 xnavBps,
        uint32 stressBps,
        uint64 ts
    ) public pure returns (bytes32) {
        bytes32 structHash = keccak256(abi.encode(UPDATE_TYPEHASH, vault, xnavBps, stressBps, ts));
        bytes32 domain = keccak256(abi.encode(
            DOMAIN_TYPEHASH,
            keccak256(bytes(NAME)),
            keccak256(bytes(VERSION)),
            chainId,
            verifying
        ));
        return keccak256(abi.encodePacked(bytes1(0x19), bytes1(0x01), domain, structHash));
    }

    function update(
        address vault,
        uint32 xnavBps,
        uint32 stressBps,
        uint64 ts,
        bytes calldata sigA,
        bytes calldata sigB
    ) external {
        require(uint256(xnavBps) <= maxFeedBps && uint256(stressBps) <= maxFeedBps, "bound");
        require(ts <= block.timestamp + maxStaleness, "stale");
        require(block.timestamp <= uint256(ts) + maxStaleness, "stale");
        Reading memory prev = readings[vault];
        if (prev.set) {
            require(ts > prev.ts, "replay");
            uint256 a = xnavBps;
            uint256 b = prev.xnavBps;
            uint256 diff = a > b ? a - b : b - a;
            require(diff <= maxDeltaBps, "delta");
        }
        bytes32 hash = digestFor(block.chainid, address(this), vault, xnavBps, stressBps, ts);
        address one = _recover(hash, sigA);
        address two = _recover(hash, sigB);
        require(one != two && isSigner[one] && isSigner[two], "bad sig");
        readings[vault] = Reading(xnavBps, stressBps, ts, true);
    }

    /// @notice Reverts when nothing has been stored, or the stored reading
    ///         is older than maxStaleness. Neither case answers zero.
    function latest(address vault) external view returns (uint32 xnavBps, uint32 stressBps, uint64 ts) {
        Reading memory row = readings[vault];
        require(row.set, "unset");
        require(block.timestamp <= uint256(row.ts) + maxStaleness, "stale");
        return (row.xnavBps, row.stressBps, row.ts);
    }

    function _recover(bytes32 hash, bytes calldata sig) internal pure returns (address) {
        if (sig.length != 65) return address(0);
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 32))
            v := byte(0, calldataload(add(sig.offset, 64)))
        }
        if (v < 27) v += 27;
        if (v != 27 && v != 28) return address(0);
        return ecrecover(hash, v, r, s);
    }
}
