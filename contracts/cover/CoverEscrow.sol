// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

/**
 * Cover escrow for two testnet drafts: ADL re-entry cover and parametric
 * stop-gap cover. They share this contract, the collateral, and the claim
 * path. A readings contract supplies the mark, the oracle and the position.
 *
 * Not offered to customers. Licence review pending. Do not enable in production.
 *
 * Cash-settled cover on a price outcome is a derivative or insurance under
 * MiFID II. No EU customer may use this until an AFM licence is in force.
 * The constructor reverts on every chain except HyperEVM testnet (998) and
 * Base Sepolia (84532). There is no mainnet configuration in this file.
 *
 * What the payout is. Prices and `size` are integers. `SIZE_SCALE` is 1e8.
 * A long ADL pays size * max(0, mark - adlPx - deductible) / SIZE_SCALE.
 * A short ADL swaps the two prices. A long stop-gap pays
 * size * max(0, trigger - oracle - deductible) / SIZE_SCALE, and a short
 * stop-gap swaps those two. The result is capped at the cap the underwriter
 * funded. A zero gap is a real "nothing is owed", and the claim reverts
 * rather than recording a payment. An unreadable price is not a zero price:
 * the claim reverts and the policy stays active.
 *
 * The mark and oracle precompiles return a price and no clock. The readings
 * contract's `updatedAt` is the observation time, and a claim also has to
 * land inside [trigger + delta, trigger + delta + MAX_STALENESS]. Outside
 * that window the current price is not the price at trigger + delta, so the
 * claim reverts. A price of zero is unreadable: a short would otherwise be
 * paid as if the market had gone to nothing.
 *
 * ADL additionally reads the position precompile. The covered magnitude has
 * to shrink. A position the readings contract cannot read does not count as
 * a flat position. `int64` minimum cannot be negated and is unreadable too.
 *
 * The underwriter is one address, set at construction, and nothing in this
 * contract can change it, the readings contract, the premium token or the
 * asset allowlist. A policy that the underwriter does not fund cannot be
 * claimed. `size` (the payout multiplier) and `coveredSzi` (the position
 * magnitude that must shrink) are the terms that underwriter funds. This
 * file does not convert Hyperliquid szDecimals; the cap is the most the
 * escrow will pay.
 *
 * Stop-gap claims do not take a fill price. The trigger is stored at open
 * and the oracle is read here. Passing a fill figure reverts.
 *
 * No function here is a promise that a loss will be made good. The cap, the
 * deductible and a failed reading are all ordinary outcomes.
 */

interface ICoverReadings {
    function mark(uint32 asset) external view returns (uint64 price, uint64 updatedAt);

    function oraclePx(uint32 asset) external view returns (uint64 price, uint64 updatedAt);

    function positionSzi(address user, uint32 asset) external view returns (int64 szi, bool readable);
}

