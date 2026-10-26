// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {DrainErrors} from "./DrainErrors.sol";
import {DrainPolicy} from "./DrainPolicy.sol";
import {DrainRevoke} from "./DrainRevoke.sol";
import {DrainTypes} from "./DrainTypes.sol";

/// @title FirewallAccount
/// @notice EIP-7702 delegate for the Claw Firewall. This is a test of whether
///         a wallet will delegate to this contract, on a chain that supports
///         EIP-7702 (Base Sepolia, and the Arbitrum Sepolia tests). Avalanche
///         Fuji has no EIP-7702; the constructor refuses that chain.
/// @dev Clean-room. Not derived from Zodiac Roles (BUSL-1.1).
///
///      Init is a self-call, or an EIP-191 signature by this account over the
///      exact parameters. A stranger who calls init after the delegation is
///      published cannot choose the guardian or the vault.
///
///      The root key still sends ordinary transactions and can point the
///      delegation at another contract or at address(0). That bypasses this
///      module. A phished key is not something the delegate can stop.
///
///      `isValidSignature` does not recover a plain v, r, s. Permit2 and
///      USDC v2.2 staticcall it, so an unregistered digest is invalid and a
///      facilitator that only has v, r, s fails closed. Tokens that ecrecover
///      before ERC-1271 (DAI, OpenZeppelin ERC20Permit, USDS) never call this
///      function. Installing the delegate also makes outstanding unused
///      Permit2 and USDC v2.2 signatures fail until they are registered.
contract FirewallAccount {
    /// @dev ERC-7201 for the initialised flag and the reentrancy lock only.
    ///      keccak256(abi.encode(uint256(keccak256("runeclaw.firewall.account.v1")) - 1)) & ~bytes32(uint256(0xff))
    bytes32 public constant ACCOUNT_SLOT = 0x2e30086a22b22174b0577010a5f98e60a12ac559423fa9effef8beb64db4c400;

    struct AStore {
        bool initialized;
        uint8 lock;
    }

    struct Call {
        address target;
        uint256 value;
        bytes data;
    }

    DrainPolicy public immutable POLICY;

    event Evacuated(address indexed vault);

    constructor(DrainPolicy policy_) {
        if (DrainTypes.mainnet(block.chainid)) revert DrainErrors.Mainnet();
        if (!DrainTypes.supports7702(block.chainid)) revert DrainErrors.ChainRefused();
        if (address(policy_) == address(0)) revert DrainErrors.BadTarget();
        POLICY = policy_;
        _store().initialized = true;
    }

    function init(address guardian, address vault, bytes calldata sig) external {
        bytes32 digest = keccak256(abi.encode(block.chainid, address(this), guardian, vault));
        _gate(digest, sig);
        DrainPolicy.Init memory cfg;
        cfg.guardian = guardian;
        cfg.vault = vault;
        cfg.mode = DrainTypes.MODE_QUEUE;
        POLICY.initAccount(cfg);
    }

    function initFull(DrainPolicy.Init calldata cfg, bytes calldata sig) external {
        _gate(initDigest(cfg), sig);
        POLICY.initAccount(cfg);
    }

    /// @dev Every init field is inside this digest. A signature for one set of
    ///      caps, delays, or known addresses does not authorise another.
    function initDigest(DrainPolicy.Init calldata cfg) public view returns (bytes32) {
        bytes32 head = keccak256(
            abi.encode(block.chainid, address(this), cfg.guardian, cfg.vault, cfg.hook, cfg.owner, cfg.mode, cfg.hot)
        );
        bytes32 delays = keccak256(
            abi.encode(cfg.shortDelay, cfg.longDelay, cfg.maxFreeze, cfg.freezeCooldown, cfg.scanBlock, cfg.pairsHash)
        );
        bytes32 lists = keccak256(abi.encode(keccak256(abi.encode(cfg.tokens)), keccak256(abi.encode(cfg.known))));
        return keccak256(abi.encode(head, delays, lists));
    }

    function execute(address target, uint256 value, bytes calldata data) external payable {
        if (msg.sender != address(this)) revert DrainErrors.BadCaller();
        _guarded(target, value, data, 0);
    }

    function executeBatch(Call[] calldata calls) external payable {
        if (msg.sender != address(this)) revert DrainErrors.BadCaller();
        for (uint256 i = 0; i < calls.length; i++) {
            _guarded(calls[i].target, calls[i].value, calls[i].data, 0);
        }
    }

    function executeForGrant(address target, uint256 value, bytes calldata data, uint256 grantId) external payable {
        uint256 grant = POLICY.executorGrantOf(address(this), msg.sender);
        if (grantId == 0 || grant == 0 || grant != grantId) revert DrainErrors.BadCaller();
        _guarded(target, value, data, grantId);
    }

    /// @dev Magic only when the digest was registered. The signature bytes are ignored.
    function isValidSignature(bytes32 hash, bytes calldata) external view returns (bytes4) {
        if (POLICY.checkDigest(address(this), hash)) return bytes4(0x1626ba7e);
        return bytes4(0xffffffff);
    }

    function guardianRevoke(
        uint8 kind,
        address token,
        address spender,
        uint48 nonce,
        uint256 wordPos,
        uint256 mask,
        bytes32 digest
    ) external {
        if (msg.sender != address(this) && msg.sender != POLICY.guardianOf(address(this))) revert DrainErrors.NotGuardian();
        (address target, bytes memory data) =
            DrainRevoke.build(POLICY, address(this), kind, token, spender, nonce, wordPos, mask, digest);
        _guarded(target, 0, data, 0);
    }

    /// @dev One tap. Every token is sent only to the vault chosen at init.
    ///      A reverting token does not stop the rest. Revoke of a tracked
    ///      spender is attempted after the transfer. Pre-existing approvals
    ///      are not stopped by the delegate; this is the revoke.
    function evacuate(address[] calldata tokens) external {
        if (msg.sender != address(this) && msg.sender != POLICY.guardianOf(address(this))) revert DrainErrors.NotGuardian();
        if (tokens.length > 32) revert DrainErrors.BadPath();
        address vault = POLICY.vaultOf(address(this));
        uint256 bal = address(this).balance;
        if (bal > 0) {
            (bool ok,) = vault.call{value: bal}("");
            ok;
        }
        address[] memory spenders = POLICY.spendersOf(address(this));
        for (uint256 i = 0; i < tokens.length; i++) {
            if (tokens[i] == address(0)) continue;
            try this.pull(tokens[i], vault) {} catch {}
            for (uint256 j = 0; j < spenders.length; j++) {
                try this.zero(tokens[i], spenders[j]) {} catch {}
            }
        }
        emit Evacuated(vault);
    }

    function pull(address token, address vault) external {
        if (msg.sender != address(this)) revert DrainErrors.BadCaller();
        if (vault != POLICY.vaultOf(address(this))) revert DrainErrors.BadTarget();
        (bool ok, bytes memory ret) = token.staticcall(abi.encodeWithSelector(bytes4(0x70a08231), address(this)));
        if (!ok || ret.length < 32) revert DrainErrors.BadTarget();
        uint256 amount = abi.decode(ret, (uint256));
        if (amount == 0) return;
        (ok, ret) = token.call(abi.encodeWithSelector(DrainTypes.SEL_TRANSFER, vault, amount));
        if (!ok || (ret.length >= 32 && !abi.decode(ret, (bool)))) revert DrainErrors.BadTarget();
    }

    function zero(address token, address spender) external {
        if (msg.sender != address(this)) revert DrainErrors.BadCaller();
        (bool ok, bytes memory ret) = token.call(abi.encodeWithSelector(DrainTypes.SEL_APPROVE, spender, uint256(0)));
        if (!ok || (ret.length >= 32 && !abi.decode(ret, (bool)))) revert DrainErrors.BadTarget();
    }

    function initialized() external view returns (bool) {
        return _store().initialized;
    }

    receive() external payable {}

    function _gate(bytes32 digest, bytes calldata sig) internal {
        AStore storage s = _store();
        if (s.initialized) revert DrainErrors.AlreadyInit();
        if (msg.sender != address(this)) {
            if (sig.length != 65) revert DrainErrors.FrontRun();
            bytes32 ethHash = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", digest));
            bytes32 r = bytes32(sig[:32]);
            bytes32 ss = bytes32(sig[32:64]);
            uint8 v = uint8(sig[64]);
            address rec = ecrecover(ethHash, v, r, ss);
            if (rec != address(this)) revert DrainErrors.FrontRun();
        }
        s.initialized = true;
    }

    function _guarded(address target, uint256 value, bytes memory data, uint256 grantId) internal {
        AStore storage s = _store();
        if (s.lock != 0) revert DrainErrors.Denied(DrainTypes.DCAL);
        s.lock = 1;
        (uint8 verdict, bytes4 reason) = POLICY.check(address(this), target, value, data, grantId);
        if (verdict == 2) revert DrainErrors.Denied(reason);
        if (verdict == 1) revert DrainErrors.Queued(bytes32(0), reason);
        (bool ok, bytes memory ret) = target.call{value: value}(data);
        s.lock = 0;
        if (!ok) {
            assembly {
                revert(add(ret, 32), mload(ret))
            }
        }
    }

    function _store() private pure returns (AStore storage s) {
        bytes32 slot = ACCOUNT_SLOT;
        assembly {
            s.slot := slot
        }
    }
}
