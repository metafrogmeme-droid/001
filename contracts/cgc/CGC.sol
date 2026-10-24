// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

/**
 * Conservation-Gated Collateral, testnet only.
 *
 * A curator wires this beside a lending market. brake() reads destination-chain
 * facts first. A reading with no violation, or a reading that fails, reverts
 * before the bond is accounted. The bond then freezes new borrows and new
 * collateral deposits. Repay, withdraw and liquidation keep working, and the
 * liquidation price does not move until a violation root is finalized.
 *
 * Four timers, snapshotted per claim from the chain config:
 *   W_brake   how long new borrows stay blocked
 *   W_prov    deadline for the claimant to post an unfinalized root
 *   W_final   how long a posted root must age before it is final
 *   W_final_max  refund the bond if no root is final by then
 *
 * An unfinalized root can only extend the brake. The bond is slashed only when
 * a finalized root shows no violation, and then half is paid to the prover and
 * half to the curator pool. Lifting the brake pays nobody. The bond is returned
 * in full if no root is final by W_final_max, if the proof system is paused or
 * retired, if the dispute game type changes, or if a watcher vote never resolves.
 *
 * Unichain reads DisputeGameFactory (game type 9, SUPER_CANNON_KONA). Bonds are
 * the chain's native testnet
 * asset. The one token is gwrsETH, a testnet receipt around an underlying the
 * curator names at deploy. There is no deploy script.
 */

interface ICGCFacts {
    function readBreach(bytes32 asset, uint8 claimType) external view returns (bool readable, bool violation);
}

interface ICGCSystem {
    function paused() external view returns (bool);
    function retired() external view returns (bool);
    function respectedGameType() external view returns (uint32);
}

interface ICGCRootBoard {
    function postedAt(bytes32 root) external view returns (uint64);
}

interface ICGCDisputeGameFactory {
    function gameAtIndex(uint256 index) external view returns (uint32 gameType, uint64 timestamp, address proxy);
}

interface ICGCDisputeGame {
    function status() external view returns (uint8);
    function rootClaim() external view returns (bytes32);
    function resolvedAt() external view returns (uint64);
}

interface ICGCWatchers {
    function attest(
        uint256 claimId,
        bytes32 evidence,
        uint256 num,
        uint256 den,
        bytes32[] calldata rs,
        bytes32[] calldata ss,
        uint8[] calldata vs
    ) external view returns (bool);
}

library CGCChains {
    uint8 internal constant MODE_ROOT = 1;
    uint8 internal constant MODE_DISPUTE = 2;
    uint8 internal constant MODE_WATCHER = 3;

    struct Windows {
        uint64 brake;
        uint64 prov;
        uint64 fin;
        uint64 finMax;
        uint8 mode;
        uint32 gameType;
    }

    function windows(uint256 chainId) internal pure returns (Windows memory) {
        if (chainId == 11155111) return Windows(6 hours, 1 hours, 2 hours, 24 hours, MODE_ROOT, 0);
        if (chainId == 11155420) return Windows(6 hours, 2 hours, 7 days + 6 hours, 18 days, MODE_DISPUTE, 0);
        if (chainId == 84532) return Windows(6 hours, 24 hours, 5 days + 6 hours, 8 days, MODE_DISPUTE, 0);
        if (chainId == 1301) return Windows(6 hours, 2 hours, 7 days + 6 hours, 18 days, MODE_DISPUTE, 9);
        if (chainId == 763373) return Windows(6 hours, 2 hours, 7 days + 6 hours, 18 days, MODE_DISPUTE, 0);
        if (chainId == 421614) return Windows(6 hours, 2 hours, 6 days + 14 hours, 15 days, MODE_ROOT, 0);
        if (
            chainId == 97 || chainId == 43113 || chainId == 10143 || chainId == 998 || chainId == 9746
        ) {
            return Windows(6 hours, 6 hours, 6 hours, 24 hours, MODE_WATCHER, 0);
        }
        revert("not testnet");
    }

    /// sigFloor 0 is a measurement. A den of 0 is unreadable.
    function inflowFactor(uint8 floor, bool readable) internal pure returns (uint256 num, uint256 den) {
        if (!readable) return (0, 0);
        if (floor == 0) return (0, 1);
        if (floor == 1) return (1, 10);
        if (floor == 2) return (1, 2);
        return (1, 1);
    }
}

contract CGCRootBoard is ICGCRootBoard {
    address public immutable poster;
    mapping(bytes32 => uint64) public postedAt;

    constructor(address poster_) {
        CGCChains.windows(block.chainid);
        require(poster_ != address(0), "poster");
        poster = poster_;
    }

    function post(bytes32 root_) external {
        require(msg.sender == poster, "poster");
        require(block.timestamp > 0, "clock");
        if (postedAt[root_] == 0) postedAt[root_] = uint64(block.timestamp);
    }
}