contract CoverEscrow {
    uint256 public constant HYPEREVM_TESTNET = 998;
    uint256 public constant BASE_SEPOLIA = 84532;
    uint256 public constant SIZE_SCALE = 1e8;
    uint256 public constant ACTIVATION_COOLDOWN = 1 hours;
    uint256 public constant MIN_HOLD = 1 hours;
    uint256 public constant CHALLENGE_WINDOW = 30 minutes;
    uint256 public constant MAX_STALENESS = 120;

    uint8 public constant KIND_ADL = 1;
    uint8 public constant KIND_STOPGAP = 2;
    uint8 public constant SIDE_LONG = 1;
    uint8 public constant SIDE_SHORT = 2;

    uint8 public constant ST_PENDING = 1;
    uint8 public constant ST_ACTIVE = 2;
    uint8 public constant ST_CLAIMED = 3;
    uint8 public constant ST_PAID = 4;
    uint8 public constant ST_VOID = 5;
    uint8 public constant ST_SETTLED = 6;

    address public immutable premiumToken;
    ICoverReadings public immutable readings;
    address public immutable underwriter;

    uint256 public nextId = 1;
    uint256 private locked = 1;

    mapping(uint32 => bool) public allowed;
    mapping(uint256 => Policy) public policies;

    struct Policy {
        address buyer;
        uint8 kind;
        uint8 side;
        uint8 state;
        uint32 asset;
        uint256 size;
        uint256 coveredSzi;
        uint256 triggerPx;
        uint256 deductible;
        uint256 delta;
        uint256 expiry;
        uint256 activeAt;
        uint256 claimReadyAt;
        uint256 payoutCap;
        uint256 premium;
        uint256 collateral;
        uint256 payout;
    }

    // Not named so that its selector starts with 0xef. EIP-3541 rejects new
    // code with that prefix, and a constructor revert is then easy to misread.
    error ChainRefused();
    error ZeroAddress();
    error NoMajors();
    error BadTerms();
    error AssetNotAllowed();
    error TransferFailed();
    error Unreadable();
    error Stale();
    error Window();
    error Cooldown();
    error MinHold();
    error NotBuyer();
    error NotUnderwriter();
    error BadState();
    error BelowCap();
    error NothingToPay();
    error FillNotUsed();
    error PositionIntact();
    error ChallengeFailed();
    error Reentered();
    error Insolvent();
    error NativeRejected();

    event Opened(uint256 indexed id, uint8 kind, address indexed buyer);
    event Funded(uint256 indexed id);
    event ClaimFiled(uint256 indexed id, uint256 payout);
    event Challenged(uint256 indexed id);
    event Paid(uint256 indexed id, uint256 payout);
    event Reclaimed(uint256 indexed id);

    modifier nonReentrant() {
        if (locked != 1) revert Reentered();
        locked = 2;
        _;
        locked = 1;
    }

    constructor(address token, address readings_, address underwriter_, uint32[] memory majors) {
        if (block.chainid != HYPEREVM_TESTNET && block.chainid != BASE_SEPOLIA) revert ChainRefused();
        if (token == address(0) || readings_ == address(0) || underwriter_ == address(0)) revert ZeroAddress();
        if (majors.length == 0) revert NoMajors();
        premiumToken = token;
        readings = ICoverReadings(readings_);
        underwriter = underwriter_;
        for (uint256 i = 0; i < majors.length; i++) {
            allowed[majors[i]] = true;
        }
    }

    receive() external payable {
        revert NativeRejected();
    }

    function quotePayout(
        uint8 kind,
        uint8 side,
        uint256 size,
        uint256 basis,
        uint256 observed,
        uint256 deductible,
        uint256 cap
    ) external pure returns (uint256) {
        return _quote(kind, side, size, basis, observed, deductible, cap);
    }

    function _quote(
        uint8 kind,
        uint8 side,
        uint256 size,
        uint256 basis,
        uint256 observed,
        uint256 deductible,
        uint256 cap
    ) internal pure returns (uint256) {
        if (size == 0 || size > type(uint64).max) revert BadTerms();
        if (basis > type(uint64).max || observed > type(uint64).max || deductible > type(uint64).max) {
            revert BadTerms();
        }
        if (side != SIDE_LONG && side != SIDE_SHORT) revert BadTerms();
        uint256 hi;
        uint256 lo;
        if (kind == KIND_ADL) {
            if (side == SIDE_LONG) {
                hi = observed;
                lo = basis;
            } else {
                hi = basis;
                lo = observed;
            }
        } else if (kind == KIND_STOPGAP) {
            if (side == SIDE_LONG) {
                hi = basis;
                lo = observed;
            } else {
                hi = observed;
                lo = basis;
            }
        } else {
            revert BadTerms();
        }
        if (hi <= lo || hi - lo <= deductible) return 0;
        uint256 raw = size * (hi - lo - deductible) / SIZE_SCALE;
        if (raw > cap) return cap;
        return raw;
    }

    // Eleven arguments plus the reentrancy lock overflow the legacy codegen
    // stack. The tests compile this file with viaIR for that reason.
    function open(
        uint8 kind,
        uint8 side,
        uint32 asset,
        uint256 size,
        uint256 coveredSzi,
        uint256 triggerPx,
        uint256 deductible,
        uint256 payoutCap,
        uint256 delta,
        uint256 expiry,
        uint256 premium
    ) external nonReentrant returns (uint256 id) {
        if (kind != KIND_ADL && kind != KIND_STOPGAP) revert BadTerms();
        if (side != SIDE_LONG && side != SIDE_SHORT) revert BadTerms();
        if (!allowed[asset]) revert AssetNotAllowed();
        if (size == 0 || size > type(uint64).max) revert BadTerms();
        if (deductible > type(uint64).max || triggerPx > type(uint64).max) revert BadTerms();
        if (payoutCap == 0 || premium == 0) revert BadTerms();
        if (delta == 0 || delta > MAX_STALENESS) revert BadTerms();
        uint256 earliest = block.timestamp + ACTIVATION_COOLDOWN + MIN_HOLD + MAX_STALENESS;
        if (expiry <= earliest) revert BadTerms();
        if (kind == KIND_ADL) {
            if (triggerPx != 0 || coveredSzi == 0) revert BadTerms();
        } else if (triggerPx == 0 || coveredSzi != 0) {
            revert BadTerms();
        }

        id = nextId;
        nextId = id + 1;
        Policy storage p = policies[id];
        p.buyer = msg.sender;
        p.kind = kind;
        p.side = side;
        p.asset = asset;
        p.size = size;
        p.coveredSzi = coveredSzi;
        p.triggerPx = triggerPx;
        p.deductible = deductible;
        p.payoutCap = payoutCap;
        p.delta = delta;
        p.expiry = expiry;
        p.premium = premium;
        p.state = ST_PENDING;
        _take(msg.sender, premium);
        emit Opened(id, kind, msg.sender);
    }

    function postCollateral(uint256 id, uint256 amount) external nonReentrant {
        if (msg.sender != underwriter) revert NotUnderwriter();
        Policy storage p = policies[id];
        if (p.state != ST_PENDING) revert BadState();
        if (amount < p.payoutCap) revert BelowCap();
        p.collateral = amount;
        p.activeAt = block.timestamp + ACTIVATION_COOLDOWN;
        p.state = ST_ACTIVE;
        _take(msg.sender, amount);
        emit Funded(id);
    }

    function cancel(uint256 id) external nonReentrant {
        Policy storage p = policies[id];
        if (p.buyer != msg.sender) revert NotBuyer();
        if (p.state != ST_PENDING) revert BadState();
        uint256 prem = p.premium;
        p.premium = 0;
        p.state = ST_VOID;
        _give(p.buyer, prem);
    }

    function fileClaim(uint256 id, uint64 adlPx, uint256 triggerTime) external nonReentrant {
        Policy storage p = policies[id];
        if (p.buyer != msg.sender) revert NotBuyer();
        if (p.state != ST_ACTIVE) revert BadState();
        if (block.timestamp < p.activeAt) revert Cooldown();
        if (block.timestamp < p.activeAt + MIN_HOLD) revert MinHold();
        if (triggerTime < p.activeAt || triggerTime >= p.expiry) revert Window();
        uint256 observeAt = triggerTime + p.delta;
        if (block.timestamp < observeAt || block.timestamp > observeAt + MAX_STALENESS) revert Window();

        uint256 basis;
        uint256 observed;
        if (p.kind == KIND_ADL) {
            if (adlPx == 0) revert BadTerms();
            observed = _fresh(p.asset, true);
            _requireReduced(p);
            basis = adlPx;
        } else {
            if (adlPx != 0) revert FillNotUsed();
            observed = _fresh(p.asset, false);
            basis = p.triggerPx;
        }
        uint256 pay = _quote(p.kind, p.side, p.size, basis, observed, p.deductible, p.payoutCap);
        if (pay == 0) revert NothingToPay();
        if (pay > p.collateral) revert Insolvent();
        p.payout = pay;
        p.claimReadyAt = block.timestamp + CHALLENGE_WINDOW;
        p.state = ST_CLAIMED;
        emit ClaimFiled(id, pay);
    }

    function challenge(uint256 id) external nonReentrant {
        Policy storage p = policies[id];
        if (p.state != ST_CLAIMED) revert BadState();
        if (block.timestamp >= p.claimReadyAt) revert Window();
        if (p.kind != KIND_ADL) revert ChallengeFailed();
        (bool ok, bytes memory ret) = address(readings).staticcall(
            abi.encodeWithSelector(ICoverReadings.positionSzi.selector, p.buyer, p.asset)
        );
        if (!ok || ret.length < 64) revert ChallengeFailed();
        (int64 szi, bool readable) = abi.decode(ret, (int64, bool));
        if (!readable) revert ChallengeFailed();
        if (_mag(szi) < p.coveredSzi) revert ChallengeFailed();
        p.state = ST_VOID;
        emit Challenged(id);
    }

    function settle(uint256 id) external nonReentrant {
        Policy storage p = policies[id];
        if (p.state != ST_CLAIMED) revert BadState();
        if (block.timestamp < p.claimReadyAt) revert Window();
        uint256 pay = p.payout;
        uint256 rest = p.premium + p.collateral - pay;
        address buyer = p.buyer;
        p.premium = 0;
        p.collateral = 0;
        p.state = ST_PAID;
        _give(buyer, pay);
        _give(underwriter, rest);
        emit Paid(id, pay);
    }

    function reclaim(uint256 id) external nonReentrant {
        if (msg.sender != underwriter) revert NotUnderwriter();
        Policy storage p = policies[id];
        uint256 amount;
        if (p.state == ST_VOID && p.collateral != 0) {
            amount = p.premium + p.collateral;
        } else if (p.state == ST_ACTIVE && block.timestamp >= p.expiry) {
            amount = p.premium + p.collateral;
        } else {
            revert BadState();
        }
        p.premium = 0;
        p.collateral = 0;
        p.state = ST_SETTLED;
        _give(underwriter, amount);
        emit Reclaimed(id);
    }

    function _fresh(uint32 asset, bool useMark) internal view returns (uint256) {
        bytes memory data = abi.encodeWithSelector(
            useMark ? ICoverReadings.mark.selector : ICoverReadings.oraclePx.selector, asset
        );
        (bool ok, bytes memory ret) = address(readings).staticcall(data);
        if (!ok || ret.length < 64) revert Unreadable();
        (uint64 px, uint64 at) = abi.decode(ret, (uint64, uint64));
        if (px == 0 || at == 0 || at > block.timestamp) revert Unreadable();
        if (block.timestamp - at > MAX_STALENESS) revert Stale();
        return px;
    }

    function _requireReduced(Policy storage p) internal view {
        (bool ok, bytes memory ret) = address(readings).staticcall(
            abi.encodeWithSelector(ICoverReadings.positionSzi.selector, p.buyer, p.asset)
        );
        if (!ok || ret.length < 64) revert Unreadable();
        (int64 szi, bool readable) = abi.decode(ret, (int64, bool));
        if (!readable) revert Unreadable();
        if (_mag(szi) >= p.coveredSzi) revert PositionIntact();
    }

    function _mag(int64 s) internal pure returns (uint256) {
        if (s == type(int64).min) revert Unreadable();
        return uint256(uint64(s < 0 ? -s : s));
    }

    function _balance() internal view returns (uint256) {
        (bool ok, bytes memory ret) =
            premiumToken.staticcall(abi.encodeWithSelector(bytes4(0x70a08231), address(this)));
        if (!ok || ret.length < 32) revert Unreadable();
        return abi.decode(ret, (uint256));
    }

    function _callToken(bytes memory data) internal {
        (bool ok, bytes memory ret) = premiumToken.call(data);
        if (!ok) revert TransferFailed();
        if (ret.length == 0) return;
        if (ret.length < 32 || !abi.decode(ret, (bool))) revert TransferFailed();
    }

    function _take(address from, uint256 amount) internal {
        uint256 beforeBal = _balance();
        _callToken(abi.encodeWithSelector(bytes4(0x23b872dd), from, address(this), amount));
        if (_balance() != beforeBal + amount) revert TransferFailed();
    }

    function _give(address to, uint256 amount) internal {
        if (amount == 0) return;
        uint256 beforeBal = _balance();
        if (beforeBal < amount) revert Insolvent();
        _callToken(abi.encodeWithSelector(bytes4(0xa9059cbb), to, amount));
        if (beforeBal - _balance() != amount) revert TransferFailed();
    }
}

