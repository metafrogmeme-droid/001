// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

import "CGC.sol";

interface IERC20 {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

/// Testnet receipt around an underlying the curator names. Mint velocity is the
/// shared inflow cap. The constructor refuses every chain that is not in
/// CGCChains. There is no deploy script and no mainnet underlying address.
contract CGCGwrsETH {
    bytes32 public constant SYMBOL_HASH = keccak256("gwrsETH");

    CGCInflow public immutable inflow;
    CGCBrake public immutable brake;
    address public immutable underlying;
    address public immutable curator;
    bytes32 public immutable asset;

    address public route;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 amount);
    event Approval(address indexed owner, address indexed spender, uint256 amount);

    constructor(address underlying_, CGCInflow inflow_, CGCBrake brake_, bytes32 asset_, address curator_) {
        CGCChains.windows(block.chainid);
        require(underlying_ != address(0) && address(inflow_) != address(0) && address(brake_) != address(0), "addr");
        require(curator_ != address(0), "curator");
        underlying = underlying_;
        inflow = inflow_;
        brake = brake_;
        asset = asset_;
        curator = curator_;
    }

    function name() external pure returns (string memory) {
        return "Gated wrapped rsETH";
    }

    function symbol() external pure returns (string memory) {
        return "gwrsETH";
    }

    function decimals() external pure returns (uint8) {
        return 18;
    }

    function bindRoute(address route_) external {
        require(msg.sender == curator, "curator");
        require(route == address(0) && route_ != address(0), "bind");
        route = route_;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _move(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        require(allowed >= amount, "allowance");
        if (allowed != type(uint256).max) allowance[from][msg.sender] = allowed - amount;
        _move(from, to, amount);
        return true;
    }

    /// Curator or the bound testnet Morpho route. A stranger's mint reverts
    /// before the underlying moves.
    function mint(address payer, address to, uint256 amount) external {
        require(msg.sender == curator || msg.sender == route, "minter");
        require(to != address(0) && amount != 0, "amount");
        require(!brake.frozen(asset), "frozen");
        uint256 beforeBal = IERC20(underlying).balanceOf(address(this));
        require(IERC20(underlying).transferFrom(payer, address(this), amount), "underlying");
        uint256 got = IERC20(underlying).balanceOf(address(this)) - beforeBal;
        require(got == amount, "underlying short");
        inflow.record(asset, amount);
        totalSupply += amount;
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }

    /// Exit. Works while the brake is on. Underlying goes to `to`.
    function burn(address to, uint256 amount) external {
        require(to != address(0) && amount != 0, "amount");
        uint256 bal = balanceOf[msg.sender];
        require(bal >= amount, "balance");
        balanceOf[msg.sender] = bal - amount;
        totalSupply -= amount;
        emit Transfer(msg.sender, address(0), amount);
        inflow.release(asset, amount);
        require(IERC20(underlying).transfer(to, amount), "underlying");
    }

    function _move(address from, address to, uint256 amount) internal {
        require(to != address(0), "to");
        uint256 bal = balanceOf[from];
        require(bal >= amount, "balance");
        balanceOf[from] = bal - amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
    }
}