contract CGCRegistry {
    uint256 internal constant WEEK = 7 days;
    address public immutable publisher;
    address public curator;

    struct Path {
        bool exists;
        uint8 floor;
        bool floorOk;
        uint64 rate;
        bool rateOk;
        address upgradeAuthority;
        uint64 lastChange;
        bool changeOk;
        bytes32 bridgeRisk;
    }

    mapping(bytes32 => mapping(bytes32 => Path)) public paths;
    mapping(bytes32 => bytes32[]) internal pathIds;
    mapping(bytes32 => mapping(bytes32 => bool)) internal listed;

    constructor(address publisher_, address curator_) {
        CGCChains.windows(block.chainid);
        require(publisher_ != address(0) && curator_ != address(0), "addr");
        publisher = publisher_;
        curator = curator_;
    }

    function publish(
        bytes32 asset,
        bytes32 pathway,
        uint8 floor,
        bool floorOk,
        uint64 rate,
        bool rateOk,
        address upgradeAuthority,
        uint64 lastChange,
        bool changeOk,
        bytes32 bridgeRisk
    ) external {
        require(msg.sender == publisher, "publisher");
        Path storage p = paths[asset][pathway];
        p.exists = true;
        p.floor = floor;
        p.floorOk = floorOk;
        p.rate = rate;
        p.rateOk = rateOk;
        p.upgradeAuthority = upgradeAuthority;
        p.lastChange = lastChange;
        p.changeOk = changeOk;
        p.bridgeRisk = bridgeRisk;
        if (!listed[asset][pathway]) {
            listed[asset][pathway] = true;
            pathIds[asset].push(pathway);
        }
    }

    /// The weakest readable pathway. One unreadable pathway makes the asset unreadable.
    function sigFloorOf(bytes32 asset) external view returns (uint8 floor, bool readable) {
        bytes32[] storage ids = pathIds[asset];
        if (ids.length == 0) return (0, false);
        uint8 minF = type(uint8).max;
        for (uint256 i = 0; i < ids.length; i++) {
            Path storage p = paths[asset][ids[i]];
            if (!p.floorOk) return (0, false);
            if (p.floor < minF) minF = p.floor;
        }
        return (minF, true);
    }

    /// 0 unreadable, 1 changed within 7 days, 2 older than 7 days.
    function configChange(bytes32 asset, bytes32 pathway) external view returns (uint8) {
        Path storage p = paths[asset][pathway];
        if (!p.exists || !p.changeOk || p.lastChange == 0) return 0;
        if (block.timestamp < p.lastChange) return 0;
        if (block.timestamp - p.lastChange < WEEK) return 1;
        return 2;
    }

    function pathwayCount(bytes32 asset) external view returns (uint256) {
        return pathIds[asset].length;
    }
}

contract CGCWatchers is ICGCWatchers {
    address public immutable curator;
    uint256 public immutable k;
    address[] public members;
    mapping(address => bool) public isMember;

    constructor(address curator_, uint256 k_) {
        CGCChains.windows(block.chainid);
        require(curator_ != address(0), "curator");
        require(k_ >= 1 && k_ <= 16, "k");
        curator = curator_;
        k = k_;
    }

    function add(address member_) external {
        require(msg.sender == curator, "curator");
        require(member_ != address(0) && !isMember[member_], "member");
        require(members.length < 16, "full");
        isMember[member_] = true;
        members.push(member_);
    }

    function memberCount() external view returns (uint256) {
        return members.length;
    }

    function _signer(bytes32 hash, bytes32 r, bytes32 s, uint8 v) private pure returns (address) {
        if (v < 27) v += 27;
        return ecrecover(hash, v, r, s);
    }

    function attest(
        uint256 claimId,
        bytes32 evidence,
        uint256 num,
        uint256 den,
        bytes32[] calldata rs,
        bytes32[] calldata ss,
        uint8[] calldata vs
    ) external view returns (bool) {
        uint256 n = rs.length;
        if (n != ss.length || n != vs.length || n < k) return false;
        address[] memory found = new address[](n);
        bytes32 digest = keccak256(abi.encode(block.chainid, claimId, evidence, num, den));
        bytes32 hash = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", digest));
        for (uint256 i = 0; i < n; i++) {
            address a = _signer(hash, rs[i], ss[i], vs[i]);
            if (a == address(0) || !isMember[a]) return false;
            for (uint256 j = 0; j < i; j++) {
                if (found[j] == a) return false;
            }
            found[i] = a;
        }
        return true;
    }
}

