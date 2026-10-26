// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {DrainErrors} from "./DrainErrors.sol";
import {DrainHash} from "./DrainHash.sol";
import {DrainHasher} from "./DrainHasher.sol";
import {DrainTypes} from "./DrainTypes.sol";
import {IDrainPolicy} from "./IDrainPolicy.sol";

/// @title DrainPolicy
/// @notice One shared Claw Firewall policy per chain. Every account stores
///         its rules here, keyed by the account. A second account does not
///         deploy a second module.
/// @dev Clean-room. Not derived from Zodiac Roles (BUSL-1.1). There is no
///      admin key and no RUNECLAW address. Digests are built from the permit
///      preimage; nothing accepts a caller-supplied hash plus a spender.
///      `isValidSignature` lives on the account and is view, so a registration
///      cannot be cleared there. Revoke and the vault exit stay available
///      while a freeze is on. Outflows fail closed.
contract DrainPolicy is IDrainPolicy {
    /// @dev ERC-7201: keccak256(abi.encode(uint256(keccak256("runeclaw.drainfw.v1")) - 1)) & ~bytes32(uint256(0xff))
    bytes32 public constant POLICY_SLOT = 0x4e0b939384c18a59f03c163642d25b17c6e2ba27f509b7f1e60d5cd9bf301500;

    address public immutable PERMIT2;
    DrainHasher public immutable HASHER;

    struct Account {
        bool installed;
        bool hookBound;
        bool live;
        address guardian;
        address vault;
        address owner;
        address hook;
        uint64 shortDelay;
        uint64 longDelay;
        uint64 maxFreeze;
        uint64 freezeCooldown;
        uint64 frozenUntil;
        uint64 lastFreezeAt;
        uint64 policyNonce;
        uint64 queueNonce;
        uint64 scanBlock;
        uint128 hot;
        uint8 mode;
        uint8 openOps;
        bytes32 pairsHash;
    }

    struct TokenCfg {
        bool tracked;
        uint128 cap;
        uint128 used;
        uint64 windowStart;
        uint64 windowLen;
    }

    struct Op {
        address account;
        address target;
        uint256 value;
        uint8 kind;
        uint8 state;
        uint8 path;
        bool extended;
        uint64 eta;
        uint64 expiry;
        uint64 policyNonce;
        bytes32 payloadHash;
        bytes data;
    }

    struct TokenInit {
        address token;
        uint128 cap;
        uint64 windowLen;
    }

    struct KnownInit {
        address who;
        uint8 role;
        uint256 grantId;
    }

    struct Init {
        address guardian;
        address vault;
        address hook;
        address owner;
        uint64 shortDelay;
        uint64 longDelay;
        uint64 maxFreeze;
        uint64 freezeCooldown;
        uint8 mode;
        uint128 hot;
        uint64 scanBlock;
        bytes32 pairsHash;
        TokenInit[] tokens;
        KnownInit[] known;
    }

    struct Store {
        mapping(address => Account) accounts;
        mapping(address => mapping(address => TokenCfg)) tokens;
        mapping(address => mapping(address => uint8)) roles;
        mapping(address => mapping(bytes32 => uint64)) digestExpiry;
        mapping(address => mapping(uint8 => bytes32)) slots;
        mapping(bytes32 => Op) ops;
        mapping(address => mapping(address => uint256)) executorGrant;
        mapping(address => address[]) spenders;
    }

    event InstallAttested(address indexed account, uint64 scanBlock, bytes32 pairsHash);
    event Queued(address indexed account, bytes32 indexed opId, uint8 kind, uint64 eta);
    event Executed(address indexed account, bytes32 indexed opId);
    event Cancelled(address indexed account, bytes32 indexed opId);
    event Extended(address indexed account, bytes32 indexed opId, uint64 eta);
    event Frozen(address indexed account, uint64 until);
    event Unfrozen(address indexed account);
    event DigestRegistered(address indexed account, bytes32 indexed digest, uint64 expiry);

    constructor(address permit2, DrainHasher hasher) {
        if (DrainTypes.mainnet(block.chainid)) revert DrainErrors.Mainnet();
        if (permit2 == address(0) || address(hasher) == address(0)) revert DrainErrors.BadTarget();
        PERMIT2 = permit2;
        HASHER = hasher;
    }

    function initAccount(Init calldata cfg) external {
        Store storage s = _s();
        Account storage a = s.accounts[msg.sender];
        if (a.installed) revert DrainErrors.AlreadyInit();
        if (cfg.guardian == address(0) || cfg.vault == address(0)) revert DrainErrors.BadTarget();
        if (cfg.tokens.length > 32 || cfg.known.length > 32) revert DrainErrors.BadPath();
        if (cfg.mode > DrainTypes.MODE_STRICT) revert DrainErrors.BadPath();
        a.installed = true;
        a.live = true;
        a.guardian = cfg.guardian;
        a.vault = cfg.vault;
        a.owner = cfg.owner == address(0) ? msg.sender : cfg.owner;
        a.hook = cfg.hook;
        a.shortDelay = _orMin(cfg.shortDelay, DrainTypes.MIN_SHORT);
        a.longDelay = _orMin(cfg.longDelay, DrainTypes.MIN_LONG);
        a.maxFreeze = _orFreeze(cfg.maxFreeze);
        a.freezeCooldown = _orMin(cfg.freezeCooldown, DrainTypes.MIN_COOLDOWN);
        a.mode = cfg.mode;
        a.hot = cfg.hot;
        a.policyNonce = 1;
        a.scanBlock = cfg.scanBlock;
        a.pairsHash = cfg.pairsHash;
        for (uint256 i = 0; i < cfg.tokens.length; i++) {
            TokenCfg storage t = s.tokens[msg.sender][cfg.tokens[i].token];
            t.tracked = true;
            t.cap = cfg.tokens[i].cap;
            t.windowLen = cfg.tokens[i].windowLen;
        }
        for (uint256 i = 0; i < cfg.known.length; i++) {
            address who = cfg.known[i].who;
            uint8 role = cfg.known[i].role;
            if (who == address(0) || role == 0) revert DrainErrors.BadTarget();
            s.roles[msg.sender][who] |= role;
            if ((role & DrainTypes.ROLE_SPENDER) != 0) _noteSpender(msg.sender, who);
            if ((role & DrainTypes.ROLE_EXECUTOR) != 0) {
                if (cfg.known[i].grantId == 0) revert DrainErrors.BadTarget();
                s.executorGrant[msg.sender][who] = cfg.known[i].grantId;
            }
        }
        emit InstallAttested(msg.sender, cfg.scanBlock, cfg.pairsHash);
    }

    function bindHook(address account) external {
        Account storage a = _s().accounts[account];
        if (!a.installed) revert DrainErrors.NotInstalled();
        if (a.hook == address(0) || msg.sender != a.hook) revert DrainErrors.BadCaller();
        a.hookBound = true;
    }

    function consumeUninstall(address account) external {
        Account storage a = _s().accounts[account];
        if (!a.installed || msg.sender != a.hook || !a.hookBound) revert DrainErrors.BadCaller();
        bytes32 id = _findReady(account, DrainTypes.KIND_UNINSTALL, bytes32(0), false);
        if (id == bytes32(0)) revert DrainErrors.NotReady();
        _consumeOp(id);
        a.hook = address(0);
        a.hookBound = false;
        a.live = false;
    }

    function check(address account, address target, uint256 value, bytes calldata data, uint256 grantId)
        external
        returns (uint8 verdict, bytes4 reason)
    {
        Account storage a = _s().accounts[account];
        if (!a.installed) revert DrainErrors.NotInstalled();
        if (msg.sender != account && !(msg.sender == a.hook && a.hookBound)) revert DrainErrors.BadCaller();
        if (!a.live) return _retired(account, target, value, data, grantId);
        if (grantId == 0 && _takeReady(account, target, value, data)) return (0, bytes4(0));
        return _price(account, target, value, data, grantId);
    }

    struct Price {
        uint8 vCall;
        bytes4 rCall;
        bool spendTok;
        address token;
        uint256 amt;
    }

    function _price(address account, address target, uint256 value, bytes calldata data, uint256 grantId)
        internal
        returns (uint8, bytes4)
    {
        Price memory p;
        (p.vCall, p.rCall, p.spendTok, p.token, p.amt) = _classify(account, target, data, grantId);
        (uint8 vNat, bytes4 rNat, bool spendNat) = _native(account, target, value, grantId);
        (uint8 verdict, bytes4 reason) = _merge(vNat, rNat, p.vCall, p.rCall);
        if (grantId != 0 && verdict == 1) verdict = 2;
        if (verdict != 0) return (verdict, reason);
        return _bill(account, value, grantId, spendNat, p);
    }

    function _bill(address account, uint256 value, uint256 grantId, bool spendNat, Price memory p)
        internal
        returns (uint8, bytes4)
    {
        if (grantId != 0) {
            uint256 cost = value;
            if (p.spendTok) cost += p.amt;
            uint128 hot = _s().accounts[account].hot;
            if (cost > hot) return (2, DrainTypes.HOTB);
            if (cost > 0) _s().accounts[account].hot = hot - uint128(cost);
        }
        if (spendNat) _consume(account, address(0), value);
        if (p.spendTok) _consume(account, p.token, p.amt);
        return (0, bytes4(0));
    }

    function queueOutflow(address target, uint256 value, bytes calldata data) external returns (bytes32) {
        _auth();
        uint8 path = target == msg.sender ? DrainTypes.PATH_LONG : DrainTypes.PATH_SHORT;
        return _enqueue(DrainTypes.KIND_OUTFLOW, path, target, value, data);
    }

    function queueRecipient(address who) external returns (bytes32) {
        if (who == address(0)) revert DrainErrors.BadTarget();
        _auth();
        return _enqueue(DrainTypes.KIND_RECIPIENT, DrainTypes.PATH_SHORT, who, 0, "");
    }

    function queueSpender(address who) external returns (bytes32) {
        if (who == address(0)) revert DrainErrors.BadTarget();
        _auth();
        return _enqueue(DrainTypes.KIND_SPENDER, DrainTypes.PATH_SHORT, who, 0, "");
    }

    function queueExecutor(address who, uint256 grantId) external returns (bytes32) {
        if (who == address(0) || grantId == 0) revert DrainErrors.BadTarget();
        _auth();
        return _enqueue(DrainTypes.KIND_EXECUTOR, DrainTypes.PATH_LONG, who, grantId, "");
    }

    function queueGuardian(address who) external returns (bytes32) {
        if (who == address(0)) revert DrainErrors.BadTarget();
        _auth();
        return _enqueue(DrainTypes.KIND_GUARDIAN, DrainTypes.PATH_LONG, who, 0, "");
    }

    function queueVault(address who) external returns (bytes32) {
        if (who == address(0)) revert DrainErrors.BadTarget();
        _auth();
        return _enqueue(DrainTypes.KIND_VAULT, DrainTypes.PATH_LONG, who, 0, "");
    }

    function queueUninstall() external returns (bytes32) {
        _auth();
        return _enqueue(DrainTypes.KIND_UNINSTALL, DrainTypes.PATH_LONG, address(0), 0, "");
    }

    function queueUnfreeze() external returns (bytes32) {
        _auth();
        return _enqueue(DrainTypes.KIND_POLICY, DrainTypes.PATH_LONG, address(0), 0, abi.encode(uint8(3)));
    }

    function executeOp(bytes32 opId) external {
        Op storage o = _s().ops[opId];
        if (o.account != msg.sender || o.state != DrainTypes.ST_QUEUED) revert DrainErrors.OpMissing();
        if (o.kind == DrainTypes.KIND_OUTFLOW) revert DrainErrors.BadPath();
        _requireReady(o);
        _consumeOp(opId);
        _apply(o);
    }

    function cancel(bytes32 opId) external {
        Op storage o = _s().ops[opId];
        if (o.state != DrainTypes.ST_QUEUED) revert DrainErrors.OpMissing();
        Account storage a = _s().accounts[o.account];
        bool owner = msg.sender == o.account;
        bool shortGuard = msg.sender == a.guardian && o.path == DrainTypes.PATH_SHORT;
        if (!owner && !shortGuard) revert DrainErrors.NotGuardian();
        o.state = DrainTypes.ST_CANCELLED;
        a.openOps -= 1;
        emit Cancelled(o.account, opId);
    }

    function extendOnce(bytes32 opId) external {
        Op storage o = _s().ops[opId];
        if (o.state != DrainTypes.ST_QUEUED) revert DrainErrors.OpMissing();
        Account storage a = _s().accounts[o.account];
        if (msg.sender != a.guardian) revert DrainErrors.NotGuardian();
        if (o.extended) revert DrainErrors.BadPath();
        if (block.timestamp > o.expiry) revert DrainErrors.NotReady();
        o.extended = true;
        o.eta += a.shortDelay;
        uint64 floorEta = o.eta + uint64(DrainTypes.EXEC_WINDOW);
        if (floorEta > o.expiry) o.expiry = floorEta;
        emit Extended(o.account, opId, o.eta);
    }

    function freeze(address account, uint64 duration) external {
        Account storage a = _s().accounts[account];
        if (!a.installed || !a.live) revert DrainErrors.NotInstalled();
        if (msg.sender != a.guardian) revert DrainErrors.NotGuardian();
        if (duration < 1 hours || duration > a.maxFreeze) revert DrainErrors.BadPath();
        if (a.lastFreezeAt != 0 && block.timestamp < uint256(a.lastFreezeAt) + a.freezeCooldown) {
            revert DrainErrors.Cooldown();
        }
        a.lastFreezeAt = uint64(block.timestamp);
        a.frozenUntil = uint64(block.timestamp + duration);
        emit Frozen(account, a.frozenUntil);
    }

    function unfreeze(address account) external {
        Account storage a = _s().accounts[account];
        if (!a.installed || !a.live) revert DrainErrors.NotInstalled();
        if (msg.sender != a.guardian) revert DrainErrors.NotGuardian();
        a.frozenUntil = 0;
        emit Unfrozen(account);
    }

    function setCap(address token, uint128 newCap, uint64 windowLen) external {
        _auth();
        if (windowLen == 0) revert DrainErrors.BadPath();
        TokenCfg storage t = _s().tokens[msg.sender][token];
        bool loosen = !t.tracked || newCap > t.cap || windowLen > t.windowLen;
        if (loosen) {
            _enqueue(
                DrainTypes.KIND_POLICY,
                DrainTypes.PATH_LONG,
                address(0),
                0,
                abi.encode(uint8(1), token, newCap, windowLen)
            );
            return;
        }
        _applyCap(msg.sender, token, newCap, windowLen);
        _bump(msg.sender);
    }

    function setDelays(uint64 newShort, uint64 newLong) external returns (bytes32 opId) {
        Account storage a = _auth();
        uint64 sDelay = _orMin(newShort, DrainTypes.MIN_SHORT);
        uint64 lDelay = _orMin(newLong, DrainTypes.MIN_LONG);
        if (sDelay < a.shortDelay || lDelay < a.longDelay) {
            return _enqueue(
                DrainTypes.KIND_POLICY, DrainTypes.PATH_LONG, address(0), 0, abi.encode(uint8(2), sDelay, lDelay)
            );
        }
        a.shortDelay = sDelay;
        a.longDelay = lDelay;
        _bump(msg.sender);
    }

    function setMaxFreeze(uint64 next) external {
        Account storage a = _auth();
        uint64 n = _orFreeze(next);
        if (n > a.maxFreeze) {
            _enqueue(DrainTypes.KIND_POLICY, DrainTypes.PATH_LONG, address(0), 0, abi.encode(uint8(4), n));
            return;
        }
        a.maxFreeze = n;
        _bump(msg.sender);
    }

    function setCooldown(uint64 next) external {
        Account storage a = _auth();
        uint64 n = _orMin(next, DrainTypes.MIN_COOLDOWN);
        if (n < a.freezeCooldown) {
            _enqueue(DrainTypes.KIND_POLICY, DrainTypes.PATH_LONG, address(0), 0, abi.encode(uint8(5), n));
            return;
        }
        a.freezeCooldown = n;
        _bump(msg.sender);
    }

    function setHot(uint128 next) external {
        Account storage a = _auth();
        if (next > a.hot) {
            _enqueue(DrainTypes.KIND_HOT, DrainTypes.PATH_LONG, address(0), next, "");
            return;
        }
        a.hot = next;
        _bump(msg.sender);
    }

    function setMode(uint8 mode) external returns (bytes32 opId) {
        Account storage a = _auth();
        if (mode > DrainTypes.MODE_STRICT) revert DrainErrors.BadPath();
        if (_rank(mode) < _rank(a.mode)) {
            return _enqueue(DrainTypes.KIND_MODE, DrainTypes.PATH_LONG, address(0), mode, "");
        }
        a.mode = mode;
        _bump(msg.sender);
    }

    function removeRecipient(address who) external {
        _auth();
        _s().roles[msg.sender][who] &= ~DrainTypes.ROLE_RECIPIENT;
        _bump(msg.sender);
    }

    function removeSpender(address who) external {
        _auth();
        _s().roles[msg.sender][who] &= ~DrainTypes.ROLE_SPENDER;
        _bump(msg.sender);
    }

    function removeExecutor(address who) external {
        _auth();
        _s().roles[msg.sender][who] &= ~DrainTypes.ROLE_EXECUTOR;
        _s().executorGrant[msg.sender][who] = 0;
        _bump(msg.sender);
    }

    function track(address token) external {
        _auth();
        _s().tokens[msg.sender][token].tracked = true;
    }

    /// @dev The shared hasher calls these. The account is the hasher's caller.
    function reserveFor(address account, address token, uint256 amount, uint256 deadline, address spender)
        external
        returns (uint64)
    {
        if (msg.sender != address(HASHER)) revert DrainErrors.BadCaller();
        Account storage a = _s().accounts[account];
        if (!a.installed || !a.live) revert DrainErrors.NotInstalled();
        if (spender != address(0)) _knownSpender(account, spender);
        return _reserve(account, token, amount, deadline);
    }

    function keepFor(address account, bytes32 digest, uint64 expiry) external {
        if (msg.sender != address(HASHER)) revert DrainErrors.BadCaller();
        if (!_s().accounts[account].installed) revert DrainErrors.NotInstalled();
        _keep(account, digest, expiry);
    }

    function isHasherAdmin(bytes4 sel) external pure returns (bool) {
        return _hasherAdmin(sel);
    }

    function clearDigest(address account, bytes32 digest) external {
        Account storage a = _s().accounts[account];
        if (!a.installed) revert DrainErrors.NotInstalled();
        if (msg.sender != account && msg.sender != a.guardian) revert DrainErrors.NotGuardian();
        _s().digestExpiry[account][digest] = 0;
    }

    function checkDigest(address account, bytes32 digest) external view returns (bool) {
        Account storage a = _s().accounts[account];
        if (!a.installed || !a.live) return false;
        uint64 exp = _s().digestExpiry[account][digest];
        return exp != 0 && block.timestamp < exp;
    }

    function isPolicyAdmin(bytes4 sel) external pure returns (bool) {
        return _admin(sel);
    }

    function installed(address account) external view returns (bool) {
        return _s().accounts[account].installed;
    }

    function guardianOf(address account) external view returns (address) {
        return _s().accounts[account].guardian;
    }

    function vaultOf(address account) external view returns (address) {
        return _s().accounts[account].vault;
    }

    function ownerOf(address account) external view returns (address) {
        return _s().accounts[account].owner;
    }

    function hotOf(address account) external view returns (uint128) {
        return _s().accounts[account].hot;
    }

    function hookOf(address account) external view returns (address) {
        return _s().accounts[account].hook;
    }

    function hookBound(address account) external view returns (bool) {
        return _s().accounts[account].hookBound;
    }

    function modeOf(address account) external view returns (uint8) {
        return _s().accounts[account].mode;
    }

    function policyNonceOf(address account) external view returns (uint64) {
        return _s().accounts[account].policyNonce;
    }

    function openOpsOf(address account) external view returns (uint8) {
        return _s().accounts[account].openOps;
    }

    function frozenUntilOf(address account) external view returns (uint64) {
        return _s().accounts[account].frozenUntil;
    }

    function lastFreezeAtOf(address account) external view returns (uint64) {
        return _s().accounts[account].lastFreezeAt;
    }

    function delaysOf(address account) external view returns (uint64, uint64, uint64, uint64) {
        Account storage a = _s().accounts[account];
        return (a.shortDelay, a.longDelay, a.maxFreeze, a.freezeCooldown);
    }

    function roleOf(address account, address who) external view returns (uint8) {
        return _s().roles[account][who];
    }

    function executorGrantOf(address account, address executor) external view returns (uint256) {
        return _s().executorGrant[account][executor];
    }

    function tokenOf(address account, address token) external view returns (uint128, uint128, uint64, uint64, bool) {
        TokenCfg storage t = _s().tokens[account][token];
        return (t.cap, t.used, t.windowStart, t.windowLen, t.tracked);
    }

    function digestExpiryOf(address account, bytes32 digest) external view returns (uint64) {
        return _s().digestExpiry[account][digest];
    }

    function spendersOf(address account) external view returns (address[] memory) {
        return _s().spenders[account];
    }

    function opOf(bytes32 opId)
        external
        view
        returns (uint8 kind, uint8 state, uint8 path, uint64 eta, uint64 expiry, uint64 nonce, bool extended, address target)
    {
        Op storage o = _s().ops[opId];
        return (o.kind, o.state, o.path, o.eta, o.expiry, o.policyNonce, o.extended, o.target);
    }

    function pairsOf(address account) external view returns (uint64 scanBlock, bytes32 pairsHash) {
        Account storage a = _s().accounts[account];
        return (a.scanBlock, a.pairsHash);
    }

    function _classify(address account, address target, bytes calldata data, uint256 grantId)
        internal
        view
        returns (uint8 verdict, bytes4 reason, bool spend, address token, uint256 amount)
    {
        if (data.length < 4 && data.length != 0) return (2, DrainTypes.UNKN, false, address(0), 0);
        if (data.length < 4) return (0, bytes4(0), false, address(0), 0);
        bytes4 sel = bytes4(data[:4]);
        if (_isRevoke(sel, data)) return (0, bytes4(0), false, address(0), 0);
        if (target == address(HASHER) && _hasherAdmin(sel)) {
            if (grantId != 0) return (2, DrainTypes.AGNT, false, address(0), 0);
            return (0, bytes4(0), false, address(0), 0);
        }
        if (target == address(this) && _admin(sel)) {
            if (grantId != 0) return (2, DrainTypes.AGNT, false, address(0), 0);
            return (0, bytes4(0), false, address(0), 0);
        }
        if (target == account) {
            if (_frozen(account)) return (2, DrainTypes.FRZN, false, address(0), 0);
            if (grantId != 0) return (2, DrainTypes.SELF, false, address(0), 0);
            return (1, DrainTypes.SELF, false, address(0), 0);
        }
        if (sel == DrainTypes.SEL_TRANSFER) {
            if (data.length < 68) return (2, DrainTypes.UNKN, false, address(0), 0);
            address to = _addr(data, 4);
            uint256 amt = uint256(bytes32(data[36:68]));
            (verdict, reason, spend) = _xfer(account, target, to, amt, grantId);
            return (verdict, reason, spend, target, amt);
        }
        if (sel == DrainTypes.SEL_TRANSFER_FROM) {
            if (data.length < 100) return (2, DrainTypes.UNKN, false, address(0), 0);
            if (_addr(data, 4) != account) return (2, DrainTypes.UNKN, false, address(0), 0);
            address to = _addr(data, 36);
            uint256 amt = uint256(bytes32(data[68:100]));
            (verdict, reason, spend) = _xfer(account, target, to, amt, grantId);
            return (verdict, reason, spend, target, amt);
        }
        if (sel == DrainTypes.SEL_APPROVE || sel == DrainTypes.SEL_PERMIT2_APPROVE) {
            return _approval(account, target, sel, data, grantId);
        }
        if (sel == DrainTypes.SEL_INCREASE) return (2, DrainTypes.OCAP, false, address(0), 0);
        if (sel == DrainTypes.SEL_FOR_ALL) return _forAll(account, data, grantId);
        if (
            sel == DrainTypes.SEL_SAFE_721 || sel == DrainTypes.SEL_SAFE_721_DATA || sel == DrainTypes.SEL_SAFE_1155
                || sel == DrainTypes.SEL_SAFE_1155_BATCH
        ) {
            return _nft(account, data, grantId);
        }
        if (_frozen(account)) return (2, DrainTypes.FRZN, false, address(0), 0);
        if (grantId != 0 || _s().accounts[account].mode != DrainTypes.MODE_QUEUE) {
            return (2, DrainTypes.UNKN, false, address(0), 0);
        }
        return (1, DrainTypes.UNKN, false, address(0), 0);
    }

    function _approval(address account, address, bytes4 sel, bytes calldata data, uint256 grantId)
        internal
        view
        returns (uint8, bytes4, bool, address, uint256)
    {
        if (_frozen(account)) return (2, DrainTypes.FRZN, false, address(0), 0);
        if (sel == DrainTypes.SEL_APPROVE) {
            if (data.length < 68) return (2, DrainTypes.UNKN, false, address(0), 0);
            uint256 amt = uint256(bytes32(data[36:68]));
            if (amt >= DrainTypes.APPROVE_MAX_LINE) return (2, DrainTypes.NCAP, false, address(0), 0);
            address spender = _addr(data, 4);
            if (_has(account, spender, DrainTypes.ROLE_SPENDER)) return (0, bytes4(0), false, address(0), 0);
            if (grantId != 0) return (2, DrainTypes.SPND, false, address(0), 0);
            return (1, DrainTypes.SPND, false, address(0), 0);
        }
        if (data.length < 132) return (2, DrainTypes.UNKN, false, address(0), 0);
        uint256 expiration = uint256(bytes32(data[100:132]));
        if (expiration > block.timestamp + DrainTypes.MAX_PERMIT_LIFE) return (2, DrainTypes.EXPY, false, address(0), 0);
        address spender2 = _addr(data, 36);
        if (_has(account, spender2, DrainTypes.ROLE_SPENDER)) return (0, bytes4(0), false, address(0), 0);
        if (grantId != 0) return (2, DrainTypes.SPND, false, address(0), 0);
        return (1, DrainTypes.SPND, false, address(0), 0);
    }

    function _forAll(address account, bytes calldata data, uint256 grantId)
        internal
        view
        returns (uint8, bytes4, bool, address, uint256)
    {
        if (data.length < 68) return (2, DrainTypes.UNKN, false, address(0), 0);
        if (_frozen(account)) return (2, DrainTypes.FRZN, false, address(0), 0);
        address operator = _addr(data, 4);
        if (_has(account, operator, DrainTypes.ROLE_SPENDER)) return (0, bytes4(0), false, address(0), 0);
        if (grantId != 0) return (2, DrainTypes.SPND, false, address(0), 0);
        return (1, DrainTypes.SPND, false, address(0), 0);
    }

    function _nft(address account, bytes calldata data, uint256 grantId)
        internal
        view
        returns (uint8, bytes4, bool, address, uint256)
    {
        if (data.length < 68) return (2, DrainTypes.UNKN, false, address(0), 0);
        address to = _addr(data, 36);
        if (to == _s().accounts[account].vault && grantId == 0) return (0, bytes4(0), false, address(0), 0);
        if (_frozen(account)) return (2, DrainTypes.FRZN, false, address(0), 0);
        if (grantId != 0) return (2, DrainTypes.RCPT, false, address(0), 0);
        return (1, DrainTypes.RCPT, false, address(0), 0);
    }

    function _xfer(address account, address token, address to, uint256 amount, uint256 grantId)
        internal
        view
        returns (uint8, bytes4, bool)
    {
        token;
        if (to == _s().accounts[account].vault && grantId == 0) return (0, bytes4(0), false);
        if (_frozen(account)) return (2, DrainTypes.FRZN, false);
        if (!_isRecipient(account, to)) {
            if (grantId != 0) return (2, DrainTypes.RCPT, false);
            return (1, DrainTypes.RCPT, false);
        }
        if (amount == 0) return (0, bytes4(0), false);
        if (amount > _preview(account, token)) return (2, DrainTypes.OCAP, false);
        return (0, bytes4(0), true);
    }

    function _native(address account, address target, uint256 value, uint256 grantId)
        internal
        view
        returns (uint8, bytes4, bool)
    {
        if (value == 0) return (0, bytes4(0), false);
        if (target == _s().accounts[account].vault && grantId == 0) return (0, bytes4(0), false);
        if (_frozen(account)) return (2, DrainTypes.FRZN, false);
        if (!_isRecipient(account, target)) {
            if (grantId != 0) return (2, DrainTypes.RCPT, false);
            return (1, DrainTypes.RCPT, false);
        }
        if (value > _preview(account, address(0))) return (2, DrainTypes.OCAP, false);
        return (0, bytes4(0), true);
    }

    function _takeReady(address account, address target, uint256 value, bytes calldata data) internal returns (bool) {
        if (_frozen(account)) return false;
        bytes32 ph = keccak256(abi.encode(target, value, keccak256(data)));
        bytes32 id = _findReady(account, DrainTypes.KIND_OUTFLOW, ph, true);
        if (id == bytes32(0)) return false;
        _consumeOp(id);
        _markPayee(account, target, data);
        return true;
    }

    function _findReady(address account, uint8 kind, bytes32 ph, bool matchHash) internal view returns (bytes32) {
        Store storage s = _s();
        Account storage a = s.accounts[account];
        for (uint8 i = 0; i < DrainTypes.QUEUE_CAP; i++) {
            bytes32 id = s.slots[account][i];
            if (id == bytes32(0)) continue;
            Op storage o = s.ops[id];
            if (o.state != DrainTypes.ST_QUEUED || o.kind != kind || o.account != account) continue;
            if (o.policyNonce != a.policyNonce) continue;
            if (block.timestamp < o.eta || block.timestamp > o.expiry) continue;
            if (matchHash && o.payloadHash != ph) continue;
            return id;
        }
        return bytes32(0);
    }

    function _markPayee(address account, address target, bytes calldata data) internal {
        if (data.length >= 68 && bytes4(data[:4]) == DrainTypes.SEL_TRANSFER) {
            _s().roles[account][_addr(data, 4)] |= DrainTypes.ROLE_RECIPIENT;
            return;
        }
        if (data.length >= 100 && bytes4(data[:4]) == DrainTypes.SEL_TRANSFER_FROM) {
            _s().roles[account][_addr(data, 36)] |= DrainTypes.ROLE_RECIPIENT;
            return;
        }
        if (target != address(0)) _s().roles[account][target] |= DrainTypes.ROLE_RECIPIENT;
    }

    function _enqueue(uint8 kind, uint8 path, address target, uint256 value, bytes memory data)
        internal
        returns (bytes32 opId)
    {
        Store storage s = _s();
        Account storage a = s.accounts[msg.sender];
        uint8 slot = _alloc(msg.sender);
        a.queueNonce += 1;
        bytes32 ph = keccak256(abi.encode(target, value, keccak256(data)));
        opId = keccak256(abi.encode(block.chainid, msg.sender, a.queueNonce, kind, ph));
        uint64 delay = path == DrainTypes.PATH_LONG ? a.longDelay : a.shortDelay;
        uint64 eta = uint64(block.timestamp + delay);
        s.slots[msg.sender][slot] = opId;
        s.ops[opId] = Op({
            account: msg.sender,
            target: target,
            value: value,
            kind: kind,
            state: DrainTypes.ST_QUEUED,
            path: path,
            extended: false,
            eta: eta,
            expiry: eta + uint64(DrainTypes.EXEC_WINDOW),
            policyNonce: a.policyNonce,
            payloadHash: ph,
            data: data
        });
        a.openOps += 1;
        emit Queued(msg.sender, opId, kind, eta);
    }

    function _alloc(address account) internal returns (uint8) {
        Store storage s = _s();
        Account storage a = s.accounts[account];
        for (uint8 i = 0; i < DrainTypes.QUEUE_CAP; i++) {
            bytes32 id = s.slots[account][i];
            if (id == bytes32(0)) return i;
            Op storage o = s.ops[id];
            if (o.state != DrainTypes.ST_QUEUED || block.timestamp > o.expiry) {
                if (o.state == DrainTypes.ST_QUEUED) {
                    o.state = DrainTypes.ST_CANCELLED;
                    a.openOps -= 1;
                }
                return i;
            }
        }
        revert DrainErrors.QueueFull();
    }

    function _requireReady(Op storage o) internal view {
        Account storage a = _s().accounts[o.account];
        if (o.policyNonce != a.policyNonce) revert DrainErrors.NotReady();
        if (block.timestamp < o.eta || block.timestamp > o.expiry) revert DrainErrors.NotReady();
        if (block.timestamp < a.frozenUntil) revert DrainErrors.NotReady();
    }

    function _consumeOp(bytes32 opId) internal {
        Op storage o = _s().ops[opId];
        Account storage a = _s().accounts[o.account];
        o.state = DrainTypes.ST_EXECUTED;
        a.openOps -= 1;
        emit Executed(o.account, opId);
    }

    function _apply(Op storage o) internal {
        Account storage a = _s().accounts[o.account];
        if (o.kind == DrainTypes.KIND_RECIPIENT) {
            _s().roles[o.account][o.target] |= DrainTypes.ROLE_RECIPIENT;
        } else if (o.kind == DrainTypes.KIND_SPENDER) {
            _s().roles[o.account][o.target] |= DrainTypes.ROLE_SPENDER;
            _noteSpender(o.account, o.target);
        } else if (o.kind == DrainTypes.KIND_EXECUTOR) {
            _s().roles[o.account][o.target] |= DrainTypes.ROLE_EXECUTOR;
            _s().executorGrant[o.account][o.target] = o.value;
        } else if (o.kind == DrainTypes.KIND_GUARDIAN) {
            a.guardian = o.target;
        } else if (o.kind == DrainTypes.KIND_VAULT) {
            a.vault = o.target;
        } else if (o.kind == DrainTypes.KIND_HOT) {
            a.hot = uint128(o.value);
        } else if (o.kind == DrainTypes.KIND_MODE) {
            a.mode = uint8(o.value);
        } else if (o.kind == DrainTypes.KIND_UNINSTALL) {
            a.live = false;
            a.hookBound = false;
        } else if (o.kind == DrainTypes.KIND_POLICY) {
            _applyPolicy(o.account, o.data);
        } else {
            revert DrainErrors.BadPath();
        }
    }

    function _applyPolicy(address account, bytes memory data) internal {
        uint256 tag = _word(data, 0);
        if (tag == 1) {
            _applyCap(account, address(uint160(_word(data, 1))), uint128(_word(data, 2)), uint64(_word(data, 3)));
        } else if (tag == 2) {
            Account storage a = _s().accounts[account];
            a.shortDelay = uint64(_word(data, 1));
            a.longDelay = uint64(_word(data, 2));
        } else if (tag == 3) {
            _s().accounts[account].frozenUntil = 0;
            emit Unfrozen(account);
        } else if (tag == 4) {
            _s().accounts[account].maxFreeze = uint64(_word(data, 1));
        } else if (tag == 5) {
            _s().accounts[account].freezeCooldown = uint64(_word(data, 1));
        } else {
            revert DrainErrors.BadPath();
        }
    }

    function _applyCap(address account, address token, uint128 newCap, uint64 windowLen) internal {
        TokenCfg storage t = _s().tokens[account][token];
        t.tracked = true;
        t.cap = newCap;
        t.windowLen = windowLen;
        if (t.used > newCap) t.used = newCap;
    }

    function _reserve(address account, address token, uint256 amount, uint256 deadline) internal returns (uint64) {
        if (amount == 0) revert DrainErrors.BadTarget();
        if (deadline <= block.timestamp) revert DrainErrors.OverCap(DrainTypes.EXPY);
        if (amount > _preview(account, token)) revert DrainErrors.OverCap(DrainTypes.OCAP);
        _consume(account, token, amount);
        return _bound(account, token, deadline);
    }

    function _bound(address account, address token, uint256 deadline) internal view returns (uint64) {
        TokenCfg storage t = _s().tokens[account][token];
        uint256 windowEnd = uint256(t.windowStart) + uint256(t.windowLen);
        uint256 bound = deadline < windowEnd ? deadline : windowEnd;
        return uint64(bound);
    }

    function _keep(address account, bytes32 digest, uint64 expiry) internal {
        if (digest == bytes32(0) || expiry == 0) revert DrainErrors.BadTarget();
        _s().digestExpiry[account][digest] = expiry;
        emit DigestRegistered(account, digest, expiry);
    }

    function _knownSpender(address account, address spender) internal view {
        if (!_has(account, spender, DrainTypes.ROLE_SPENDER)) revert DrainErrors.NeedsQueue(DrainTypes.SPND);
    }

    function _preview(address account, address token) internal view returns (uint256) {
        TokenCfg storage t = _s().tokens[account][token];
        if (!t.tracked || t.cap == 0 || t.windowLen == 0) return 0;
        if (t.windowStart == 0 || block.timestamp >= uint256(t.windowStart) + t.windowLen) return t.cap;
        if (t.used >= t.cap) return 0;
        return uint256(t.cap - t.used);
    }

    function _consume(address account, address token, uint256 amount) internal {
        if (amount == 0) return;
        TokenCfg storage t = _s().tokens[account][token];
        if (t.windowStart == 0 || block.timestamp >= uint256(t.windowStart) + t.windowLen) {
            t.windowStart = uint64(block.timestamp);
            t.used = 0;
        }
        t.used += uint128(amount);
    }

    function _noteSpender(address account, address who) internal {
        address[] storage list = _s().spenders[account];
        uint256 n = list.length;
        for (uint256 i = 0; i < n; i++) {
            if (list[i] == who) return;
        }
        if (n >= 16) return;
        list.push(who);
    }

    function _auth() internal view returns (Account storage a) {
        a = _s().accounts[msg.sender];
        if (!a.installed || !a.live) revert DrainErrors.NotInstalled();
    }

    function _retired(address account, address target, uint256 value, bytes calldata data, uint256 grantId)
        internal
        view
        returns (uint8, bytes4)
    {
        if (grantId != 0) return (2, DrainTypes.UNKN);
        if (data.length >= 4 && _isRevoke(bytes4(data[:4]), data)) return (0, bytes4(0));
        address vault = _s().accounts[account].vault;
        if (value > 0 && target == vault && data.length == 0) return (0, bytes4(0));
        if (data.length >= 68 && bytes4(data[:4]) == DrainTypes.SEL_TRANSFER && _addr(data, 4) == vault) {
            return (0, bytes4(0));
        }
        if (data.length >= 100 && bytes4(data[:4]) == DrainTypes.SEL_TRANSFER_FROM && _addr(data, 36) == vault) {
            return (0, bytes4(0));
        }
        if (data.length >= 68) {
            bytes4 sel = bytes4(data[:4]);
            if (
                (sel == DrainTypes.SEL_SAFE_721 || sel == DrainTypes.SEL_SAFE_721_DATA || sel == DrainTypes.SEL_SAFE_1155
                    || sel == DrainTypes.SEL_SAFE_1155_BATCH) && _addr(data, 36) == vault
            ) return (0, bytes4(0));
        }
        return (2, DrainTypes.UNKN);
    }

    function _bump(address account) internal {
        _s().accounts[account].policyNonce += 1;
    }

    function _frozen(address account) internal view returns (bool) {
        return block.timestamp < _s().accounts[account].frozenUntil;
    }

    function _has(address account, address who, uint8 role) internal view returns (bool) {
        return (_s().roles[account][who] & role) != 0;
    }

    function _isRecipient(address account, address who) internal view returns (bool) {
        return who == _s().accounts[account].vault || _has(account, who, DrainTypes.ROLE_RECIPIENT);
    }

    function _isRevoke(bytes4 sel, bytes calldata data) internal pure returns (bool) {
        if (
            sel == DrainTypes.SEL_LOCKDOWN || sel == DrainTypes.SEL_INVALIDATE_NONCES
                || sel == DrainTypes.SEL_INVALIDATE_UNORDERED || sel == DrainTypes.SEL_CANCEL_AUTH
        ) return true;
        if (sel == DrainPolicy.clearDigest.selector) return true;
        if (sel == DrainTypes.SEL_APPROVE && data.length >= 68) return uint256(bytes32(data[36:68])) == 0;
        if (sel == DrainTypes.SEL_FOR_ALL && data.length >= 68) return uint256(bytes32(data[36:68])) == 0;
        if (sel == DrainTypes.SEL_PERMIT2_APPROVE && data.length >= 100) return uint256(bytes32(data[68:100])) == 0;
        return false;
    }

    function _admin(bytes4 sel) internal pure returns (bool) {
        return sel == DrainPolicy.queueOutflow.selector || sel == DrainPolicy.queueRecipient.selector
            || sel == DrainPolicy.queueSpender.selector || sel == DrainPolicy.queueExecutor.selector
            || sel == DrainPolicy.queueGuardian.selector || sel == DrainPolicy.queueVault.selector
            || sel == DrainPolicy.queueUninstall.selector || sel == DrainPolicy.queueUnfreeze.selector
            || sel == DrainPolicy.executeOp.selector || sel == DrainPolicy.cancel.selector
            || sel == DrainPolicy.setCap.selector || sel == DrainPolicy.setDelays.selector
            || sel == DrainPolicy.setMaxFreeze.selector || sel == DrainPolicy.setCooldown.selector
            || sel == DrainPolicy.setHot.selector || sel == DrainPolicy.setMode.selector
            || sel == DrainPolicy.removeRecipient.selector || sel == DrainPolicy.removeSpender.selector
            || sel == DrainPolicy.removeExecutor.selector || sel == DrainPolicy.track.selector;
    }

    function _hasherAdmin(bytes4 sel) internal pure returns (bool) {
        return sel == DrainHasher.registerPermit2Single.selector || sel == DrainHasher.registerPermit2Batch.selector
            || sel == DrainHasher.registerPermit2Transfer.selector || sel == DrainHasher.registerPermit2BatchTransfer.selector
            || sel == DrainHasher.registerPermit2Witness.selector || sel == DrainHasher.registerEIP2612.selector
            || sel == DrainHasher.registerEIP3009.selector;
    }

    function _orMin(uint64 supplied, uint256 minDefault) internal pure returns (uint64) {
        if (supplied == 0) return uint64(minDefault);
        if (supplied < minDefault) revert DrainErrors.BadPath();
        return supplied;
    }

    function _orFreeze(uint64 supplied) internal pure returns (uint64) {
        if (supplied == 0) return uint64(DrainTypes.MAX_FREEZE);
        if (supplied < 1 hours || supplied > DrainTypes.MAX_FREEZE) revert DrainErrors.BadPath();
        return supplied;
    }

    function _rank(uint8 mode) internal pure returns (uint8) {
        if (mode == DrainTypes.MODE_QUEUE) return 0;
        if (mode == DrainTypes.MODE_STRICT) return 1;
        return 2;
    }

    function _merge(uint8 a, bytes4 ar, uint8 b, bytes4 br) internal pure returns (uint8, bytes4) {
        if (a == 2) return (a, ar);
        if (b == 2) return (b, br);
        if (a == 1) return (a, ar);
        if (b == 1) return (b, br);
        return (0, bytes4(0));
    }

    function _addr(bytes calldata data, uint256 off) internal pure returns (address) {
        return address(uint160(uint256(bytes32(data[off:off + 32]))));
    }

    function _word(bytes memory data, uint256 i) internal pure returns (uint256 w) {
        assembly {
            w := mload(add(add(data, 32), mul(i, 32)))
        }
    }

    function _s() private pure returns (Store storage s) {
        bytes32 slot = POLICY_SLOT;
        assembly {
            s.slot := slot
        }
    }
}
