// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

import "CGCGwrsETH.sol";

/// Testnet stand-in for listing gwrsETH on a Morpho-style market. Morpho Blue
/// markets are immutable, so the mint cap lives on the wrapper and this route
/// is the only extra caller allowed to mint. It is not a Morpho deployment.
/// The constructor refuses every chain that is not in CGCChains.
contract CGCMorphoRoute {
    CGCGwrsETH public immutable wrapped;
    CGCBrake public immutable brake;
    CGCOracleAdapter public immutable oracle;
    bytes32 public immutable asset;

    mapping(address => uint256) public collateralOf;
    mapping(address => uint256) public debtOf;

    constructor(CGCGwrsETH wrapped_, CGCBrake brake_, CGCOracleAdapter oracle_, bytes32 asset_) {
        CGCChains.windows(block.chainid);
        require(address(wrapped_) != address(0) && address(brake_) != address(0) && address(oracle_) != address(0), "addr");
        require(wrapped_.SYMBOL_HASH() == keccak256("gwrsETH"), "symbol");
        require(wrapped_.asset() == asset_ && oracle_.asset() == asset_, "asset");
        wrapped = wrapped_;
        brake = brake_;
        oracle = oracle_;
        asset = asset_;
    }

    function supplyCollateral(uint256 amount) external {
        wrapped.mint(msg.sender, address(this), amount);
        collateralOf[msg.sender] += amount;
    }

    function borrow(uint256 amount) external {
        require(!brake.frozen(asset), "frozen");
        require(amount != 0, "amount");
        uint256 next = debtOf[msg.sender] + amount;
        require(next <= collateralOf[msg.sender], "short");
        debtOf[msg.sender] = next;
    }

    /// Repay stays open while the brake is on.
    function repay(uint256 amount) external {
        uint256 debt = debtOf[msg.sender];
        require(amount != 0 && amount <= debt, "amount");
        debtOf[msg.sender] = debt - amount;
    }

    /// Withdraw stays open while the brake is on, down to the outstanding debt.
    function withdrawCollateral(uint256 amount) external {
        uint256 posted = collateralOf[msg.sender];
        require(amount != 0 && amount <= posted, "amount");
        require(posted - amount >= debtOf[msg.sender], "short");
        collateralOf[msg.sender] = posted - amount;
        wrapped.burn(msg.sender, amount);
    }

    /// The liquidation price. Unscaled until a violation is confirmed.
    function price() external view returns (uint256) {
        return oracle.price();
    }
}
