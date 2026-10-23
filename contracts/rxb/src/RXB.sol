// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

/// @notice Fork and public-testnet chain ids only. Mainnet ids revert.
abstract contract ForkOnly {
    error MainnetRefusal();

    constructor() {
        uint256 id = block.chainid;
        if (
            id != 31337 && id != 1337 && id != 11155111 && id != 84532
                && id != 46630
        ) revert MainnetRefusal();
    }
}

interface IVaultCaps {
    function decreaseAbsoluteCap(bytes calldata idData, uint256 newAbsoluteCap) external;
    function decreaseRelativeCap(bytes calldata idData, uint256 newRelativeCap) external;
}

interface IMockUSDC {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

/// @notice 2-of-3 packets, valid for one hour. The hash is EIP-191 over abi.encode.
contract RXBSigners is ForkOnly {
    error BadSig();
    error Replay();
    error Expired();

    struct Packet {
        uint256 nav;
        uint256 vChain;
        uint256 vAtt;
        uint256 supply;
        uint256 pMkt;
        bool includeMkt;
        uint256 depthUsd;
        uint256 attestedAt;
        uint256 signedAt;
        uint256 nonce;
    }

    address[3] public signers;
    Packet public current;
    bool public submitted;
    mapping(uint256 => bool) public usedNonce;
    uint256 constant HOUR = 3600;

    constructor(address a, address b, address c) {
        if (a == address(0) || b == address(0) || c == address(0)) revert BadSig();
        if (a == b || a == c || b == c) revert BadSig();
        signers = [a, b, c];
    }

    function submit(
        uint256 nav,
        uint256 vChain,
        uint256 vAtt,
        uint256 supply,
        uint256 pMkt,
        bool includeMkt,
        uint256 depthUsd,
        uint256 attestedAt,
        uint256 signedAt,
        uint256 nonce,
        bytes calldata sigA,
        bytes calldata sigB
    ) external {
        if (block.timestamp + HOUR < signedAt || signedAt + HOUR < block.timestamp) revert Expired();
        if (usedNonce[nonce]) revert Replay();
        Packet memory p = Packet(
            nav, vChain, vAtt, supply, pMkt, includeMkt, depthUsd, attestedAt, signedAt, nonce
        );
        bytes32 digest = _digest(p);
        address one = _recover(digest, sigA);
        address two = _recover(digest, sigB);
        if (one == two || !_isSigner(one) || !_isSigner(two)) revert BadSig();
        usedNonce[nonce] = true;
        current = p;
        submitted = true;
    }

    function fields()
        external
        view
        returns (
            uint256 nav,
            uint256 vChain,
            uint256 vAtt,
            uint256 supply,
            uint256 pMkt,
            bool includeMkt,
            uint256 depthUsd,
            uint256 attestedAt,
            uint256 signedAt
        )
    {
        Packet memory p = current;
        return (p.nav, p.vChain, p.vAtt, p.supply, p.pMkt, p.includeMkt, p.depthUsd, p.attestedAt, p.signedAt);
    }

    function _isSigner(address who) internal view returns (bool) {
        return who != address(0) && (who == signers[0] || who == signers[1] || who == signers[2]);
    }

    function _digest(Packet memory p) internal pure returns (bytes32) {
        bytes32 body = keccak256(
            abi.encode(
                p.nav, p.vChain, p.vAtt, p.supply, p.pMkt, p.includeMkt, p.depthUsd, p.attestedAt, p.signedAt, p.nonce
            )
        );
        return keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", body));
    }

    function _recover(bytes32 digest, bytes calldata sig) internal pure returns (address) {
        if (sig.length != 65) return address(0);
        bytes32 r;
        bytes32 s;
        uint256 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 32))
            v := shr(248, calldataload(add(sig.offset, 64)))
        }
        if (s > 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0) return address(0);
        if (v < 27) v += 27;
        if (v != 27 && v != 28) return address(0);
        return ecrecover(digest, uint8(v), r, s);
    }
}

