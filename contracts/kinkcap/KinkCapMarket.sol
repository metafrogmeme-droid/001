// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

/// @title Kink Cap market
/// @notice Fully collateralized hourly caps on Hyperliquid's USDC borrow
///         rate. Positions are ERC-1155 tokens of amount 1. They transfer
///         unless this deployment was constructed with transfers off. Mint
///         and redeem still run when transfers are off. Seller collateral
///         sits in this contract. Nothing here supplies it into Hyperliquid
///         lending.
///
///         Kind 0 is a cap (ceiling 100%). Kind 1 is a cap spread, the
///         primary series, ceiling 40% when the caller passes 0. Kind 2 is
///         a carry-flip digital: it pays D when the six-hour mean is above
///         the strike. The rate is 0.05 + 4.75 * max(0, u - 0.8), checked
///         on the raw 0x812 totals (1e8 units per USDC). borrowYearlyRateBps
///         is a cross-check: a gap of 2 bp or more freezes the series. The
///         precompile read is budgeted at about 20,720 gas. A gap of more
///         than two hours freezes the series. A base rate that is no longer
///         5% below the kink freezes it as a model change. The guardian may unfreeze after two
///         good hours, and only inside 24 hours. After that the series
///         settles at the last good hour.
///
///         The current hour may be read from 0x812 by anyone. A past hour
///         uses this market's own 2-of-3 feed. When both exist and they
///         differ by 2 bp or more, the hour keeps the lower rate.
///         When neither exists, that hour pays 0 and is not stored as a
///         measured 0% rate.
interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

interface IERC1155Receiver {
    function onERC1155Received(address operator, address from, uint256 id, uint256 value, bytes calldata data)
        external
        returns (bytes4);
    function onERC1155BatchReceived(
        address operator,
        address from,
        uint256[] calldata ids,
        uint256[] calldata values,
        bytes calldata data
    ) external returns (bytes4);
}

