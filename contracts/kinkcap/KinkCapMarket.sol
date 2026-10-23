// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

/// @title Kink Cap market
/// @notice Fully collateralized hourly caps on Hyperliquid's USDC borrow
///         rate. Positions are records. They move only when the immutable
///         `transfersEnabled` flag this deployment was constructed with is
///         true. There is no key that can move somebody else's collateral.
///
///         The rate source is precompile 0x812, verified against Hyperliquid's
///         L1Read.sol: eight uint64 words, ABI-encoded, integer basis points
///         and raw token units. It is the current reserve, so a past hour is
///         never read from it. The fallback is this market's own 2-of-3 feed.
///         When the two disagree by more than the truncation tolerance, the
///         hour settles at the lower rate. When neither is available the hour
///         pays 0 and the rate is not recorded as a measurement.
interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

contract KinkCapMarket {
    uint256 public constant CHAIN_ID = 998;
    /// Precompile 0x812. Not a configurable reader.
    address public constant PRECOMPILE = address(uint160(0x812));

    uint256 public constant WAD = 1e18;
    uint256 public constant BASE = 0.05e18;
    uint256 public constant KINK = 0.8e18;
    /// (4.75 + 1) / 10000, in wad. One floored basis point of utilization
    /// moves the rate by the slope, and the rate word is floored again.
    uint256 public constant TOLERANCE = 575e12;
    uint256 public constant HOURS_PER_YEAR = 8760;
    uint256 public constant TRAILING = 6;
    /// Documented USDC borrow cap, raw token units (1e8 per token).
    uint256 public constant BORROW_CAP = 500_000_000 * 10**8;
    /// Design choice: a cap-hit hour pays at 98% of that cap.
    uint256 public constant CAP_HIT_NUM = 98;
    uint256 public constant CAP_HIT_DEN = 100;

    bytes32 public constant TAG = keccak256("HRCFeed.v1");
    bytes32 public constant SRC_CGC = keccak256("CGC");
    bytes32 public constant SRC_RXB = keccak256("RXB");
    bytes32 public constant SRC_T2C = keccak256("T2C");
    bytes32 public constant SRC_HRC = keccak256("HRC");

    uint256 private constant HALF_N =
        0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0;

    error WrongChain();
    error TransfersDisabled();

    IERC20 public immutable collateralToken;
    uint64 public immutable usdcTokenIndex;
    bool public immutable transfersEnabled;

    address[3] public feedSigners;
    /// Post-quantum public keys, stored beside each signer. Not verified.
    bytes32[3] public pqSlots;

    struct Series {
        bool exists;
        uint8 kind;
        uint256 strikeOrDeltaWad;
        uint256 digitalPerHour;
        uint256 startHour;
        uint256 duration;
        uint256 nextHour;
        uint256 oi;
        uint256 perAddressCap;
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
    }

    struct Settled {
        bool done;
        bool hadRate;
        bool fundingKnown;
        uint8 source;
        uint256 rateWad;
        uint256 borrowed;
        uint256 fundingWad;
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
    mapping(uint256 => Series) public series;
    mapping(uint256 => Position) public positions;
    mapping(uint256 => Settled) private _settled;
    mapping(uint256 => mapping(bytes32 => Proposal)) public proposals;
    mapping(bytes32 => address) public sourceAuthority;
    mapping(uint256 => uint256) public seriesCollateral;
    mapping(uint256 => uint256) public seriesOwed;
    mapping(uint256 => mapping(address => uint256)) public mintedTo;
    mapping(uint256 => uint256[]) private _seriesPositions;

    event SeriesOpened(uint256 indexed seriesId, uint8 kind, uint256 maxNotional, uint256 startHour, uint256 duration);
    event PositionOpened(
        uint256 indexed seriesId,
        uint256 longId,
        uint256 shortId,
        address longHolder,
        address shortHolder,
        uint256 notional,
        uint256 collateral
    );
    event HourSettled(
        uint256 indexed seriesId,
        uint256 indexed hour,
        bool hadRate,
        uint8 source,
        uint256 rateWad
    );
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

    constructor(
        address collateral,
        uint64 usdcIndex,
        address[3] memory signers,
        bool transfers,
        address[4] memory authorities
    ) {
        if (block.chainid != CHAIN_ID) revert WrongChain();
        collateralToken = IERC20(collateral);
        usdcTokenIndex = usdcIndex;
        transfersEnabled = transfers;
        feedSigners = signers;
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

    function openSeries(
        uint8 kind,
        uint256 strikeOrDeltaWad,
        uint256 digitalPerHour,
        uint256 startHour,
        uint256 duration,
        uint256 maxNotional,
        uint256 perAddressCap
    ) external returns (uint256 id) {
        require(kind <= 2, "kind");
        require(duration > 0, "duration");
        require(strikeOrDeltaWad <= WAD, "strike");
        id = seriesCount++;
        series[id] = Series({
            exists: true,
            kind: kind,
            strikeOrDeltaWad: strikeOrDeltaWad,
            digitalPerHour: digitalPerHour,
            startHour: startHour,
            duration: duration,
            nextHour: startHour,
            oi: 0,
            perAddressCap: perAddressCap
        });
        proposals[id][SRC_HRC] = Proposal({exists: true, cap: maxNotional});
        emit SeriesOpened(id, kind, maxNotional, startHour, duration);
    }

    function effectiveCap(uint256 seriesId) public view returns (bool any, uint256 cap) {
        bytes32[4] memory srcs = [SRC_CGC, SRC_RXB, SRC_T2C, SRC_HRC];
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
        require(series[seriesId].exists, "series");
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
        require(s.exists, "series");
        require(notional > 0, "notional");
        require(longHolder != address(0), "holder");
        uint256 end = s.startHour + s.duration;
        require(s.nextHour < end, "closed");
        (bool any, uint256 cap) = effectiveCap(seriesId);
        require(any, "no cap");
        require(s.oi + notional <= cap, "cap");
        require(mintedTo[seriesId][longHolder] + notional <= s.perAddressCap, "address cap");
        uint256 need = collateralFor(s.kind, s.strikeOrDeltaWad, s.digitalPerHour, notional, end - s.nextHour);
        require(collateralToken.transferFrom(msg.sender, address(this), need), "pull");
        s.oi += notional;
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
            open: true
        });
        positions[shortId] = Position({
            holder: msg.sender,
            seriesId: seriesId,
            side: 2,
            notional: notional,
            collateral: need,
            claim: 0,
            pairId: longId,
            open: true
        });
        _seriesPositions[seriesId].push(longId);
        emit PositionOpened(seriesId, longId, shortId, longHolder, msg.sender, notional, need);
    }

    function collateralFor(
        uint8 kind,
        uint256 strikeOrDeltaWad,
        uint256 digitalPerHour,
        uint256 notional,
        uint256 hours_
    ) public pure returns (uint256) {
        if (hours_ == 0 || notional == 0) return 0;
        if (kind == 2) return notional * digitalPerHour * hours_;
        if (strikeOrDeltaWad >= WAD) return 0;
        uint256 num = notional * (WAD - strikeOrDeltaWad) * hours_;
        uint256 den = HOURS_PER_YEAR * WAD;
        return (num + den - 1) / den;
    }

    function settleHour(uint256 seriesId, Feed calldata feed) external {
        Series storage s = series[seriesId];
        require(s.exists, "series");
        uint256 hour = s.nextHour;
        require(hour < s.startHour + s.duration, "done");
        _record(seriesId, hour, feed);
        s.nextHour = hour + 1;
        seriesOwed[seriesId] += _accrue(seriesId, hour);
        Settled memory st = _settled[_key(seriesId, hour)];
        emit HourSettled(seriesId, hour, st.hadRate, st.source, st.rateWad);
    }

    function _record(uint256 seriesId, uint256 hour, Feed calldata feed) internal {
        (bool preOk, uint256 preRate, uint256 preBor) = _precompile(hour);
        (bool feedOk, uint256 feedRate, uint256 feedBor) = _feed(hour, feed);
        (bool hadRate, uint8 source, uint256 rate, uint256 borrowed) =
            _choose(preOk, preRate, preBor, feedOk, feedRate, feedBor);
        _settled[_key(seriesId, hour)] = Settled({
            done: true,
            hadRate: hadRate,
            fundingKnown: feedOk,
            source: source,
            rateWad: rate,
            borrowed: borrowed,
            fundingWad: feedOk ? feed.fundingWad : 0
        });
    }

    function redeem(uint256 longId) external {
        Position storage lng = positions[longId];
        require(lng.open && lng.side == 1, "position");
        Series storage s = series[lng.seriesId];
        require(s.nextHour == s.startHour + s.duration, "open");
        Position storage sht = positions[lng.pairId];
        uint256 pay = lng.claim;
        require(pay <= sht.collateral, "over");
        uint256 rest = sht.collateral - pay;
        lng.open = false;
        sht.open = false;
        seriesCollateral[lng.seriesId] -= sht.collateral;
        seriesOwed[lng.seriesId] -= pay;
        if (pay > 0) require(collateralToken.transfer(lng.holder, pay), "pay");
        if (rest > 0) require(collateralToken.transfer(sht.holder, rest), "return");
        emit Redeemed(longId, lng.holder, sht.holder, pay, rest);
    }

    function transferPosition(uint256 id, address to) external {
        if (!transfersEnabled) revert TransfersDisabled();
        Position storage p = positions[id];
        require(p.open && p.holder == msg.sender, "holder");
        require(to != address(0), "to");
        p.holder = to;
    }

    function safeTransferFrom(address from, address to, uint256 id) external {
        if (!transfersEnabled) revert TransfersDisabled();
        Position storage p = positions[id];
        require(p.open && p.holder == from && msg.sender == from, "holder");
        require(to != address(0), "to");
        p.holder = to;
    }

    function settledHour(uint256 seriesId, uint256 hour) external view returns (Settled memory) {
        return _settled[_key(seriesId, hour)];
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

    function expWad(uint256 x) public pure returns (uint256) {
        uint256 sum = WAD;
        uint256 term = WAD;
        for (uint256 i = 1; i <= 12; i++) {
            term = term * x / WAD / i;
            sum += term;
        }
        return sum;
    }

    function previewCarryStrike(uint256 fundingWad, uint256 deltaWad) public pure returns (uint256) {
        return _carryStrike(fundingWad, deltaWad);
    }

    function formulaRate(uint256 supplied, uint256 borrowed) public pure returns (bool ok, uint256 rate) {
        return _formula(supplied, borrowed);
    }

    function _accrue(uint256 seriesId, uint256 hour) internal returns (uint256 added) {
        uint256[] storage ids = _seriesPositions[seriesId];
        for (uint256 i = 0; i < ids.length; i++) {
            Position storage lng = positions[ids[i]];
            if (!lng.open || lng.side != 1) continue;
            Position storage sht = positions[lng.pairId];
            uint256 pay = _hourPay(seriesId, hour, lng.notional);
            uint256 room = sht.collateral - lng.claim;
            if (pay > room) pay = room;
            lng.claim += pay;
            added += pay;
        }
    }

    function _hourPay(uint256 seriesId, uint256 hour, uint256 notional) internal view returns (uint256) {
        Series storage s = series[seriesId];
        Settled memory cur = _settled[_key(seriesId, hour)];
        if (s.kind == 2) {
            if (!cur.hadRate) return 0;
            if (cur.borrowed * CAP_HIT_DEN < BORROW_CAP * CAP_HIT_NUM) return 0;
            return notional * s.digitalPerHour;
        }
        if (hour < s.startHour + (TRAILING - 1)) return 0;
        uint256 sum;
        for (uint256 i = 0; i < TRAILING; i++) {
            Settled memory st = _settled[_key(seriesId, hour - (TRAILING - 1) + i)];
            if (!st.hadRate) return 0;
            sum += st.rateWad;
        }
        uint256 mean = sum / TRAILING;
        uint256 strike = s.strikeOrDeltaWad;
        if (s.kind == 1) {
            if (!cur.fundingKnown) return 0;
            strike = _carryStrike(cur.fundingWad, s.strikeOrDeltaWad);
        }
        if (mean <= strike) return 0;
        return notional * (mean - strike) / HOURS_PER_YEAR / WAD;
    }

    function _carryStrike(uint256 fundingWad, uint256 deltaWad) internal pure returns (uint256) {
        if (fundingWad >= 0.7e18) return WAD;
        uint256 e = expWad(fundingWad);
        uint256 matched = e > WAD ? e - WAD : 0;
        uint256 strike = matched + deltaWad;
        if (strike > WAD) return WAD;
        return strike;
    }

    function _choose(
        bool preOk,
        uint256 preRate,
        uint256 preBor,
        bool feedOk,
        uint256 feedRate,
        uint256 feedBor
    ) internal pure returns (bool hadRate, uint8 source, uint256 rate, uint256 borrowed) {
        if (preOk && feedOk) {
            uint256 gap = preRate > feedRate ? preRate - feedRate : feedRate - preRate;
            if (gap <= TOLERANCE) {
                source = 1;
                rate = preRate;
            } else if (preRate <= feedRate) {
                source = 3;
                rate = preRate;
            } else {
                source = 3;
                rate = feedRate;
            }
            borrowed = preBor < feedBor ? preBor : feedBor;
            hadRate = true;
        } else if (preOk) {
            hadRate = true;
            source = 1;
            rate = preRate;
            borrowed = preBor;
        } else if (feedOk) {
            hadRate = true;
            source = 2;
            rate = feedRate;
            borrowed = feedBor;
        }
    }

    function _precompile(uint256 hour) internal view returns (bool ok, uint256 rate, uint256 borrowed) {
        if (hour != block.timestamp / 3600) return (false, 0, 0);
        (bool success, bytes memory ret) = PRECOMPILE.staticcall(abi.encode(uint256(usdcTokenIndex)));
        if (!success || ret.length != 256) return (false, 0, 0);
        uint256 supplied = _word(ret, 6);
        borrowed = _word(ret, 7);
        uint256 rateBps = _word(ret, 0);
        uint256 utilWord = _word(ret, 3);
        if (supplied == 0 || supplied >= 2**64 || borrowed >= 2**64 || rateBps >= 2**64 || utilWord >= 2**64) {
            return (false, 0, 0);
        }
        if (borrowed * 10000 / supplied != utilWord) return (false, 0, 0);
        if (!_quoteOk(supplied, borrowed, rateBps)) return (false, 0, 0);
        (, rate) = _formula(supplied, borrowed);
        ok = true;
    }

    function _feed(uint256 hour, Feed calldata feed) internal view returns (bool ok, uint256 rate, uint256 borrowed) {
        if (!feed.present) return (false, 0, 0);
        if (feed.supplied == 0 || !_quoteOk(feed.supplied, feed.borrowed, feed.rateBps)) return (false, 0, 0);
        bytes32 digest = feedDigest(hour, feed.supplied, feed.borrowed, feed.rateBps, feed.fundingWad);
        address a = _recover(digest, feed.v0, feed.r0, feed.s0);
        address b = _recover(digest, feed.v1, feed.r1, feed.s1);
        address c = _recover(digest, feed.v2, feed.r2, feed.s2);
        if (_distinct(a, b, c) < 2) return (false, 0, 0);
        (, rate) = _formula(feed.supplied, feed.borrowed);
        borrowed = feed.borrowed;
        ok = true;
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

    function _quoteOk(uint256 supplied, uint256 borrowed, uint256 rateBps) internal pure returns (bool) {
        (bool ok, uint256 rate) = _formula(supplied, borrowed);
        if (!ok || rateBps > 10000) return false;
        uint256 quoted = rateBps * WAD / 10000;
        uint256 gap = rate > quoted ? rate - quoted : quoted - rate;
        return gap <= TOLERANCE;
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
        assembly {
            w := mload(add(add(ret, 0x20), mul(i, 0x20)))
        }
    }

    function _key(uint256 seriesId, uint256 hour) internal pure returns (uint256) {
        return (seriesId << 64) | hour;
    }
}