/// @notice Morpho IOracle. A wad quote (1e18 = 1.0) is scaled by 1e18 to 1e36.
/// Unreadable inputs revert. They are not returned as zero.
contract RXBOracle is ForkOnly {
    error Unreadable();
    error Expired();

    RXBSigners public immutable signers;
    uint256 constant WAD = 1e18;
    uint256 constant HALF = 72 hours;
    uint256 constant HOUR = 3600;
    uint256 constant DEPTH_MIN = 1_000_000;
    uint256[16] internal BITS;

    constructor(RXBSigners signers_) {
        if (address(signers_) == address(0)) revert Unreadable();
        signers = signers_;
        BITS[0] = 707106781186547524;
        BITS[1] = 840896415253714543;
        BITS[2] = 917004043204671231;
        BITS[3] = 957603280698573646;
        BITS[4] = 978572062087700134;
        BITS[5] = 989228013193975484;
        BITS[6] = 994599423483633175;
        BITS[7] = 997296056085470126;
        BITS[8] = 998647112890970173;
        BITS[9] = 999323327502650752;
        BITS[10] = 999661606496243683;
        BITS[11] = 999830788931929063;
        BITS[12] = 999915390886613497;
        BITS[13] = 999957694548431132;
        BITS[14] = 999978847050491929;
        BITS[15] = 999989423469314464;
    }

    function weightWad(uint256 delta) public view returns (uint256) {
        uint256 whole = delta / HALF;
        if (whole >= 64) return 0;
        uint256 w = WAD >> whole;
        uint256 rem = delta % HALF;
        if (rem == 0) return w;
        uint256 q = (rem << 16) / HALF;
        for (uint256 k = 0; k < 16; k++) {
            if ((q & (uint256(1) << (15 - k))) != 0) w = (w * BITS[k]) / WAD;
        }
        return w;
    }

    /// Morpho calls price() with no arguments.
    function price() external view returns (uint256) {
        if (!signers.submitted()) revert Unreadable();
        (
            uint256 nav,
            uint256 vChain,
            uint256 vAtt,
            uint256 supply,
            uint256 pMkt,
            bool includeMkt,
            uint256 depthUsd,
            uint256 attestedAt,
            uint256 signedAt
        ) = signers.fields();
        if (block.timestamp + HOUR < signedAt || signedAt + HOUR < block.timestamp) revert Expired();
        if (supply == 0 || nav == 0) revert Unreadable();
        if (attestedAt > block.timestamp) revert Unreadable();
        uint256 w = weightWad(block.timestamp - attestedAt);
        uint256 backed = vChain + ((vAtt * w) / WAD);
        uint256 per = (backed * WAD) / supply;
        uint256 pBack = per < nav ? per : nav;
        uint256 quote = pBack;
        if (includeMkt && depthUsd >= DEPTH_MIN) {
            quote = pMkt < pBack ? pMkt : pBack;
        }
        return quote * WAD;
    }
}

/// @notice Decrease only. The keeper sends the coordinated cap. No increase entrypoint.
contract RXBSentinel is ForkOnly {
    error NotKeeper();
    error Increase();

    address public keeper;
    mapping(bytes32 => uint256) public lastAbs;
    mapping(bytes32 => bool) public seenAbs;
    mapping(bytes32 => uint256) public lastRel;
    mapping(bytes32 => bool) public seenRel;

    constructor(address keeper_) {
        if (keeper_ == address(0)) revert NotKeeper();
        keeper = keeper_;
    }

    function decreaseAbsolute(address vault, bytes calldata idData, uint256 newCap) external {
        if (msg.sender != keeper) revert NotKeeper();
        bytes32 key = keccak256(abi.encode(vault, keccak256(idData)));
        if (seenAbs[key] && newCap >= lastAbs[key]) revert Increase();
        IVaultCaps(vault).decreaseAbsoluteCap(idData, newCap);
        lastAbs[key] = newCap;
        seenAbs[key] = true;
    }

    function decreaseRelative(address vault, bytes calldata idData, uint256 newCap) external {
        if (msg.sender != keeper) revert NotKeeper();
        bytes32 key = keccak256(abi.encode(vault, keccak256(idData)));
        if (seenRel[key] && newCap >= lastRel[key]) revert Increase();
        IVaultCaps(vault).decreaseRelativeCap(idData, newCap);
        lastRel[key] = newCap;
        seenRel[key] = true;
    }
}