contract KinkCapMarket {
    uint256 public constant CHAIN_ID = 998;
    address public constant PRECOMPILE = address(uint160(0x812));
    address public constant L1_BLOCK = address(uint160(0x809));
    uint256 public constant TOKEN_INDEX = 0;

    uint256 public constant WAD = 1e18;
    uint256 public constant BASE = 0.05e18;
    uint256 public constant KINK = 0.8e18;
    /// Exclusive cross-check bound, 2 bp in wad. The stored rate is the
    /// formula on the raw totals. A gap of this size or larger is a mismatch.
    uint256 public constant TOLERANCE = 2e14;
    /// Measured cost of a 0x812 read. sample() forwards this much gas.
    uint256 public constant PRECOMPILE_GAS = 20720;
    /// Raw totals in the precompile are this many units per USDC.
    uint256 public constant RAW_PER_TOKEN = 1e8;
    /// Carry-flip default strike: the 11.6% funding baseline. A caller
    /// who passes 0 gets this strike.
    uint256 public constant FUNDING_BASELINE_BPS = 1160;
    uint256 public constant HOURS_PER_YEAR = 8760;
    uint256 public constant TRAILING = 6;
    uint256 public constant BPS = 10000;
    uint256 public constant BASE_BPS = 500;
    uint256 public constant KINK_BPS = 8000;
    uint256 public constant CAP_CEIL_BPS = 10000;
    uint256 public constant DEFAULT_CEIL_BPS = 4000;
    uint256 public constant GAP_HOURS = 2;
    uint256 public constant UNFREEZE_GOOD = 2;
    uint256 public constant UNFREEZE_WINDOW = 24;

    uint8 public constant STATE_OPEN = 0;
    uint8 public constant STATE_ACTIVE = 1;
    uint8 public constant STATE_FROZEN = 2;
    uint8 public constant STATE_SETTLED = 3;
    uint8 public constant KIND_CAP = 0;
    uint8 public constant KIND_SPREAD = 1;
    uint8 public constant KIND_DIGITAL = 2;
    uint8 public constant FREEZE_FORMULA = 0;
    uint8 public constant FREEZE_GAP = 1;
    uint8 public constant FREEZE_MODEL = 2;
    /// Internal classifier codes. Zero is the only success, so a formula
    /// failure cannot share it with the event's formula reason.
    uint8 private constant FAIL_FORMULA = 1;
    uint8 private constant FAIL_GAP = 2;
    uint8 private constant FAIL_MODEL = 3;

    bytes32 public constant TAG = keccak256("HRCFeed.v1");
    bytes32 public constant MODEL = keccak256("HRCModel.v1");
    bytes32 public constant SRC_CGC = keccak256("CGC");
    bytes32 public constant SRC_RXB = keccak256("RXB");
    bytes32 public constant SRC_T2C = keccak256("T2C");
    bytes32 public constant SRC_HRC = keccak256("HRC");

    uint256 private constant HALF_N =
        0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0;

    error WrongChain();
    error TransfersDisabled();

    IERC20 public immutable collateralToken;
    bool public immutable transfersEnabled;
    address public immutable guardian;

    address[3] public feedSigners;
    bytes32[3] public pqSlots;

    struct Series {
        uint64 strikeBps;
        uint64 ceilBps;
        uint64 startHour;
        uint64 endHour;
        uint8 kind;
        uint256 maxNotional;
        uint256 notional;
        uint8 state;
    }

    struct Sample {
        uint64 hourId;
        uint64 utilBps;
        uint64 rateBps;
        uint64 l1Block;
    }

    struct Life {
        uint64 nextHour;
        uint64 frozenAt;
        uint64 lastGoodHour;
        bool hasLastGood;
        uint8 freezeReason;
    }

    struct Position {
        address holder;
        uint256 seriesId;
        uint8 side;
        uint256 notional;
        uint256 collateral;
        uint256 claim;
        uint256 pairId;
        bool open;
        uint64 startHour;
    }

    struct Proposal {
        bool exists;
        uint256 cap;
    }

    struct Feed {
        uint256 supplied;
        uint256 borrowed;
        uint256 rateBps;
        uint256 fundingWad;
        bool present;
        uint8 v0;
        bytes32 r0;
        bytes32 s0;
        uint8 v1;
        bytes32 r1;
        bytes32 s1;
        uint8 v2;
        bytes32 r2;
        bytes32 s2;
    }

    uint256 public seriesCount;
    uint256 public positionCount;
    bool public marketFrozen;
    uint64 public marketFrozenAt;
    uint8 public marketFreezeReason;
    bool public hasLastHour;
    uint64 public lastHour;

    mapping(uint256 => Series) public series;
    mapping(uint256 => Life) public life;
    mapping(uint256 => uint32) public digitalBps;
    mapping(uint256 => uint256) public perAddressCap;
    mapping(uint256 => Position) public positions;
    mapping(uint64 => Sample) public samples;
    mapping(uint64 => bool) public sampleRecorded;
    mapping(uint64 => bool) public sampleGood;
    mapping(uint64 => bool) public sampleFromPrecompile;
    mapping(uint64 => bool) public sampleL1Known;
    mapping(uint256 => mapping(bytes32 => Proposal)) public proposals;
    mapping(bytes32 => address) public sourceAuthority;
    mapping(uint256 => uint256) public seriesCollateral;
    mapping(uint256 => uint256) public seriesOwed;
    mapping(uint256 => mapping(address => uint256)) public mintedTo;
    mapping(uint256 => uint256[]) private _seriesPositions;
    uint256[] public liveSeries;

    event SeriesCreated(
        uint256 indexed seriesId,
        uint8 kind,
        uint64 strikeBps,
        uint64 ceilBps,
        uint64 startHour,
        uint64 endHour,
        uint256 maxNotional
    );
    event Minted(
        uint256 indexed seriesId,
        uint256 indexed positionId,
        address indexed longHolder,
        uint256 notional,
        uint256 collateral
    );
    event Sampled(uint64 indexed hourId, uint64 utilBps, uint64 rateBps, uint64 l1Block, bool fromPrecompile);
    event Accrued(uint256 indexed seriesId, uint64 indexed hourId, uint256 pay);
    event Frozen(uint256 indexed seriesId, uint64 hourId, uint8 reason);
    event Settled(uint256 indexed seriesId, uint64 lastGoodHour, bool hadGoodHour, uint256 paid);
    event CapLowered(
        bytes32 indexed source,
        uint256 indexed seriesId,
        bool hadPrevious,
        uint256 previous,
        uint256 proposed,
        uint256 effectiveBefore,
        uint256 effectiveAfter
    );
    event Redeemed(uint256 indexed longId, address longHolder, address shortHolder, uint256 paid, uint256 returned_);
    event TransferSingle(address indexed operator, address indexed from, address indexed to, uint256 id, uint256 value);
    event TransferBatch(
        address indexed operator, address indexed from, address indexed to, uint256[] ids, uint256[] values
    );
    event ApprovalForAll(address indexed account, address indexed operator, bool approved);

    bytes4 private constant ERC165 = 0x01ffc9a7;
    bytes4 private constant ERC1155 = 0xd9b67a26;
    bytes4 private constant RECEIVED = 0xf23a6e61;
    bytes4 private constant BATCH_RECEIVED = 0xbc197c81;

    mapping(address => mapping(address => bool)) public isApprovedForAll;

    constructor(
        address collateral,
        address[3] memory signers,
        bool transfers,
        address[4] memory authorities,
        address guardian_
    ) {
        if (block.chainid != CHAIN_ID) revert WrongChain();
        require(guardian_ != address(0), "guardian");
        collateralToken = IERC20(collateral);
        transfersEnabled = transfers;
        guardian = guardian_;
        feedSigners = signers;
        // Authority slots stay CGC, RXB, T2C, HRC. Batch 3's build order
        // is RXB, then HRC, then T2C, and the minimum does not use that order.
        sourceAuthority[SRC_CGC] = authorities[0];
        sourceAuthority[SRC_RXB] = authorities[1];
        sourceAuthority[SRC_T2C] = authorities[2];
        sourceAuthority[SRC_HRC] = authorities[3];
    }

    function registerPqSlot(uint256 index, bytes32 material) external {
        require(index < 3, "slot");
        require(msg.sender == feedSigners[index], "signer");
        pqSlots[index] = material;
    }

    function createSeries(
        uint8 kind,
        uint64 strikeBps,
        uint64 ceilBps,
        uint64 startHour,
        uint64 endHour,
        uint256 maxNotional,
        uint256 addressCap,
        uint32 digital
    ) external returns (uint256 id) {
        uint64 strike = strikeBps;
        if (kind == KIND_DIGITAL && strike == 0) strike = uint64(FUNDING_BASELINE_BPS);
        (uint64 ceil, uint32 digitalOut) = _terms(kind, strike, ceilBps, digital);
        require(endHour > startHour, "hours");
        require(maxNotional > 0, "max");
        require(addressCap > 0, "address cap");
        id = _open(kind, strike, ceil, startHour, endHour, maxNotional, addressCap, digitalOut);
    }

    function _terms(uint8 kind, uint64 strikeBps, uint64 ceilBps, uint32 digital)
        internal
        pure
        returns (uint64 ceil, uint32 digitalOut)
    {
        require(kind <= KIND_DIGITAL, "kind");
        if (kind == KIND_CAP) {
            require(strikeBps <= CAP_CEIL_BPS, "strike");
            return (uint64(CAP_CEIL_BPS), 0);
        }
        if (kind == KIND_SPREAD) {
            ceil = ceilBps == 0 ? uint64(DEFAULT_CEIL_BPS) : ceilBps;
            require(ceil <= CAP_CEIL_BPS && ceil > strikeBps, "ceil");
            return (ceil, 0);
        }
        require(digital > 0 && digital <= BPS, "digital");
        require(strikeBps <= CAP_CEIL_BPS, "strike");
        return (0, digital);
    }

    function _open(
        uint8 kind,
        uint64 strikeBps,
        uint64 ceil,
        uint64 startHour,
        uint64 endHour,
        uint256 maxNotional,
        uint256 addressCap,
        uint32 digital
    ) internal returns (uint256 id) {
        id = seriesCount++;
        uint8 state = marketFrozen ? STATE_FROZEN : STATE_OPEN;
        series[id] = Series({
            strikeBps: strikeBps,
            ceilBps: ceil,
            startHour: startHour,
            endHour: endHour,
            kind: kind,
            maxNotional: maxNotional,
            notional: 0,
            state: state
        });
        _life(id, startHour);
        digitalBps[id] = digital;
        perAddressCap[id] = addressCap;
        proposals[id][SRC_HRC] = Proposal({exists: true, cap: maxNotional});
        liveSeries.push(id);
        emit SeriesCreated(id, kind, strikeBps, ceil, startHour, endHour, maxNotional);
        if (state == STATE_FROZEN) emit Frozen(id, marketFrozenAt, marketFreezeReason);
    }

    function _life(uint256 id, uint64 startHour) internal {
        life[id] = Life({
            nextHour: startHour,
            frozenAt: marketFrozen ? marketFrozenAt : 0,
            lastGoodHour: 0,
            hasLastGood: false,
            freezeReason: marketFrozen ? marketFreezeReason : 0
        });
    }

    function effectiveCap(uint256 seriesId) public view returns (bool any, uint256 cap) {
        // Batch 3 build order is RXB, HRC, T2C. The minimum ignores order.
        bytes32[4] memory srcs = [SRC_CGC, SRC_RXB, SRC_HRC, SRC_T2C];
        for (uint256 i = 0; i < 4; i++) {
            Proposal memory p = proposals[seriesId][srcs[i]];
            if (!p.exists) continue;
            if (!any || p.cap < cap) {
                any = true;
                cap = p.cap;
            }
        }
    }

    function lowerCap(uint256 seriesId, bytes32 source, uint256 proposed) external {
        require(seriesId < seriesCount, "series");
        require(
            source == SRC_CGC || source == SRC_RXB || source == SRC_T2C || source == SRC_HRC,
            "source"
        );
        address who = sourceAuthority[source];
        require(who != address(0) && msg.sender == who, "authority");
        Proposal storage p = proposals[seriesId][source];
        if (p.exists) require(proposed < p.cap, "not lower");
        (bool anyBefore, uint256 beforeCap) = effectiveCap(seriesId);
        bool hadPrevious = p.exists;
        uint256 previous = p.cap;
        p.exists = true;
        p.cap = proposed;
        (bool anyAfter, uint256 afterCap) = effectiveCap(seriesId);
        if (anyAfter && afterCap < series[seriesId].maxNotional) {
            series[seriesId].maxNotional = afterCap;
        }
        emit CapLowered(
            source,
            seriesId,
            hadPrevious,
            previous,
            proposed,
            anyBefore ? beforeCap : 0,
            anyAfter ? afterCap : 0
        );
    }

    function mint(uint256 seriesId, uint256 notional, address longHolder)
        external
        returns (uint256 longId, uint256 shortId)
    {
        Series storage s = series[seriesId];
        require(seriesId < seriesCount, "series");
        require(s.state == STATE_OPEN || s.state == STATE_ACTIVE, "state");
        require(notional > 0, "notional");
        require(longHolder != address(0), "holder");
        Life storage lived = life[seriesId];
        uint64 fromHour = lived.nextHour > s.startHour ? lived.nextHour : s.startHour;
        require(fromHour < s.endHour, "closed");
        (bool any, uint256 cap) = effectiveCap(seriesId);
        require(any, "no cap");
        require(s.notional + notional <= cap && s.notional + notional <= s.maxNotional, "cap");
        require(mintedTo[seriesId][longHolder] + notional <= perAddressCap[seriesId], "address cap");
        uint256 hoursLeft = uint256(s.endHour - fromHour);
        uint256 need = collateralFor(s.kind, s.strikeBps, s.ceilBps, digitalBps[seriesId], notional, hoursLeft);
        require(collateralToken.transferFrom(msg.sender, address(this), need), "pull");
        s.notional += notional;
        mintedTo[seriesId][longHolder] += notional;
        seriesCollateral[seriesId] += need;
        longId = positionCount++;
        shortId = positionCount++;
        positions[longId] = Position({
            holder: longHolder,
            seriesId: seriesId,
            side: 1,
            notional: notional,
            collateral: 0,
            claim: 0,
            pairId: shortId,
            open: true,
            startHour: fromHour
        });
        positions[shortId] = Position({
            holder: msg.sender,
            seriesId: seriesId,
            side: 2,
            notional: notional,
            collateral: need,
            claim: 0,
            pairId: longId,
            open: true,
            startHour: fromHour
        });
        _seriesPositions[seriesId].push(longId);
        emit Minted(seriesId, longId, longHolder, notional, need);
        emit TransferSingle(msg.sender, address(0), longHolder, longId, 1);
        emit TransferSingle(msg.sender, address(0), msg.sender, shortId, 1);
        _notifyOne(address(0), longHolder, longId, 1, "");
        _notifyOne(address(0), msg.sender, shortId, 1, "");
    }

    function collateralFor(
        uint8 kind,
        uint64 strikeBps,
        uint64 ceilBps,
        uint32 digital,
        uint256 notional,
        uint256 hours_
    ) public pure returns (uint256) {
        if (hours_ == 0 || notional == 0) return 0;
        if (kind == KIND_DIGITAL) {
            return _divUp(notional * uint256(digital) * hours_, BPS);
        }
        uint256 ceil = kind == KIND_CAP ? CAP_CEIL_BPS : (ceilBps == 0 ? DEFAULT_CEIL_BPS : uint256(ceilBps));
        if (ceil <= strikeBps) return 0;
        return _divUp(notional * (ceil - strikeBps) * hours_, BPS * HOURS_PER_YEAR);
    }

    /// @notice Permissionless read of precompile 0x812 for token 0, this hour only.
    function sample() external {
        uint64 hour = uint64(block.timestamp / 3600);
        (bool decoded, uint64 utilBps, uint64 rateBps, uint8 fail) = _precompile();
        (uint64 l1, bool l1Known) = _l1();
        if (!decoded) return;
        if (fail != 0) {
            _reject(hour, l1, l1Known, true, fail);
            return;
        }
        _accept(hour, utilBps, rateBps, l1, l1Known, true);
    }

    /// @notice A past hour, or the current hour beside the precompile.
    ///         Two signatures of this market's own three. One is not a feed.
    function sampleWithFeed(uint64 hour, Feed calldata feed) external {
        uint64 nowHour = uint64(block.timestamp / 3600);
        require(hour <= nowHour, "future");
        (bool feedOk, uint64 feedUtil, uint64 feedRate) = _feed(hour, feed);
        bool preOk;
        uint64 preUtil;
        uint64 preRate;
        uint8 fail;
        if (hour == nowHour) {
            (preOk, preUtil, preRate, fail) = _precompile();
            if (preOk && fail != 0) {
                (uint64 l1bad, bool knownBad) = _l1();
                _reject(hour, l1bad, knownBad, true, fail);
                return;
            }
        }
        (uint64 l1, bool l1Known) = hour == nowHour ? _l1() : (uint64(0), false);
        if (preOk && feedOk) {
            (uint64 utilBps, uint64 rateBps, bool fromPre) = _lower(preUtil, preRate, feedUtil, feedRate);
            _accept(hour, utilBps, rateBps, l1, l1Known, fromPre);
            return;
        }
        if (preOk) {
            _accept(hour, preUtil, preRate, l1, l1Known, true);
            return;
        }
        if (feedOk) {
            _accept(hour, feedUtil, feedRate, l1, l1Known, false);
            return;
        }
        if (!sampleRecorded[hour]) {
            uint8 gap = _markEmpty(hour, l1, l1Known, false);
            if (gap != 0) _freezeLive(hour, gap);
        }
    }

    function accrue(uint256 seriesId) external {
        require(seriesId < seriesCount, "series");
        Series storage s = series[seriesId];
        if (s.state == STATE_FROZEN || s.state == STATE_SETTLED) return;
        Life storage lived = life[seriesId];
        uint64 hour = lived.nextHour;
        if (hour < s.startHour || hour >= s.endHour) {
            if (hour >= s.endHour) _settle(seriesId);
            return;
        }
        if (!sampleRecorded[hour]) return;
        if (s.state == STATE_OPEN) s.state = STATE_ACTIVE;
        uint256 pay = sampleGood[hour] ? _bookPay(seriesId, hour) : 0;
        if (sampleGood[hour]) {
            lived.lastGoodHour = hour;
            lived.hasLastGood = true;
        }
        lived.nextHour = hour + 1;
        emit Accrued(seriesId, hour, pay);
        if (lived.nextHour == s.endHour) _settle(seriesId);
    }

    function unfreeze(uint256 seriesId) external {
        require(msg.sender == guardian, "guardian");
        Series storage s = series[seriesId];
        require(seriesId < seriesCount && s.state == STATE_FROZEN, "state");
        Life storage lived = life[seriesId];
        uint64 nowHour = uint64(block.timestamp / 3600);
        require(nowHour <= lived.frozenAt + UNFREEZE_WINDOW, "window");
        require(_recovered(lived.frozenAt), "hours");
        s.state = lived.nextHour > s.startHour ? STATE_ACTIVE : STATE_OPEN;
        if (marketFrozen && marketFrozenAt == lived.frozenAt) marketFrozen = false;
    }

    function settle(uint256 seriesId) external {
        require(seriesId < seriesCount, "series");
        Series storage s = series[seriesId];
        require(s.state != STATE_SETTLED, "state");
        Life storage lived = life[seriesId];
        uint64 nowHour = uint64(block.timestamp / 3600);
        if (s.state == STATE_FROZEN && nowHour > lived.frozenAt + UNFREEZE_WINDOW) {
            _settle(seriesId);
            return;
        }
        require(lived.nextHour >= s.endHour, "open");
        _settle(seriesId);
    }

    function redeem(uint256 longId) external {
        Position storage lng = positions[longId];
        require(lng.open && lng.side == 1, "position");
        require(series[lng.seriesId].state == STATE_SETTLED, "open");
        Position storage sht = positions[lng.pairId];
        uint256 pay = lng.claim;
        require(pay <= sht.collateral, "over");
        uint256 rest = sht.collateral - pay;
        address longHolder = lng.holder;
        address shortHolder = sht.holder;
        uint256 shortId = lng.pairId;
        lng.open = false;
        sht.open = false;
        seriesCollateral[lng.seriesId] -= sht.collateral;
        seriesOwed[lng.seriesId] -= pay;
        if (pay > 0) require(collateralToken.transfer(longHolder, pay), "pay");
        if (rest > 0) require(collateralToken.transfer(shortHolder, rest), "return");
        emit Redeemed(longId, longHolder, shortHolder, pay, rest);
        emit TransferSingle(msg.sender, longHolder, address(0), longId, 1);
        emit TransferSingle(msg.sender, shortHolder, address(0), shortId, 1);
    }

    function supportsInterface(bytes4 interfaceId) external pure returns (bool) {
        return interfaceId == ERC165 || interfaceId == ERC1155;
    }

    function balanceOf(address account, uint256 id) public view returns (uint256) {
        require(account != address(0), "zero");
        if (id >= positionCount) return 0;
        Position storage p = positions[id];
        if (p.open && p.holder == account) return 1;
        return 0;
    }

    function balanceOfBatch(address[] calldata accounts, uint256[] calldata ids)
        external
        view
        returns (uint256[] memory balances)
    {
        require(accounts.length == ids.length, "length");
        balances = new uint256[](accounts.length);
        for (uint256 i = 0; i < accounts.length; i++) {
            balances[i] = balanceOf(accounts[i], ids[i]);
        }
    }

    function setApprovalForAll(address operator, bool approved) external {
        require(operator != msg.sender, "self");
        isApprovedForAll[msg.sender][operator] = approved;
        emit ApprovalForAll(msg.sender, operator, approved);
    }

    function transferPosition(uint256 id, address to) external {
        _move(msg.sender, to, id, 1);
        _notifyOne(msg.sender, to, id, 1, "");
    }

    function safeTransferFrom(address from, address to, uint256 id) external {
        _move(from, to, id, 1);
        _notifyOne(from, to, id, 1, "");
    }

    function safeTransferFrom(address from, address to, uint256 id, uint256 amount, bytes calldata data) external {
        _move(from, to, id, amount);
        _notifyOne(from, to, id, amount, data);
    }

    function safeBatchTransferFrom(
        address from,
        address to,
        uint256[] calldata ids,
        uint256[] calldata amounts,
        bytes calldata data
    ) external {
        if (!transfersEnabled) revert TransfersDisabled();
        require(ids.length == amounts.length, "length");
        require(to != address(0), "to");
        require(from == msg.sender || isApprovedForAll[from][msg.sender], "operator");
        for (uint256 i = 0; i < ids.length; i++) {
            require(amounts[i] == 1, "amount");
            Position storage p = positions[ids[i]];
            require(p.open && p.holder == from, "holder");
            p.holder = to;
        }
        emit TransferBatch(msg.sender, from, to, ids, amounts);
        _notifyBatch(from, to, ids, amounts, data);
    }

    function _move(address from, address to, uint256 id, uint256 amount) internal {
        if (!transfersEnabled) revert TransfersDisabled();
        require(amount == 1, "amount");
        Position storage p = positions[id];
        require(p.open && p.holder == from, "holder");
        require(from == msg.sender || isApprovedForAll[from][msg.sender], "operator");
        require(to != address(0), "to");
        p.holder = to;
        emit TransferSingle(msg.sender, from, to, id, 1);
    }

    function _notifyOne(address from, address to, uint256 id, uint256 amount, bytes memory data) internal {
        if (to.code.length == 0) return;
        (bool ok, bytes memory ret) =
            to.call(abi.encodeWithSelector(IERC1155Receiver.onERC1155Received.selector, msg.sender, from, id, amount, data));
        require(ok && ret.length >= 32 && abi.decode(ret, (bytes4)) == RECEIVED, "receiver");
    }

    function _notifyBatch(
        address from,
        address to,
        uint256[] calldata ids,
        uint256[] calldata amounts,
        bytes calldata data
    ) internal {
        if (to.code.length == 0) return;
        (bool ok, bytes memory ret) = to.call(
            abi.encodeWithSelector(IERC1155Receiver.onERC1155BatchReceived.selector, msg.sender, from, ids, amounts, data)
        );
        require(ok && ret.length >= 32 && abi.decode(ret, (bytes4)) == BATCH_RECEIVED, "receiver");
    }

    function feedDigest(
        uint256 hour,
        uint256 supplied,
        uint256 borrowed,
        uint256 rateBps,
        uint256 fundingWad
    ) public view returns (bytes32) {
        return keccak256(abi.encode(TAG, block.chainid, hour, supplied, borrowed, rateBps, fundingWad));
    }

    function previewMean(uint64 hour) external view returns (bool ok, uint256 meanBps) {
        return _mean(hour);
    }

    function _accept(uint64 hour, uint64 utilBps, uint64 rateBps, uint64 l1, bool l1Known, bool fromPre) internal {
        uint8 gap = _gap(hour);
        if (sampleGood[hour]) {
            uint256 stored = samples[hour].rateBps;
            uint256 gapW = _gapWad(stored * WAD / BPS, uint256(rateBps) * WAD / BPS);
            if (gapW < TOLERANCE) {
                if (fromPre && !sampleFromPrecompile[hour]) {
                    _write(hour, utilBps, rateBps, l1, l1Known, true);
                }
            } else if (rateBps < stored) {
                _write(hour, utilBps, rateBps, l1, l1Known, fromPre);
            }
        } else {
            _write(hour, utilBps, rateBps, l1, l1Known, fromPre);
        }
        _touchHour(hour);
        if (gap != 0) _freezeLive(hour, gap);
    }

    function _reject(uint64 hour, uint64 l1, bool l1Known, bool fromPre, uint8 reason) internal {
        if (!sampleGood[hour]) _markEmpty(hour, l1, l1Known, fromPre);
        else _touchHour(hour);
        _freezeLive(hour, reason);
    }

    function _write(uint64 hour, uint64 utilBps, uint64 rateBps, uint64 l1, bool l1Known, bool fromPre) internal {
        samples[hour] = Sample({hourId: hour, utilBps: utilBps, rateBps: rateBps, l1Block: l1Known ? l1 : 0});
        sampleRecorded[hour] = true;
        sampleGood[hour] = true;
        sampleFromPrecompile[hour] = fromPre;
        sampleL1Known[hour] = l1Known;
        emit Sampled(hour, utilBps, rateBps, l1Known ? l1 : 0, fromPre);
    }

    function _markEmpty(uint64 hour, uint64 l1, bool l1Known, bool fromPre) internal returns (uint8 gap) {
        sampleRecorded[hour] = true;
        sampleGood[hour] = false;
        sampleFromPrecompile[hour] = fromPre;
        sampleL1Known[hour] = l1Known;
        samples[hour].hourId = hour;
        samples[hour].utilBps = 0;
        samples[hour].rateBps = 0;
        samples[hour].l1Block = l1Known ? l1 : 0;
        gap = _gap(hour);
        _touchHour(hour);
    }

    function _touchHour(uint64 hour) internal {
        if (!hasLastHour || hour > lastHour) {
            hasLastHour = true;
            lastHour = hour;
        }
    }

    function _gap(uint64 hour) internal view returns (uint8) {
        if (hasLastHour && hour > lastHour + GAP_HOURS) return FAIL_GAP;
        return 0;
    }

    function _eventReason(uint8 fail) internal pure returns (uint8) {
        if (fail == FAIL_GAP) return FREEZE_GAP;
        if (fail == FAIL_MODEL) return FREEZE_MODEL;
        return FREEZE_FORMULA;
    }

    function _freezeLive(uint64 hour, uint8 fail) internal {
        uint8 reason = _eventReason(fail);
        marketFrozen = true;
        marketFrozenAt = hour;
        marketFreezeReason = reason;
        uint256 n = liveSeries.length;
        for (uint256 i = 0; i < n; i++) {
            uint256 id = liveSeries[i];
            Series storage s = series[id];
            if (s.state == STATE_SETTLED) continue;
            bool changed = s.state != STATE_FROZEN || life[id].frozenAt != hour || life[id].freezeReason != reason;
            s.state = STATE_FROZEN;
            life[id].frozenAt = hour;
            life[id].freezeReason = reason;
            if (changed) emit Frozen(id, hour, reason);
        }
    }

    function _recovered(uint64 frozenAt) internal view returns (bool) {
        uint64 first = frozenAt + 1;
        uint64 second = frozenAt + 2;
        return sampleGood[first] && sampleGood[second];
    }

    function _settle(uint256 seriesId) internal {
        Series storage s = series[seriesId];
        if (s.state == STATE_SETTLED) return;
        s.state = STATE_SETTLED;
        Life storage lived = life[seriesId];
        emit Settled(seriesId, lived.lastGoodHour, lived.hasLastGood, seriesOwed[seriesId]);
    }

    function _bookPay(uint256 seriesId, uint64 hour) internal returns (uint256 added) {
        uint256[] storage ids = _seriesPositions[seriesId];
        for (uint256 i = 0; i < ids.length; i++) {
            Position storage lng = positions[ids[i]];
            if (!lng.open || lng.side != 1 || hour < lng.startHour) continue;
            Position storage sht = positions[lng.pairId];
            uint256 pay = _hourPay(seriesId, hour, lng.notional);
            uint256 room = sht.collateral - lng.claim;
            if (pay > room) pay = room;
            lng.claim += pay;
            added += pay;
        }
        seriesOwed[seriesId] += added;
    }

    function _hourPay(uint256 seriesId, uint64 hour, uint256 notional) internal view returns (uint256) {
        (bool ok, uint256 mean) = _mean(hour);
        if (!ok) return 0;
        Series storage s = series[seriesId];
        if (s.kind == KIND_DIGITAL) {
            if (mean <= s.strikeBps) return 0;
            return notional * uint256(digitalBps[seriesId]) / BPS;
        }
        uint256 capped = mean > s.ceilBps ? s.ceilBps : mean;
        if (capped <= s.strikeBps) return 0;
        return notional * (capped - s.strikeBps) / BPS / HOURS_PER_YEAR;
    }

    function _mean(uint64 hour) internal view returns (bool ok, uint256 meanBps) {
        if (uint256(hour) + 1 < TRAILING) return (false, 0);
        uint256 sum;
        for (uint256 i = 0; i < TRAILING; i++) {
            uint64 at = hour - uint64(TRAILING - 1 - i);
            if (!sampleGood[at]) return (false, 0);
            sum += samples[at].rateBps;
        }
        return (true, sum / TRAILING);
    }

    function _lower(uint64 preUtil, uint64 preRate, uint64 feedUtil, uint64 feedRate)
        internal
        pure
        returns (uint64 utilBps, uint64 rateBps, bool fromPre)
    {
        uint256 gap = _gapWad(uint256(preRate) * WAD / BPS, uint256(feedRate) * WAD / BPS);
        if (gap < TOLERANCE || preRate <= feedRate) return (preUtil, preRate, true);
        return (feedUtil, feedRate, false);
    }

    function _gapWad(uint256 a, uint256 b) internal pure returns (uint256) {
        return a > b ? a - b : b - a;
    }

    function _precompile() internal view returns (bool decoded, uint64 utilBps, uint64 rateBps, uint8 fail) {
        (bool success, bytes memory ret) = PRECOMPILE.staticcall{gas: PRECOMPILE_GAS}(abi.encode(TOKEN_INDEX));
        if (!success || ret.length != 256) return (false, 0, 0, 0);
        return _classify(ret);
    }

    function _classify(bytes memory ret) internal pure returns (bool decoded, uint64 utilBps, uint64 rateBps, uint8 fail) {
        uint256 quoted = _word(ret, 0);
        uint256 utilWord = _word(ret, 3);
        uint256 supplied = _word(ret, 6);
        uint256 borrowed = _word(ret, 7);
        if (supplied == 0 || supplied >= 2**64 || borrowed >= 2**64 || quoted >= 2**64 || utilWord >= 2**64) {
            return (true, 0, 0, FAIL_FORMULA);
        }
        if (quoted > BPS || borrowed * BPS / supplied != utilWord) return (true, 0, 0, FAIL_FORMULA);
        (bool ok, uint256 formula) = _formula(supplied, borrowed);
        if (!ok) return (true, 0, 0, FAIL_FORMULA);
        if (_gapWad(formula, quoted * WAD / BPS) >= TOLERANCE) {
            return (true, 0, 0, utilWord <= KINK_BPS ? FAIL_MODEL : FAIL_FORMULA);
        }
        return (true, uint64(utilWord), uint64(formula * BPS / WAD), 0);
    }

    function _l1() internal view returns (uint64 blockNo, bool known) {
        (bool success, bytes memory ret) = L1_BLOCK.staticcall("");
        if (!success || ret.length < 32) return (0, false);
        uint256 word = abi.decode(ret, (uint256));
        if (word >= 2**64) return (0, false);
        return (uint64(word), true);
    }

    function _feed(uint64 hour, Feed calldata feed) internal view returns (bool ok, uint64 utilBps, uint64 rateBps) {
        if (!feed.present || feed.supplied == 0) return (false, 0, 0);
        if (feed.supplied >= 2**64 || feed.borrowed >= 2**64 || feed.rateBps > BPS) return (false, 0, 0);
        uint256 utilWord = feed.borrowed * BPS / feed.supplied;
        (bool formulaOk, uint256 formula) = _formula(feed.supplied, feed.borrowed);
        if (!formulaOk) return (false, 0, 0);
        if (_gapWad(formula, feed.rateBps * WAD / BPS) >= TOLERANCE) return (false, 0, 0);
        bytes32 digest = feedDigest(hour, feed.supplied, feed.borrowed, feed.rateBps, feed.fundingWad);
        address a = _recover(digest, feed.v0, feed.r0, feed.s0);
        address b = _recover(digest, feed.v1, feed.r1, feed.s1);
        address c = _recover(digest, feed.v2, feed.r2, feed.s2);
        if (_distinct(a, b, c) < 2) return (false, 0, 0);
        return (true, uint64(utilWord), uint64(formula * BPS / WAD));
    }

    function _distinct(address a, address b, address c) internal view returns (uint256 n) {
        if (_isSigner(a)) n++;
        if (_isSigner(b) && b != a) n++;
        if (_isSigner(c) && c != a && c != b) n++;
    }

    function _isSigner(address who) internal view returns (bool) {
        if (who == address(0)) return false;
        return who == feedSigners[0] || who == feedSigners[1] || who == feedSigners[2];
    }

    function _recover(bytes32 digest, uint8 v, bytes32 r, bytes32 s) internal pure returns (address) {
        if (uint256(s) == 0 || uint256(s) > HALF_N || uint256(r) == 0) return address(0);
        if (v != 27 && v != 28) return address(0);
        return ecrecover(digest, v, r, s);
    }

    function _formula(uint256 supplied, uint256 borrowed) internal pure returns (bool ok, uint256 rate) {
        if (supplied == 0) return (false, 0);
        uint256 u = borrowed * WAD / supplied;
        rate = BASE;
        if (u > KINK) {
            rate = BASE + (u - KINK) * 475 / 100;
            if (rate > WAD) rate = WAD;
        }
        ok = true;
    }

    function _word(bytes memory ret, uint256 i) internal pure returns (uint256 w) {
        assembly ("memory-safe") {
            w := mload(add(add(ret, 0x20), mul(i, 0x20)))
        }
    }

    function _divUp(uint256 num, uint256 den) internal pure returns (uint256) {
        if (num == 0) return 0;
        return (num + den - 1) / den;
    }
}
