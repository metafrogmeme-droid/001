// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {DrainErrors} from "./DrainErrors.sol";
import {DrainPolicy} from "./DrainPolicy.sol";
import {DrainRevoke} from "./DrainRevoke.sol";
import {DrainTypes} from "./DrainTypes.sol";

/// @title DrainHook7579
/// @notice One shared ERC-7579 hook, executor, and ERC-1271 pre-validation
///         hook. Avalanche Fuji installs this on a smart account. It does not
///         publish an EIP-7702 delegation. A plain Safe guard is a later module.
/// @dev Clean-room. Not derived from Zodiac Roles (BUSL-1.1). No admin key.
///      Delegatecall is refused. The delegatecall allow-list is empty and
///      there is no setter. Module types: 2 executor, 4 hook, 8 pre-validation.
contract DrainHook7579 {
    DrainPolicy public immutable POLICY;

    struct Execution {
        address target;
        uint256 value;
        bytes callData;
    }

    event Evacuated(address indexed account, address indexed vault);

    constructor(DrainPolicy policy_) {
        if (DrainTypes.mainnet(block.chainid)) revert DrainErrors.Mainnet();
        if (address(policy_) == address(0)) revert DrainErrors.BadTarget();
        POLICY = policy_;
    }

    function onInstall(bytes calldata) external {
        POLICY.bindHook(msg.sender);
    }

    function onUninstall(bytes calldata) external {
        POLICY.consumeUninstall(msg.sender);
    }

    function isModuleType(uint256 moduleTypeId) external pure returns (bool) {
        return moduleTypeId == 2 || moduleTypeId == 4 || moduleTypeId == 8;
    }

    function preCheck(address sender, uint256, bytes calldata data) external returns (bytes memory) {
        address account = msg.sender;
        if (!POLICY.installed(account) || POLICY.hookOf(account) != address(this) || !POLICY.hookBound(account)) {
            revert DrainErrors.BadCaller();
        }
        if (data.length < 4) revert DrainErrors.Denied(DrainTypes.UNKN);
        bytes4 sel = bytes4(data[:4]);
        if (sel != DrainTypes.SEL_EXECUTE && sel != DrainTypes.SEL_EXECUTE_EXECUTOR) {
            revert DrainErrors.Denied(DrainTypes.UNKN);
        }
        (bytes32 mode, bytes memory execData) = abi.decode(data[4:], (bytes32, bytes));
        uint8 callType = uint8(uint256(mode) >> 248);
        if (callType == 0xFF) revert DrainErrors.Denied(DrainTypes.DCAL);
        if (callType == 0xFE) return "";
        uint256 grant = _grant(account, sender);
        if (callType == 0x00) {
            (address target, uint256 value, bytes memory cd) = _one(execData);
            _leg(account, sender, target, value, cd, grant);
        } else if (callType == 0x01) {
            Execution[] memory batch = abi.decode(execData, (Execution[]));
            for (uint256 i = 0; i < batch.length; i++) {
                _leg(account, sender, batch[i].target, batch[i].value, batch[i].callData, grant);
            }
        } else {
            revert DrainErrors.Denied(DrainTypes.UNKN);
        }
        return "";
    }

    function postCheck(bytes calldata) external pure {}

    /// @dev Reverts unless the digest was registered. A plain v, r, s is not recovered.
    function preValidationHookERC1271(address, bytes32 hash, bytes calldata)
        external
        view
        returns (bytes32, bytes memory)
    {
        if (!POLICY.checkDigest(msg.sender, hash)) revert DrainErrors.Denied(DrainTypes.OCAP);
        return (hash, "");
    }

    function guardianRevoke(
        address account,
        uint8 kind,
        address token,
        address spender,
        uint48 nonce,
        uint256 wordPos,
        uint256 mask,
        bytes32 digest
    ) external {
        _rescueCaller(account);
        (address target, bytes memory data) =
            DrainRevoke.build(POLICY, account, kind, token, spender, nonce, wordPos, mask, digest);
        bytes memory packed = abi.encodePacked(target, uint256(0), data);
        IAccount(account).executeFromExecutor(bytes32(0), packed);
    }

    function evacuate(address account, address[] calldata tokens) external {
        _rescueCaller(account);
        if (tokens.length > 32) revert DrainErrors.BadPath();
        address vault = POLICY.vaultOf(account);
        uint256 nativeBal = account.balance;
        if (nativeBal > 0) {
            bytes memory packed = abi.encodePacked(vault, nativeBal, bytes(""));
            try IAccount(account).executeFromExecutor(bytes32(0), packed) {} catch {}
        }
        address[] memory spenders = POLICY.spendersOf(account);
        for (uint256 i = 0; i < tokens.length; i++) {
            if (tokens[i] == address(0)) continue;
            uint256 amount = _balance(tokens[i], account);
            if (amount > 0) {
                bytes memory cd = abi.encodeWithSelector(DrainTypes.SEL_TRANSFER, vault, amount);
                bytes memory packed = abi.encodePacked(tokens[i], uint256(0), cd);
                try IAccount(account).executeFromExecutor(bytes32(0), packed) {} catch {}
            }
            for (uint256 j = 0; j < spenders.length && j < 16; j++) {
                bytes memory z = abi.encodeWithSelector(DrainTypes.SEL_APPROVE, spenders[j], uint256(0));
                bytes memory packedZ = abi.encodePacked(tokens[i], uint256(0), z);
                try IAccount(account).executeFromExecutor(bytes32(0), packedZ) {} catch {}
            }
        }
        emit Evacuated(account, vault);
    }

    function _rescueCaller(address account) internal view {
        if (msg.sender != POLICY.guardianOf(account) && msg.sender != POLICY.ownerOf(account)) {
            revert DrainErrors.NotGuardian();
        }
    }

    function _grant(address account, address sender) internal view returns (uint256) {
        if (sender == address(this)) return 0;
        uint256 grant = POLICY.executorGrantOf(account, sender);
        if (grant != 0) return grant;
        if (sender == account || sender == POLICY.ownerOf(account)) return 0;
        revert DrainErrors.BadCaller();
    }

    function _leg(address account, address sender, address target, uint256 value, bytes memory data, uint256 grant)
        internal
    {
        if (sender == address(this)) {
            if (!_rescueLeg(account, target, value, data)) revert DrainErrors.Denied(DrainTypes.AGNT);
        }
        bytes4 sel = data.length >= 4 ? bytes4(data) : bytes4(0);
        if (grant != 0 && target == address(POLICY) && POLICY.isPolicyAdmin(sel)) revert DrainErrors.AgentNoQueue();
        if (grant != 0 && target == address(POLICY.HASHER()) && POLICY.isHasherAdmin(sel)) {
            revert DrainErrors.AgentNoQueue();
        }
        (uint8 verdict, bytes4 reason) = POLICY.check(account, target, value, data, grant);
        if (verdict == 2 || (verdict == 1 && grant != 0)) revert DrainErrors.Denied(reason);
        if (verdict == 1) revert DrainErrors.Queued(bytes32(0), reason);
    }

    function _rescueLeg(address account, address target, uint256 value, bytes memory data) internal view returns (bool) {
        address vault = POLICY.vaultOf(account);
        if (data.length >= 4) {
            bytes4 sel = bytes4(data);
            if (sel == DrainTypes.SEL_APPROVE && data.length >= 68 && _word(data, 1) == 0) return true;
            if (sel == DrainTypes.SEL_FOR_ALL && data.length >= 68 && _word(data, 1) == 0) return true;
            if (
                sel == DrainTypes.SEL_LOCKDOWN || sel == DrainTypes.SEL_INVALIDATE_NONCES
                    || sel == DrainTypes.SEL_INVALIDATE_UNORDERED || sel == DrainTypes.SEL_CANCEL_AUTH
            ) return true;
            if (sel == DrainPolicy.clearDigest.selector) return true;
            if (sel == DrainTypes.SEL_TRANSFER && data.length >= 68 && address(uint160(_word(data, 0))) == vault) {
                return true;
            }
        }
        return value > 0 && target == vault && data.length == 0;
    }

    function _one(bytes memory exec) internal pure returns (address target, uint256 value, bytes memory cd) {
        if (exec.length < 52) revert DrainErrors.BadTarget();
        assembly {
            target := shr(96, mload(add(exec, 32)))
            value := mload(add(exec, 52))
        }
        uint256 len = exec.length - 52;
        cd = new bytes(len);
        assembly {
            let src := add(exec, 84)
            let dst := add(cd, 32)
            for { let i := 0 } lt(i, len) { i := add(i, 32) } { mstore(add(dst, i), mload(add(src, i))) }
        }
    }

    function _balance(address token, address account) internal view returns (uint256) {
        (bool ok, bytes memory ret) = token.staticcall(abi.encodeWithSelector(bytes4(0x70a08231), account));
        if (!ok || ret.length < 32) return 0;
        return abi.decode(ret, (uint256));
    }

    function _word(bytes memory data, uint256 i) internal pure returns (uint256 w) {
        assembly {
            w := mload(add(add(data, 36), mul(i, 32)))
        }
    }
}

interface IAccount {
    function executeFromExecutor(bytes32 mode, bytes calldata executionCalldata) external payable;
}