contract CGCBrake {
    uint8 internal constant OPEN = 1;
    uint8 internal constant CONFIRMED = 2;
    uint8 internal constant REFUNDED = 3;
    uint8 internal constant SLASHED = 4;
    uint8 internal constant USABLE = 1;
    uint8 internal constant TOO_EARLY = 2;
    uint8 internal constant PAUSED = 3;
    uint8 internal constant UNREADABLE = 4;
    uint8 internal constant TYPE_CHANGED = 5;
    uint8 internal constant NOT_DEFENDER = 6;
    uint8 internal constant NO_ROOT = 7;
    uint8 internal constant DEFENDER_WINS = 2;

    uint8 public constant MODE_ROOT = 1;
    uint8 public constant MODE_DISPUTE = 2;
    uint8 public constant MODE_WATCHER = 3;
    uint8 public constant KIND_STARK = 2;
    uint8 public constant KIND_GROTH16 = 4;
    uint8 public constant KIND_PLONK = 5;
    uint8 public constant KIND_BRIDGE = 6;

    address public immutable curator;
    address public immutable curatorPool;
    address public immutable facts;
    address public immutable system;
    address public immutable board;
    address public immutable factory;
    address public immutable watchers;
    uint64 public wBrake;
    uint64 public wProv;
    uint64 public wFin;
    uint64 public wFinalMax;
    uint8 public mode;
    uint32 public expectedGameType;
    uint256 public bondFloor;
    uint256 public lockedBonds;
    uint256 public nextId = 1;
    uint256 private locked;

    struct Claim {
        address claimer;
        bytes32 asset;
        uint8 claimType;
        uint8 status;
        uint32 gameType;
        uint64 openedAt;
        uint64 provUntil;
        uint64 refundAt;
        uint64 brakeFor;
        uint64 finDelay;
        uint64 violationFinalAt;
        uint64 clearFinalAt;
        uint256 violationGame;
        uint256 clearGame;
        bytes32 evidence;
        bytes32 violationRoot;
        bytes32 clearRoot;
        uint256 num;
        uint256 den;
        uint256 bond;
        bool watcherResolved;
    }

    mapping(uint256 => Claim) internal claims;
    mapping(bytes32 => uint64) public freezeUntil;
    mapping(bytes32 => uint256) public borrowed;
    mapping(bytes32 => bool) public borrowedSet;
    mapping(bytes32 => uint256) public backingNum;
    mapping(bytes32 => uint256) public backingDen;
    mapping(bytes32 => uint256) public backingClaim;
    mapping(bytes32 => bool) public backingSet;

    modifier nonReentrant() {
        require(locked == 0, "reentrancy");
        locked = 1;
        _;
        locked = 0;
    }

    constructor(
        address facts_,
        address system_,
        address board_,
        address factory_,
        address watchers_,
        address curator_,
        address curatorPool_,
        uint256 bondFloorWei
    ) {
        CGCChains.Windows memory d = CGCChains.windows(block.chainid);
        require(d.finMax > d.fin && d.brake != 0 && d.prov != 0 && d.fin != 0, "window");
        require(facts_ != address(0) && system_ != address(0), "addr");
        require(curator_ != address(0) && curatorPool_ != address(0) && curatorPool_ != curator_, "curator");
        require(bondFloorWei > 0, "floor");
        if (d.mode == MODE_ROOT) require(board_ != address(0), "board");
        if (d.mode == MODE_DISPUTE) require(factory_ != address(0), "factory");
        if (d.mode == MODE_WATCHER) require(watchers_ != address(0), "watchers");
        facts = facts_;
        system = system_;
        board = board_;
        factory = factory_;
        watchers = watchers_;
        curator = curator_;
        curatorPool = curatorPool_;
        bondFloor = bondFloorWei;
        wBrake = d.brake;
        wProv = d.prov;
        wFin = d.fin;
        wFinalMax = d.finMax;
        mode = d.mode;
        expectedGameType = d.gameType;
    }

    function chainDefaults() external view returns (uint64, uint64, uint64, uint64, uint8, uint32) {
        CGCChains.Windows memory d = CGCChains.windows(block.chainid);
        return (d.brake, d.prov, d.fin, d.finMax, d.mode, d.gameType);
    }

    function setWindows(uint64 brake, uint64 prov, uint64 fin, uint64 finMax) external {
        require(msg.sender == curator, "curator");
        CGCChains.Windows memory d = CGCChains.windows(block.chainid);
        require(brake >= d.brake && prov >= d.prov && fin >= d.fin && finMax >= d.finMax, "window too short");
        require(finMax > fin, "final max");
        wBrake = brake;
        wProv = prov;
        wFin = fin;
        wFinalMax = finMax;
    }

    function setBondFloor(uint256 next) external {
        require(msg.sender == curator, "curator");
        require(next > 0, "floor");
        bondFloor = next;
    }

    /// `readable` false is not a borrowed value of zero.
    function setBorrowed(bytes32 asset, uint256 amount, bool readable) external {
        require(msg.sender == curator, "curator");
        borrowed[asset] = readable ? amount : 0;
        borrowedSet[asset] = readable;
    }

    function requiredBond(bytes32 asset) public view returns (uint256) {
        require(borrowedSet[asset], "borrowed unreadable");
        uint256 ratio = borrowed[asset] / 1000;
        return ratio > bondFloor ? ratio : bondFloor;
    }

    function frozen(bytes32 asset) public view returns (bool) {
        return block.timestamp < freezeUntil[asset];
    }

    function backing(bytes32 asset) external view returns (uint256 num, uint256 den, bool repriced) {
        if (!backingSet[asset]) return (1, 1, false);
        return (backingNum[asset], backingDen[asset], true);
    }

    function claimCore(uint256 id)
        external
        view
        returns (address claimer, bytes32 asset, uint8 status, uint256 bond, bool watcherResolved)
    {
        Claim storage c = claims[id];
        return (c.claimer, c.asset, c.status, c.bond, c.watcherResolved);
    }

    function claimTimes(uint256 id)
        external
        view
        returns (uint64 openedAt, uint64 provUntil, uint64 finDelay, uint64 refundAt, uint64 brakeFor)
    {
        Claim storage c = claims[id];
        return (c.openedAt, c.provUntil, c.finDelay, c.refundAt, c.brakeFor);
    }

    function claimRoots(uint256 id)
        external
        view
        returns (bytes32 violationRoot, uint64 violationFinalAt, bytes32 clearRoot, uint64 clearFinalAt, uint256 num, uint256 den)
    {
        Claim storage c = claims[id];
        return (c.violationRoot, c.violationFinalAt, c.clearRoot, c.clearFinalAt, c.num, c.den);
    }

    function brake(bytes32 asset, uint8 claimType, bytes32 evidence) external payable nonReentrant returns (uint256 id) {
        require(claimType == 1 || claimType == 2, "claim type");
        require(evidence != bytes32(0), "evidence");
        require(block.timestamp > 0, "clock");
        (bool factOk, bool violation) = _facts(asset, claimType);
        require(factOk, "facts unreadable");
        require(violation, "no violation");
        (bool sysOk, bool paused, bool retired, uint32 liveType) = _system();
        require(sysOk, "system unreadable");
        require(!paused && !retired, "system unavailable");
        if (mode == MODE_DISPUTE && expectedGameType != 0) require(liveType == expectedGameType, "game type");
        require(borrowedSet[asset], "borrowed unreadable");
        uint256 bond = requiredBond(asset);
        require(msg.value == bond, "bond");

        id = nextId++;
        Claim storage c = claims[id];
        c.claimer = msg.sender;
        c.asset = asset;
        c.claimType = claimType;
        c.evidence = evidence;
        c.bond = bond;
        c.status = OPEN;
        c.openedAt = uint64(block.timestamp);
        c.gameType = mode == MODE_DISPUTE ? liveType : 0;
        c.provUntil = _add(c.openedAt, wProv);
        c.refundAt = _add(c.openedAt, wFinalMax);
        c.brakeFor = _add(c.openedAt, wBrake);
        c.finDelay = wFin;
        lockedBonds += bond;
        if (freezeUntil[asset] < c.brakeFor) freezeUntil[asset] = c.brakeFor;
    }

    /// The claimant attaches a root the bound poster already published.
    /// This extends the brake and does not reprice or slash.
    function proposeRoot(uint256 id, uint256 num, uint256 den) external nonReentrant {
        require(mode == MODE_ROOT, "mode");
        Claim storage c = claims[id];
        bytes32 leaf = _violationLeaf(c, num, den);
        uint64 born = ICGCRootBoard(board).postedAt(leaf);
        _attachViolation(c, leaf, born, 0);
    }

    /// Unichain and the other dispute-game chains. The root is the game's
    /// rootClaim, read from DisputeGameFactory.gameAtIndex.
    function proposeGame(uint256 id, uint256 index, uint256 num, uint256 den) external nonReentrant {
        require(mode == MODE_DISPUTE, "mode");
        Claim storage c = claims[id];
        bytes32 leaf = _violationLeaf(c, num, den);
        (uint64 born,) = _loadGame(c, index, leaf);
        _attachViolation(c, leaf, born, index);
    }

    function proposeClearRoot(uint256 id) external nonReentrant {
        require(mode == MODE_ROOT, "mode");
        Claim storage c = claims[id];
        bytes32 leaf = _clearLeaf(c);
        uint64 born = ICGCRootBoard(board).postedAt(leaf);
        _attachClear(c, leaf, born, 0);
    }

    function proposeClearGame(uint256 id, uint256 index) external nonReentrant {
        require(mode == MODE_DISPUTE, "mode");
        Claim storage c = claims[id];
        bytes32 leaf = _clearLeaf(c);
        (uint64 born,) = _loadGame(c, index, leaf);
        _attachClear(c, leaf, born, index);
    }

    function confirm(uint256 id) external nonReentrant {
        Claim storage c = claims[id];
        require(c.status == OPEN, "not open");
        require(mode != MODE_WATCHER, "mode");
        _requireUsable(_usability(c, c.violationRoot, c.violationFinalAt, c.violationGame));
        require(_usability(c, c.clearRoot, c.clearFinalAt, c.clearGame) != USABLE, "cleared");
        _setBacking(c.asset, id, c.num, c.den);
        c.status = CONFIRMED;
        _returnBond(c);
    }

    function slash(uint256 id) external nonReentrant {
        Claim storage c = claims[id];
        require(c.status == OPEN, "not open");
        require(mode != MODE_WATCHER, "mode");
        _requireUsable(_usability(c, c.clearRoot, c.clearFinalAt, c.clearGame));
        c.status = SLASHED;
        uint256 bond = c.bond;
        c.bond = 0;
        lockedBonds -= bond;
        uint256 half = bond / 2;
        _pay(msg.sender, half);
        _pay(curatorPool, bond - half);
    }

    function refundNoFinalRoot(uint256 id) external nonReentrant {
        Claim storage c = claims[id];
        require(c.status == OPEN, "not open");
        require(mode != MODE_WATCHER, "mode");
        require(block.timestamp >= c.refundAt, "final max");
        require(_usability(c, c.violationRoot, c.violationFinalAt, c.violationGame) != USABLE, "root is final");
        require(_usability(c, c.clearRoot, c.clearFinalAt, c.clearGame) != USABLE, "root is final");
        c.status = REFUNDED;
        _returnBond(c);
    }

    function refundPaused(uint256 id) external nonReentrant {
        Claim storage c = claims[id];
        require(c.status == OPEN, "not open");
        (bool ok, bool paused, bool retired,) = _system();
        require(ok && (paused || retired), "not paused");
        c.status = REFUNDED;
        _returnBond(c);
    }

    function refundGameTypeChanged(uint256 id) external nonReentrant {
        Claim storage c = claims[id];
        require(c.status == OPEN, "not open");
        require(mode == MODE_DISPUTE, "mode");
        (bool ok,,, uint32 liveType) = _system();
        require(ok, "game type unreadable");
        require(liveType != c.gameType, "game type same");
        c.status = REFUNDED;
        _returnBond(c);
    }

    function refundWatcherUnresolved(uint256 id) external nonReentrant {
        Claim storage c = claims[id];
        require(c.status == OPEN, "not open");
        require(mode == MODE_WATCHER, "mode");
        require(!c.watcherResolved, "resolved");
        require(block.timestamp >= c.refundAt, "final max");
        c.status = REFUNDED;
        _returnBond(c);
    }

    /// A resolved watcher vote confirms or refunds. It does not slash: a slash
    /// requires a finalized root, and these chains have none.
    function vote(
        uint256 id,
        uint256 num,
        uint256 den,
        bytes32[] calldata rs,
        bytes32[] calldata ss,
        uint8[] calldata vs
    ) external nonReentrant {
        Claim storage c = claims[id];
        require(mode == MODE_WATCHER, "mode");
        require(c.status == OPEN, "not open");
        require(!c.watcherResolved, "resolved");
        require(block.timestamp <= c.provUntil, "prov window");
        require(den != 0, "ratio");
        require(ICGCWatchers(watchers).attest(id, c.evidence, num, den, rs, ss, vs), "watchers");
        c.watcherResolved = true;
        if (num < den) {
            _setBacking(c.asset, id, num, den);
            c.num = num;
            c.den = den;
            c.status = CONFIRMED;
        } else {
            c.status = REFUNDED;
        }
        _returnBond(c);
    }

    /// Pairing proofs and an unimplemented hash proof are refused. This entry
    /// has no success path.
    function submitProof(uint256, uint8 kind, bytes calldata) external pure {
        if (kind == KIND_STARK || kind == KIND_GROTH16 || kind == KIND_PLONK || kind == KIND_BRIDGE) {
            revert("rejected kind");
        }
        revert("kind");
    }

    function _violationLeaf(Claim storage c, uint256 num, uint256 den) internal returns (bytes32) {
        require(c.status == OPEN, "not open");
        require(msg.sender == c.claimer, "claimer");
        require(den != 0 && num < den, "ratio");
        c.num = num;
        c.den = den;
        return keccak256(abi.encode(c.evidence, num, den));
    }

    function _clearLeaf(Claim storage c) internal view returns (bytes32) {
        require(c.status == OPEN, "not open");
        return keccak256(abi.encode(c.evidence, uint256(1), uint256(1)));
    }

    function _loadGame(Claim storage c, uint256 index, bytes32 leaf) internal view returns (uint64 born, address proxy) {
        uint32 gType;
        (gType, born, proxy) = ICGCDisputeGameFactory(factory).gameAtIndex(index);
        require(proxy != address(0), "game");
        require(gType == c.gameType, "game type");
        require(ICGCDisputeGame(proxy).rootClaim() == leaf, "root");
    }

    function _attachViolation(Claim storage c, bytes32 leaf, uint64 born, uint256 gameIndex) internal {
        require(c.violationRoot == bytes32(0), "posted");
        require(block.timestamp <= c.provUntil, "prov window");
        require(born != 0 && born <= block.timestamp, "root time");
        c.violationRoot = leaf;
        c.violationGame = gameIndex;
        c.violationFinalAt = _add(born, c.finDelay);
        if (freezeUntil[c.asset] < c.violationFinalAt) freezeUntil[c.asset] = c.violationFinalAt;
    }

    function _attachClear(Claim storage c, bytes32 leaf, uint64 born, uint256 gameIndex) internal {
        require(c.clearRoot == bytes32(0), "posted");
        require(born != 0 && born <= block.timestamp, "root time");
        c.clearRoot = leaf;
        c.clearGame = gameIndex;
        c.clearFinalAt = _add(born, c.finDelay);
    }

    function _facts(bytes32 asset, uint8 claimType) internal view returns (bool readable, bool violation) {
        try ICGCFacts(facts).readBreach(asset, claimType) returns (bool r, bool v) {
            return (r, v);
        } catch {
            return (false, false);
        }
    }

    function _system() internal view returns (bool readable, bool paused, bool retired, uint32 gameType) {
        try ICGCSystem(system).paused() returns (bool p) {
            paused = p;
        } catch {
            return (false, false, false, 0);
        }
        try ICGCSystem(system).retired() returns (bool r) {
            retired = r;
        } catch {
            return (false, false, false, 0);
        }
        try ICGCSystem(system).respectedGameType() returns (uint32 g) {
            return (true, paused, retired, g);
        } catch {
            return (false, false, false, 0);
        }
    }

    function _usability(Claim storage c, bytes32 root, uint64 finalAt, uint256 gameIndex) internal view returns (uint8) {
        if (root == bytes32(0) || finalAt == 0) return NO_ROOT;
        if (block.timestamp < finalAt) return TOO_EARLY;
        (bool ok, bool paused, bool retired, uint32 liveType) = _system();
        if (!ok) return UNREADABLE;
        if (paused || retired) return PAUSED;
        if (mode == MODE_DISPUTE) {
            if (liveType != c.gameType) return TYPE_CHANGED;
            if (!_defenderWon(gameIndex, root)) return NOT_DEFENDER;
            return USABLE;
        }
        if (mode == MODE_ROOT) {
            if (ICGCRootBoard(board).postedAt(root) == 0) return NO_ROOT;
            return USABLE;
        }
        return NO_ROOT;
    }

    function _requireUsable(uint8 code) internal pure {
        if (code == USABLE) return;
        if (code == TOO_EARLY) revert("not final");
        if (code == PAUSED) revert("system unavailable");
        if (code == TYPE_CHANGED) revert("game type changed");
        if (code == UNREADABLE) revert("system unreadable");
        if (code == NO_ROOT) revert("no root");
        revert("not defender");
    }

    function _defenderWon(uint256 gameIndex, bytes32 root) internal view returns (bool) {
        try ICGCDisputeGameFactory(factory).gameAtIndex(gameIndex) returns (uint32, uint64, address proxy) {
            if (proxy == address(0)) return false;
            try ICGCDisputeGame(proxy).status() returns (uint8 status) {
                if (status != DEFENDER_WINS) return false;
            } catch {
                return false;
            }
            try ICGCDisputeGame(proxy).resolvedAt() returns (uint64 t) {
                if (t == 0 || t > block.timestamp) return false;
            } catch {
                return false;
            }
            try ICGCDisputeGame(proxy).rootClaim() returns (bytes32 live) {
                return live == root;
            } catch {
                return false;
            }
        } catch {
            return false;
        }
    }

    function _setBacking(bytes32 asset, uint256 id, uint256 num, uint256 den) internal {
        require(den != 0 && num < den, "ratio");
        if (!backingSet[asset] || num * backingDen[asset] < backingNum[asset] * den) {
            backingSet[asset] = true;
            backingNum[asset] = num;
            backingDen[asset] = den;
            backingClaim[asset] = id;
        }
    }

    function _returnBond(Claim storage c) internal {
        uint256 bond = c.bond;
        c.bond = 0;
        lockedBonds -= bond;
        _pay(c.claimer, bond);
    }

    function _pay(address to, uint256 amount) internal {
        (bool ok,) = to.call{value: amount}("");
        require(ok, "pay");
    }

    function _add(uint64 t, uint64 d) internal pure returns (uint64) {
        uint256 sum = uint256(t) + uint256(d);
        require(sum <= type(uint64).max, "time");
        return uint64(sum);
    }
}

