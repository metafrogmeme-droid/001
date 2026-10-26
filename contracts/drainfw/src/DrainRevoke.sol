// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {DrainErrors} from "./DrainErrors.sol";
import {DrainPolicy} from "./DrainPolicy.sol";
import {DrainTypes} from "./DrainTypes.sol";

/// @title DrainRevoke
/// @notice Calldata for a revoke. Lives outside the policy so the shared
///         policy stays within the 24,576-byte deploy limit.
/// @dev Clean-room. Not derived from Zodiac Roles (BUSL-1.1).
library DrainRevoke {
    function build(
        DrainPolicy policy,
        address account,
        uint8 kind,
        address token,
        address spender,
        uint48 nonce,
        uint256 wordPos,
        uint256 mask,
        bytes32 digest
    ) internal view returns (address target, bytes memory data) {
        if (!policy.installed(account)) revert DrainErrors.NotInstalled();
        if (kind == DrainTypes.REV_APPROVE || kind == DrainTypes.REV_FORALL) {
            (,,,, bool tracked) = policy.tokenOf(account, token);
            if (!tracked) revert DrainErrors.BadTarget();
            if (kind == DrainTypes.REV_APPROVE) {
                return (token, abi.encodeWithSelector(DrainTypes.SEL_APPROVE, spender, uint256(0)));
            }
            return (token, abi.encodeWithSelector(DrainTypes.SEL_FOR_ALL, spender, false));
        }
        if (kind == DrainTypes.REV_LOCKDOWN) {
            return (policy.PERMIT2(), abi.encodePacked(DrainTypes.SEL_LOCKDOWN, _onePair(token, spender)));
        }
        if (kind == DrainTypes.REV_NONCE) {
            return (policy.PERMIT2(), abi.encodeWithSelector(DrainTypes.SEL_INVALIDATE_NONCES, token, spender, nonce));
        }
        if (kind == DrainTypes.REV_UNORDERED) {
            return (policy.PERMIT2(), abi.encodeWithSelector(DrainTypes.SEL_INVALIDATE_UNORDERED, wordPos, mask));
        }
        if (kind == DrainTypes.REV_DIGEST) {
            return (address(policy), abi.encodeWithSelector(DrainPolicy.clearDigest.selector, account, digest));
        }
        revert DrainErrors.BadPath();
    }

    function _onePair(address token, address spender) private pure returns (bytes memory) {
        return abi.encodePacked(
            bytes32(uint256(32)),
            bytes32(uint256(1)),
            bytes32(uint256(uint160(token))),
            bytes32(uint256(uint160(spender)))
        );
    }
}
