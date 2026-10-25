// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

import "./QCHOracle.sol";

/// Curator actions for one testnet market.
///
/// The caller is the curator. A trigger lowers the supply cap now and
/// schedules a lower LTV for the SaR timelock. There is no liquidation
/// entrypoint and no function that raises a cap or an LTV.
///
/// Flag 16 and flag 32 are the SaR peg and quantum-canary bits. They
/// carry the haircut. A caller who has not read a score must not pass 0.
contract QCHSentinel {
    uint16 public constant FLAG_PEG = 16;
    uint16 public constant FLAG_CANARY = 32;
    uint256 public constant TIMELOCK = 7 days;

    QCHOracle public immutable oracle;
    address public immutable curator;
    uint16 public immutable jumpBps;
    uint16 public immutable cutBps;

    struct Market {
        uint256 supplyCap;
        uint256 lltv;
        uint256 pendingLltv;
        uint64 pendingReady;
        uint16 lastQes;
        uint64 deadline;
        bool seeded;
        bool seen;
        bool attested;
    }

    mapping(bytes32 => Market) public markets;

    event CapCut(bytes32 indexed asset, bytes32 indexed market, uint256 oldCap, uint256 newCap, uint16 qesBps);
    event LtvScheduled(bytes32 indexed market, uint256 pendingLltv, uint64 readyAt);
    event LtvCut(bytes32 indexed market, uint256 oldLltv, uint256 newLltv);

    constructor(QCHOracle oracle_, address curator_, uint16 jumpBps_, uint16 cutBps_) {
        require(_allowed(block.chainid), "testnet only");
        require(address(oracle_) != address(0) && curator_ != address(0), "curator");
        require(jumpBps_ > 0 && jumpBps_ <= 10000, "jump");
        require(cutBps_ > 0 && cutBps_ < 10000, "cut");
        oracle = oracle_;
        curator = curator_;
        jumpBps = jumpBps_;
        cutBps = cutBps_;
    }

    modifier onlyCurator() {
        require(msg.sender == curator, "curator");
        _;
    }

    function _allowed(uint256 id) internal pure returns (bool) {
        return id == 11155111 || id == 84532 || id == 421614 || id == 998;
    }

    /// Flags were supplied by the caller. Clear flags return a measured 0.
    function collateralHaircut(uint16 sarFlags, uint16 qesBps) external pure returns (uint16) {
        require(qesBps <= 10000, "qes");
        if ((sarFlags & FLAG_PEG) != 0 || (sarFlags & FLAG_CANARY) != 0) return qesBps;
        return 0;
    }

    function seed(bytes32 marketId, uint256 cap, uint256 lltv) external onlyCurator {
        Market storage m = markets[marketId];
        require(!m.seeded, "seeded");
        require(cap > 0 && lltv > 0, "seed");
        m.supplyCap = cap;
        m.lltv = lltv;
        m.seeded = true;
    }

    function setPolicy(bytes32 marketId, uint64 deadline, bool attested) external onlyCurator {
        Market storage m = markets[marketId];
        require(m.seeded, "seed");
        m.deadline = deadline;
        m.attested = attested;
    }

    /// Record the current score so a later increase can be a jump.
    function observe(bytes32 asset, bytes32 marketId) external onlyCurator {
        Market storage m = markets[marketId];
        require(m.seeded, "seed");
        (uint16 qes,,,) = oracle.qesOf(asset);
        m.lastQes = qes;
        m.seen = true;
    }

    function cut(bytes32 asset, bytes32 marketId) external onlyCurator {
        Market storage m = markets[marketId];
        require(m.seeded, "seed");
        (uint16 qes,, uint16 flags,) = oracle.qesOf(asset);
        bool canary = (flags & FLAG_CANARY) != 0;
        bool jump = m.seen && qes > m.lastQes && uint256(qes - m.lastQes) > jumpBps;
        bool due = m.deadline != 0 && block.timestamp >= m.deadline && !m.attested;
        require(canary || jump || due, "no trigger");
        uint256 nextCap = m.supplyCap * (10000 - cutBps) / 10000;
        require(nextCap < m.supplyCap, "cap");
        uint256 oldCap = m.supplyCap;
        m.supplyCap = nextCap;
        uint256 nextLtv = m.lltv * (10000 - cutBps) / 10000;
        require(nextLtv < m.lltv, "ltv");
        m.pendingLltv = nextLtv;
        m.pendingReady = uint64(block.timestamp + TIMELOCK);
        m.lastQes = qes;
        m.seen = true;
        emit CapCut(asset, marketId, oldCap, nextCap, qes);
        emit LtvScheduled(marketId, nextLtv, m.pendingReady);
    }

    function applyLtv(bytes32 marketId) external onlyCurator {
        Market storage m = markets[marketId];
        require(m.pendingReady != 0 && block.timestamp >= m.pendingReady, "timelock");
        require(m.pendingLltv < m.lltv, "ltv");
        uint256 oldLtv = m.lltv;
        m.lltv = m.pendingLltv;
        m.pendingReady = 0;
        emit LtvCut(marketId, oldLtv, m.lltv);
    }
}