contract CGCInflow {
    CGCRegistry public immutable registry;
    CGCBrake public immutable brake;
    address public immutable curator;
    address public hook;
    address public wrapper;
    uint256 public inflowWindow;
    mapping(bytes32 => uint256) public baseGap;
    mapping(bytes32 => bool) public gapSet;
    mapping(bytes32 => uint64) public windowStart;
    mapping(bytes32 => uint256) public used;

    constructor(CGCRegistry registry_, CGCBrake brake_, address curator_, uint256 inflowWindow_) {
        CGCChains.windows(block.chainid);
        require(address(registry_) != address(0) && address(brake_) != address(0), "addr");
        require(curator_ != address(0), "curator");
        require(inflowWindow_ > 0, "window");
        registry = registry_;
        brake = brake_;
        curator = curator_;
        inflowWindow = inflowWindow_;
    }

    function bindHook(address hook_) external {
        require(msg.sender == curator, "curator");
        require(hook == address(0) && hook_ != address(0), "bind");
        hook = hook_;
    }

    /// The testnet wrapper shares this cap. A second counter would be a second answer.
    function bindWrapper(address wrapper_) external {
        require(msg.sender == curator, "curator");
        require(wrapper == address(0) && wrapper_ != address(0), "bind");
        wrapper = wrapper_;
    }

    function setBaseGap(bytes32 asset, uint256 gap, bool readable) external {
        require(msg.sender == curator, "curator");
        baseGap[asset] = readable ? gap : 0;
        gapSet[asset] = readable;
    }

    function maxInflow(bytes32 asset) public view returns (uint256 amount, bool readable) {
        (uint8 floor, bool ok) = registry.sigFloorOf(asset);
        if (!ok || !gapSet[asset]) return (0, false);
        (uint256 n, uint256 d) = CGCChains.inflowFactor(floor, true);
        if (d == 0) return (0, false);
        return (baseGap[asset] * n / d, true);
    }

    function record(bytes32 asset, uint256 amount) external {
        require(msg.sender == hook || msg.sender == wrapper, "hook");
        require(!brake.frozen(asset), "frozen");
        (uint256 cap, bool ok) = maxInflow(asset);
        require(ok, "inflow unreadable");
        uint64 start = windowStart[asset];
        if (start == 0 || block.timestamp >= uint256(start) + inflowWindow) {
            windowStart[asset] = uint64(block.timestamp);
            used[asset] = 0;
        }
        require(block.timestamp > 0, "clock");
        require(used[asset] + amount <= cap, "inflow cap");
        used[asset] += amount;
    }

    /// Burn path of the wrapper. Net inflow, and it still works while frozen.
    function release(bytes32 asset, uint256 amount) external {
        require(msg.sender == wrapper, "wrapper");
        uint256 u = used[asset];
        used[asset] = u >= amount ? u - amount : 0;
    }
}

