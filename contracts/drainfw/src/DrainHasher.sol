// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {DrainErrors} from "./DrainErrors.sol";
import {DrainHash} from "./DrainHash.sol";
import {DrainTypes} from "./DrainTypes.sol";

/// @title DrainHasher
/// @notice One shared EIP-712 helper per chain. The policy calls it so the
///         hash code is not copied into every policy. Accounts do not deploy
///         their own copy.
/// @dev Clean-room. Not derived from Zodiac Roles (BUSL-1.1).
interface IDrainCommit {
    function PERMIT2() external view returns (address);
    function HASHER() external view returns (address);
    function installed(address account) external view returns (bool);
    function reserveFor(address account, address token, uint256 amount, uint256 deadline, address spender)
        external
        returns (uint64);
    function keepFor(address account, bytes32 digest, uint64 expiry) external;
    function vaultOf(address account) external view returns (address);
    function roleOf(address account, address who) external view returns (uint8);
}

contract DrainHasher {
    IDrainCommit public committer;

    constructor() {
        if (DrainTypes.mainnet(block.chainid)) revert DrainErrors.Mainnet();
    }

    function bind(address policy) external {
        if (address(committer) != address(0)) revert DrainErrors.AlreadyInit();
        if (policy == address(0) || IDrainCommit(policy).HASHER() != address(this)) revert DrainErrors.BadCaller();
        committer = IDrainCommit(policy);
    }

    function single(DrainHash.PermitSingle calldata permit, address permit2) external view returns (bytes32) {
        return DrainHash.single(permit, permit2);
    }

    function batch(
        DrainHash.PermitDetails[] calldata details,
        address spender,
        uint256 sigDeadline,
        address permit2
    ) external view returns (bytes32) {
        return DrainHash.batch(details, spender, sigDeadline, permit2);
    }

    function transferFrom(DrainHash.PermitTransferFrom calldata permit, address spender, address permit2)
        external
        view
        returns (bytes32)
    {
        return DrainHash.transferFrom(permit, spender, permit2);
    }

    function batchTransfer(
        DrainHash.TokenPermissions[] calldata permitted,
        address spender,
        uint256 nonce,
        uint256 deadline,
        address permit2
    ) external view returns (bytes32) {
        return DrainHash.batchTransfer(permitted, spender, nonce, deadline, permit2);
    }

    function witness(
        DrainHash.PermitTransferFrom calldata permit,
        address spender,
        bytes32 witnessHash,
        string calldata witnessTypeString,
        address permit2
    ) external view returns (bytes32) {
        return DrainHash.witness(permit, spender, witnessHash, witnessTypeString, permit2);
    }

    function eip2612(
        bytes32 domainSeparator,
        address owner,
        address spender,
        uint256 value,
        uint256 nonce,
        uint256 deadline
    ) external pure returns (bytes32) {
        return DrainHash.eip2612(domainSeparator, owner, spender, value, nonce, deadline);
    }

    function eip3009(
        bytes32 domainSeparator,
        bytes32 typeHash,
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce
    ) external pure returns (bytes32) {
        return DrainHash.eip3009(domainSeparator, typeHash, from, to, value, validAfter, validBefore, nonce);
    }

    function registerPermit2Single(address permit2, DrainHash.PermitSingle calldata permit) external {
        _permit2(permit2);
        uint64 exp = committer.reserveFor(msg.sender, permit.details.token, permit.details.amount, permit.sigDeadline, permit.spender);
        committer.keepFor(msg.sender, DrainHash.single(permit, permit2), exp);
    }

    function registerPermit2Batch(
        address permit2,
        DrainHash.PermitDetails[] calldata details,
        address spender,
        uint256 sigDeadline
    ) external {
        _permit2(permit2);
        if (details.length == 0 || details.length > 16) revert DrainErrors.BadPath();
        uint64 exp = type(uint64).max;
        for (uint256 i = 0; i < details.length; i++) {
            uint64 one = committer.reserveFor(
                msg.sender, details[i].token, details[i].amount, sigDeadline, i == 0 ? spender : address(0)
            );
            if (one < exp) exp = one;
        }
        committer.keepFor(msg.sender, DrainHash.batch(details, spender, sigDeadline, permit2), exp);
    }

    function registerPermit2Transfer(address permit2, DrainHash.PermitTransferFrom calldata permit, address spender)
        external
    {
        _permit2(permit2);
        uint64 exp = committer.reserveFor(msg.sender, permit.permitted.token, permit.permitted.amount, permit.deadline, spender);
        committer.keepFor(msg.sender, DrainHash.transferFrom(permit, spender, permit2), exp);
    }

    function registerPermit2BatchTransfer(
        address permit2,
        DrainHash.TokenPermissions[] calldata permitted,
        address spender,
        uint256 nonce,
        uint256 deadline
    ) external {
        _permit2(permit2);
        if (permitted.length == 0 || permitted.length > 16) revert DrainErrors.BadPath();
        uint64 exp = type(uint64).max;
        for (uint256 i = 0; i < permitted.length; i++) {
            uint64 one = committer.reserveFor(
                msg.sender, permitted[i].token, permitted[i].amount, deadline, i == 0 ? spender : address(0)
            );
            if (one < exp) exp = one;
        }
        committer.keepFor(msg.sender, DrainHash.batchTransfer(permitted, spender, nonce, deadline, permit2), exp);
    }

    function registerPermit2Witness(
        address permit2,
        DrainHash.PermitTransferFrom calldata permit,
        address spender,
        bytes32 witnessHash,
        string calldata witnessTypeString
    ) external {
        _permit2(permit2);
        uint64 exp = committer.reserveFor(msg.sender, permit.permitted.token, permit.permitted.amount, permit.deadline, spender);
        committer.keepFor(msg.sender, DrainHash.witness(permit, spender, witnessHash, witnessTypeString, permit2), exp);
    }

    function registerEIP2612(
        address token,
        address owner,
        address spender,
        uint256 value,
        uint256 nonce,
        uint256 deadline
    ) external {
        if (owner != msg.sender) revert DrainErrors.BadTarget();
        (bool ok, bytes memory ret) = token.staticcall(abi.encodeWithSelector(bytes4(0x3644e515)));
        if (!ok || ret.length < 32) revert DrainErrors.BadTarget();
        bytes32 domain = abi.decode(ret, (bytes32));
        (ok, ret) = token.staticcall(abi.encodeWithSelector(bytes4(0x7ecebe00), owner));
        if (!ok || ret.length < 32) revert DrainErrors.BadTarget();
        if (abi.decode(ret, (uint256)) != nonce) revert DrainErrors.OverCap(DrainTypes.EXPY);
        uint64 exp = committer.reserveFor(msg.sender, token, value, deadline, spender);
        committer.keepFor(msg.sender, DrainHash.eip2612(domain, owner, spender, value, nonce, deadline), exp);
    }

    function registerEIP3009(
        address token,
        bytes32 typeHash,
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce
    ) external {
        if (from != msg.sender) revert DrainErrors.BadTarget();
        if (address(committer) == address(0) || !committer.installed(msg.sender)) revert DrainErrors.NotInstalled();
        if (to != committer.vaultOf(msg.sender) && (committer.roleOf(msg.sender, to) & 1) == 0) {
            revert DrainErrors.NeedsQueue(DrainTypes.RCPT);
        }
        (bool ok, bytes memory ret) = token.staticcall(abi.encodeWithSelector(bytes4(0x3644e515)));
        if (!ok || ret.length < 32) revert DrainErrors.BadTarget();
        bytes32 digest = DrainHash.eip3009(abi.decode(ret, (bytes32)), typeHash, from, to, value, validAfter, validBefore, nonce);
        if (digest == bytes32(0)) revert DrainErrors.BadTarget();
        uint64 exp = committer.reserveFor(msg.sender, token, value, validBefore, address(0));
        if (validAfter >= exp) revert DrainErrors.OverCap(DrainTypes.EXPY);
        committer.keepFor(msg.sender, digest, exp);
    }

    function _permit2(address permit2) internal view {
        if (address(committer) == address(0) || permit2 != committer.PERMIT2()) revert DrainErrors.BadTarget();
    }
}
