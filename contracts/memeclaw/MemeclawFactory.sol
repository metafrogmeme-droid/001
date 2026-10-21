// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

/**
 * Memeclaw — a memecoin launchpad for the DogeOS TESTNET, and nothing else.
 *
 * What this is: anyone may launch a coin from their OWN wallet. The factory
 * creates the coin with a fixed supply, holds all of it, and sells and buys
 * it back on a bonding curve priced in the chain's native coin (testnet
 * DOGE). The website never signs or sends a transaction: it hands a wallet
 * the call, then reads the chain to see what happened.
 *
 * What this is NOT, by construction:
 * - Not on a chain with value. The constructor refuses every chain but the
 *   DogeOS testnet (CHAIN_ID). A mainnet build is a different contract and
 *   a different decision, after an audit and a legal check.
 * - Not owned. No owner, no admin, no pause, no fee, no withdraw, no
 *   upgrade. What is deployed is what runs.
 * - Not a mint. A coin's supply is created once, in full, inside the coin's
 *   constructor, and no function can create more.
 * - Not a pool or a graduation. The curve is the market, for as long as the
 *   chain runs: once the supply is sold out, buys stop and sells still work.
 *
 * THE CURVE. Constant product over virtual reserves, the shape pump.fun made
 * familiar, with no fee:
 *
 *   x = VIRTUAL_DOGE   + dogeReserve     (the native coin the curve holds)
 *   y = VIRTUAL_TOKENS + tokenReserve    (the coins the curve still holds)
 *
 *   buy:  out = dogeIn  * y / (x + dogeIn)      (rounded down)
 *   sell: out = tokenIn * x / (y + tokenIn)     (rounded down)
 *
 * Rounding down on both sides means x*y never falls, so x never falls below
 * VIRTUAL_DOGE while the curve holds at most SUPPLY coins: every coin sold
 * back is paid from DOGE the curve actually received, never from another
 * coin's reserve. The tests drive that with random trading and then sell
 * every holder out.
 */
contract MemeclawToken {
    string public name;
    string public symbol;
    uint8 public constant decimals = 18;
    uint256 public immutable totalSupply;
    /// The factory that created this coin and holds its curve.
    address public immutable factory;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    constructor(string memory name_, string memory symbol_, uint256 supply) {
        name = name_;
        symbol = symbol_;
        totalSupply = supply;
        factory = msg.sender;
        balanceOf[msg.sender] = supply;
        emit Transfer(address(0), msg.sender, supply);
    }

    function transfer(address to, uint256 value) external returns (bool) {
        _move(msg.sender, to, value);
        return true;
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) {
            require(allowed >= value, "allowance");
            allowance[from][msg.sender] = allowed - value;
        }
        _move(from, to, value);
        return true;
    }

    function _move(address from, address to, uint256 value) private {
        require(to != address(0), "zero address");
        uint256 held = balanceOf[from];
        require(held >= value, "balance");
        // No sum of balances can exceed totalSupply, so neither line wraps.
        unchecked {
            balanceOf[from] = held - value;
            balanceOf[to] += value;
        }
        emit Transfer(from, to, value);
    }
}