/// Euler EVK operation bits. OP_LIQUIDATE is bit 11. Bit 12 is the flash loan.
/// The vault call selector was not checked against a deployment; the market
/// address is bound by the curator, and `hook` applies this mask.
contract CGCEulerHook {
    uint256 public constant OP_DEPOSIT = 1 << 0;
    uint256 public constant OP_MINT = 1 << 1;
    uint256 public constant OP_WITHDRAW = 1 << 2;
    uint256 public constant OP_REDEEM = 1 << 3;
    uint256 public constant OP_TRANSFER = 1 << 4;
    uint256 public constant OP_BORROW = 1 << 6;
    uint256 public constant OP_REPAY = 1 << 7;
    uint256 public constant OP_REPAY_WITH_SHARES = 1 << 8;
    uint256 public constant OP_PULL_DEBT = 1 << 9;
    uint256 public constant OP_LIQUIDATE = 1 << 11;
    uint256 public constant OP_FLASHLOAN = 1 << 12;
    uint256 internal constant NEW_EXPOSURE = OP_DEPOSIT | OP_MINT | OP_BORROW | OP_FLASHLOAN;

    CGCInflow public immutable inflow;
    CGCBrake public immutable brake;
    address public immutable curator;
    bytes32 public immutable asset;
    address public market;

    constructor(CGCInflow inflow_, CGCBrake brake_, address curator_, bytes32 asset_) {
        CGCChains.windows(block.chainid);
        require(address(inflow_) != address(0) && address(brake_) != address(0), "addr");
        require(curator_ != address(0), "curator");
        inflow = inflow_;
        brake = brake_;
        curator = curator_;
        asset = asset_;
    }

    function setMarket(address market_) external {
        require(msg.sender == curator, "curator");
        require(market_ != address(0), "market");
        market = market_;
    }

    function hook(address, address, uint256 amount, uint256 op) external {
        _guard(asset, op);
        if ((op & (OP_DEPOSIT | OP_MINT)) != 0) inflow.record(asset, amount);
    }

    function checkDeposit(bytes32 asset_, address, uint256 assets) external {
        _guard(asset_, OP_DEPOSIT);
        inflow.record(asset, assets);
    }

    function checkBorrow(bytes32 asset_, uint256) external view {
        _guard(asset_, OP_BORROW);
    }

    function checkRepay(bytes32 asset_, uint256) external view {
        _guard(asset_, OP_REPAY);
    }

    function checkWithdraw(bytes32 asset_, address, uint256) external view {
        _guard(asset_, OP_WITHDRAW);
    }

    function _guard(bytes32 asset_, uint256 op) internal view {
        require(msg.sender == market, "market");
        require(asset_ == asset, "asset");
        if ((op & NEW_EXPOSURE) != 0) require(!brake.frozen(asset), "frozen");
    }
}