/**
 * Reads HyperEVM precompiles. Position is 0x800, mark is 0x806, oracle is
 * 0x807, which is the layout named for these covers. Upstream helpers also
 * expose a later position precompile at 0x813; this reader stays on 0x800.
 * A failed call or a zero price comes back as (0, 0) or (0, false). That is
 * "not read", and the escrow will not treat it as a price or a flat book.
 * The precompiles do not return a timestamp. `updatedAt` is this block's
 * timestamp when a non-zero price was actually returned.
 *
 * Not offered to customers. Licence review pending. Do not enable in production.
 */
contract HyperPrecompileReader is ICoverReadings {
    address public constant POSITION_PRECOMPILE = 0x0000000000000000000000000000000000000800;
    address public constant MARK_PRECOMPILE = 0x0000000000000000000000000000000000000806;
    address public constant ORACLE_PRECOMPILE = 0x0000000000000000000000000000000000000807;

    error ChainRefused();

    constructor() {
        if (block.chainid != 998 && block.chainid != 84532) revert ChainRefused();
    }

    function mark(uint32 asset) external view returns (uint64 price, uint64 updatedAt) {
        return _px(MARK_PRECOMPILE, asset);
    }

    function oraclePx(uint32 asset) external view returns (uint64 price, uint64 updatedAt) {
        return _px(ORACLE_PRECOMPILE, asset);
    }

    function positionSzi(address user, uint32 asset) external view returns (int64 szi, bool readable) {
        (bool ok, bytes memory result) = POSITION_PRECOMPILE.staticcall(abi.encode(user, asset));
        if (!ok || result.length < 32) return (0, false);
        return (abi.decode(result, (int64)), true);
    }

    function _px(address precompile, uint32 asset) internal view returns (uint64 price, uint64 updatedAt) {
        (bool ok, bytes memory result) = precompile.staticcall(abi.encode(asset));
        if (!ok || result.length < 32) return (0, 0);
        uint64 px = abi.decode(result, (uint64));
        if (px == 0) return (0, 0);
        return (px, uint64(block.timestamp));
    }
}