contract MemeclawFactory {
    /// The DogeOS testnet. The constructor refuses any other chain.
    uint256 public constant CHAIN_ID = 6281971;
    /// Every coin's whole supply, created once, held by the curve at launch.
    uint256 public constant SUPPLY = 1_000_000_000 ether;
    /// The curve's virtual reserves (see the header). The opening price is
    /// VIRTUAL_DOGE / (VIRTUAL_TOKENS + SUPPLY) native coin per coin, and
    /// buying out the whole supply costs
    /// VIRTUAL_DOGE * SUPPLY / VIRTUAL_TOKENS (40 testnet DOGE).
    uint256 public constant VIRTUAL_DOGE = 10 ether;
    uint256 public constant VIRTUAL_TOKENS = 250_000_000 ether;
    uint256 public constant MAX_NAME_BYTES = 32;
    uint256 public constant MAX_SYMBOL_BYTES = 10;

    struct Curve {
        uint256 dogeReserve;   // native coin this curve received and still holds
        uint256 tokenReserve;  // coins this curve still holds
        address creator;
        uint64 launchedAt;
    }

    mapping(address => Curve) public curves;
    address[] public launches;
    bool private entered;

    event Launched(address indexed token, address indexed creator, bytes32 indexed termsHash,
                   string name, string symbol);
    event Trade(address indexed token, address indexed trader, bool isBuy,
                uint256 dogeAmount, uint256 tokenAmount,
                uint256 dogeReserve, uint256 tokenReserve);

    modifier nonReentrant() {
        require(!entered, "reentrant");
        entered = true;
        _;
        entered = false;
    }

    modifier live(uint256 deadline) {
        require(block.timestamp <= deadline, "expired");
        _;
    }

    constructor() {
        require(block.chainid == CHAIN_ID, "testnet only");
    }

    // ── launch ──────────────────────────────────────────────────────────────

    /**
     * Create a coin and its curve. `termsHash` is the SHA-256 of the terms
     * the Memeclaw page sealed for this launch (name, symbol, description,
     * creator); it rides in the event so the chain says which terms the
     * coin was launched under. Any value sent buys for the creator in the
     * same transaction, before anyone else can.
     */
    function launch(string calldata name_, string calldata symbol_, bytes32 termsHash,
                    uint256 minTokensOut, uint256 deadline)
        external payable nonReentrant live(deadline) returns (address token)
    {
        require(termsHash != bytes32(0), "terms required");
        _checkName(bytes(name_));
        _checkSymbol(bytes(symbol_));
        token = address(new MemeclawToken(name_, symbol_, SUPPLY));
        curves[token] = Curve(0, SUPPLY, msg.sender, uint64(block.timestamp));
        launches.push(token);
        emit Launched(token, msg.sender, termsHash, name_, symbol_);
        if (msg.value > 0) _buy(token, msg.value, minTokensOut);
    }

    /// Printable ASCII only, 1 to MAX_NAME_BYTES: no control characters, no
    /// look-alike letters from other scripts, no invisible characters.
    function _checkName(bytes calldata b) private pure {
        require(b.length > 0 && b.length <= MAX_NAME_BYTES, "name length");
        for (uint256 i = 0; i < b.length; i++) {
            require(b[i] >= 0x20 && b[i] <= 0x7e, "name characters");
        }
        require(b[0] != 0x20 && b[b.length - 1] != 0x20, "name spaces");
    }

    /// A-Z and 0-9 only, 1 to MAX_SYMBOL_BYTES.
    function _checkSymbol(bytes calldata b) private pure {
        require(b.length > 0 && b.length <= MAX_SYMBOL_BYTES, "symbol length");
        for (uint256 i = 0; i < b.length; i++) {
            bytes1 c = b[i];
            require((c >= 0x41 && c <= 0x5a) || (c >= 0x30 && c <= 0x39), "symbol characters");
        }
    }

    // ── trade ───────────────────────────────────────────────────────────────

    function buy(address token, uint256 minTokensOut, uint256 deadline)
        external payable nonReentrant live(deadline) returns (uint256 out)
    {
        require(msg.value > 0, "nothing sent");
        out = _buy(token, msg.value, minTokensOut);
    }

    /// Sell `amount` coins back to the curve. The factory must be approved
    /// for `amount` on the coin first (`approve(factory, amount)`).
    function sell(address token, uint256 amount, uint256 minDogeOut, uint256 deadline)
        external nonReentrant live(deadline) returns (uint256 out)
    {
        Curve storage c = curves[token];
        require(c.creator != address(0), "unknown token");
        require(amount > 0, "nothing sold");
        out = _sellQuote(c, amount);
        require(out > 0, "too small");
        require(out >= minDogeOut, "slippage");
        // The invariant, checked rather than assumed.
        require(out <= c.dogeReserve, "reserve");
        c.dogeReserve -= out;
        c.tokenReserve += amount;
        emit Trade(token, msg.sender, false, out, amount, c.dogeReserve, c.tokenReserve);
        require(MemeclawToken(token).transferFrom(msg.sender, address(this), amount), "transfer");
        (bool ok, ) = payable(msg.sender).call{value: out}("");
        require(ok, "payout");
    }

    function _buy(address token, uint256 dogeIn, uint256 minTokensOut) private returns (uint256 out) {
        Curve storage c = curves[token];
        require(c.creator != address(0), "unknown token");
        out = _buyQuote(c, dogeIn);
        require(out > 0, "too small");
        require(out <= c.tokenReserve, "sold out");
        require(out >= minTokensOut, "slippage");
        c.dogeReserve += dogeIn;
        c.tokenReserve -= out;
        emit Trade(token, msg.sender, true, dogeIn, out, c.dogeReserve, c.tokenReserve);
        require(MemeclawToken(token).transfer(msg.sender, out), "transfer");
    }

    // ── quotes (what the page shows before a wallet is asked) ───────────────

    function _buyQuote(Curve storage c, uint256 dogeIn) private view returns (uint256) {
        uint256 x = VIRTUAL_DOGE + c.dogeReserve;
        uint256 y = VIRTUAL_TOKENS + c.tokenReserve;
        return dogeIn * y / (x + dogeIn);
    }

    function _sellQuote(Curve storage c, uint256 tokenIn) private view returns (uint256) {
        uint256 x = VIRTUAL_DOGE + c.dogeReserve;
        uint256 y = VIRTUAL_TOKENS + c.tokenReserve;
        return tokenIn * x / (y + tokenIn);
    }

    /// Coins a buy of `dogeIn` would receive now; 0 for an unknown coin or a
    /// buy larger than the supply left.
    function quoteBuy(address token, uint256 dogeIn) external view returns (uint256 out) {
        Curve storage c = curves[token];
        if (c.creator == address(0)) return 0;
        out = _buyQuote(c, dogeIn);
        if (out > c.tokenReserve) return 0;
    }

    /// Native coin a sale of `tokenIn` would receive now; 0 for an unknown coin.
    function quoteSell(address token, uint256 tokenIn) external view returns (uint256) {
        Curve storage c = curves[token];
        if (c.creator == address(0)) return 0;
        return _sellQuote(c, tokenIn);
    }

    function launchCount() external view returns (uint256) {
        return launches.length;
    }
}