contract CGCCapAutomator {
    CGCInflow public immutable inflow;
    CGCBrake public immutable brake;
    address public immutable curator;
    uint256 public immutable cooldown;
    mapping(bytes32 => uint256) public storedCap;
    mapping(bytes32 => bool) public capSet;
    mapping(bytes32 => uint64) public lastRaise;

    constructor(CGCInflow inflow_, CGCBrake brake_, address curator_, uint256 cooldown_) {
        CGCChains.windows(block.chainid);
        require(address(inflow_) != address(0) && address(brake_) != address(0), "addr");
        require(curator_ != address(0), "curator");
        require(cooldown_ > 0, "cooldown");
        inflow = inflow_;
        brake = brake_;
        curator = curator_;
        cooldown = cooldown_;
    }

    function currentCap(bytes32 asset, uint256 supply) public view returns (uint256 cap, bool readable) {
        (uint256 room, bool ok) = inflow.maxInflow(asset);
        if (!ok) return (0, false);
        return (supply + room, true);
    }

    function commitRaise(bytes32 asset, uint256 supply) external returns (uint256 cap) {
        require(msg.sender == curator, "curator");
        require(!brake.frozen(asset), "frozen");
        require(block.timestamp >= uint256(lastRaise[asset]) + cooldown, "cooldown");
        bool ok;
        (cap, ok) = currentCap(asset, supply);
        require(ok, "cap unreadable");
        storedCap[asset] = cap;
        capSet[asset] = true;
        lastRaise[asset] = uint64(block.timestamp);
    }
}