/// @notice 5_000 mock USDC, doubling for 30 days, 72h window. Reclaim is not a failure.
contract RXBChallenge is ForkOnly {
    error Window();
    error Bond();
    error Resolved();
    error NotResolver();
    error NotChallenger();
    error Duplicate();

    uint256 public constant BASE = 5000 * 10 ** 6;
    uint256 public constant WINDOW = 72 hours;
    uint256 public constant SPAN = 30 days;

    IMockUSDC public immutable usdc;
    address public resolver;
    address public slashRecipient;
    mapping(bytes32 => Challenge) public challenges;
    mapping(address => uint256[]) public failAt;

    struct Challenge {
        address challenger;
        uint256 bond;
        uint256 opened;
        bool open;
        bool resolved;
    }

    constructor(address usdc_, address resolver_, address recipient_) {
        if (usdc_ == address(0) || resolver_ == address(0) || recipient_ == address(0)) revert Bond();
        usdc = IMockUSDC(usdc_);
        resolver = resolver_;
        slashRecipient = recipient_;
    }

    function requiredBond(address who) public view returns (uint256) {
        uint256 n = _recent(who);
        if (n >= 64) revert Bond();
        return BASE << n;
    }

    function open(bytes32 id) external {
        Challenge storage c = challenges[id];
        if (c.open) revert Duplicate();
        uint256 bond = requiredBond(msg.sender);
        if (!usdc.transferFrom(msg.sender, address(this), bond)) revert Bond();
        c.challenger = msg.sender;
        c.bond = bond;
        c.opened = block.timestamp;
        c.open = true;
    }

    function resolve(bytes32 id, bool pass) external {
        if (msg.sender != resolver) revert NotResolver();
        Challenge storage c = challenges[id];
        if (!c.open || c.resolved) revert Resolved();
        if (block.timestamp > c.opened + WINDOW) revert Window();
        c.resolved = true;
        if (pass) {
            if (!usdc.transfer(c.challenger, c.bond)) revert Bond();
        } else {
            if (!usdc.transfer(slashRecipient, c.bond)) revert Bond();
            failAt[c.challenger].push(block.timestamp);
        }
    }

    function reclaim(bytes32 id) external {
        Challenge storage c = challenges[id];
        if (!c.open || c.resolved) revert Resolved();
        if (msg.sender != c.challenger) revert NotChallenger();
        if (block.timestamp <= c.opened + WINDOW) revert Window();
        c.resolved = true;
        if (!usdc.transfer(c.challenger, c.bond)) revert Bond();
    }

    function _recent(address who) internal view returns (uint256 n) {
        uint256[] storage xs = failAt[who];
        uint256 cut = block.timestamp >= SPAN ? block.timestamp - SPAN : 0;
        for (uint256 i = 0; i < xs.length; i++) {
            if (xs[i] >= cut) n++;
        }
    }
}

/// @notice Test double. Not a Morpho deployment. Delay zero applies an increase immediately.
contract VaultStub {
    error NotOwner();
    error NotSentinel();
    error Increase();
    error Locked();

    address public owner;
    address public curator;
    uint256 public increaseDelay;
    mapping(address => bool) public isSentinel;
    mapping(bytes32 => uint256) public absCap;
    mapping(bytes32 => uint256) public relCap;
    mapping(bytes32 => uint256) public armedAt;

    constructor(address owner_, address curator_) {
        owner = owner_;
        curator = curator_;
    }

    function setIsSentinel(address who, bool allowed) external {
        if (msg.sender != owner) revert NotOwner();
        isSentinel[who] = allowed;
    }

    function setIncreaseDelay(uint256 delay) external {
        if (msg.sender != owner) revert NotOwner();
        increaseDelay = delay;
    }

    function increaseAbsoluteCap(bytes calldata idData, uint256 newAbsoluteCap) external {
        if (msg.sender != owner && msg.sender != curator) revert NotOwner();
        bytes32 id = keccak256(idData);
        if (increaseDelay != 0) {
            if (armedAt[id] == 0) {
                armedAt[id] = block.timestamp;
                return;
            }
            if (block.timestamp < armedAt[id] + increaseDelay) revert Locked();
        }
        absCap[id] = newAbsoluteCap;
        armedAt[id] = 0;
    }

    function decreaseAbsoluteCap(bytes calldata idData, uint256 newAbsoluteCap) external {
        if (msg.sender != curator && !isSentinel[msg.sender]) revert NotSentinel();
        bytes32 id = keccak256(idData);
        if (newAbsoluteCap > absCap[id]) revert Increase();
        absCap[id] = newAbsoluteCap;
    }

    function decreaseRelativeCap(bytes calldata idData, uint256 newRelativeCap) external {
        if (msg.sender != curator && !isSentinel[msg.sender]) revert NotSentinel();
        bytes32 id = keccak256(idData);
        if (newRelativeCap > relCap[id]) revert Increase();
        relCap[id] = newRelativeCap;
    }

    function loadRelative(bytes calldata idData, uint256 cap) external {
        if (msg.sender != owner) revert NotOwner();
        relCap[keccak256(idData)] = cap;
    }
}

/// @notice Test double, 6 decimals. Anyone can mint. It is not a mainnet asset.
contract MockUSDC {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function decimals() external pure returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "balance");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(balanceOf[from] >= amount, "balance");
        require(allowance[from][msg.sender] >= amount, "allowance");
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}