/// One collateral price. Not a token. `price` stays on the underlying reading
/// until a violation is confirmed, then scales by the backing ratio.
contract CGCOracleAdapter {
    CGCBrake public immutable brake;
    bytes32 public immutable asset;
    address public immutable curator;
    uint256 public underlying;
    bool public priceSet;
    uint256 public updatedAt;

    constructor(CGCBrake brake_, bytes32 asset_, address curator_) {
        CGCChains.windows(block.chainid);
        require(address(brake_) != address(0) && curator_ != address(0), "addr");
        brake = brake_;
        asset = asset_;
        curator = curator_;
    }

    function setUnderlying(uint256 px) external {
        require(msg.sender == curator, "curator");
        require(px > 0, "price");
        require(block.timestamp > 0, "clock");
        underlying = px;
        priceSet = true;
        updatedAt = block.timestamp;
    }

    function price() public view returns (uint256) {
        require(priceSet, "price unreadable");
        (uint256 num, uint256 den, bool repriced) = brake.backing(asset);
        if (!repriced) return underlying;
        require(den != 0, "backing");
        return underlying * num / den;
    }

    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
        uint256 px = price();
        return (1, int256(px), updatedAt, updatedAt, 1);
    }

    function newExposureAllowed() external view returns (bool) {
        return !brake.frozen(asset);
    }

    function quoteNewExposure() external view returns (uint256) {
        require(!brake.frozen(asset), "frozen");
        return price();
    }
}
