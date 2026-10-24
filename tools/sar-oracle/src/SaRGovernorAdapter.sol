// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

interface IEulerVault {
    function setLTV(address collateral, uint16 borrowLTV, uint16 liquidationLTV, uint32 rampDuration) external;
    function LTVFull(address collateral)
        external
        view
        returns (uint16 borrowLTV, uint16 liquidationLTV, uint16 initialLiquidationLTV, uint48 targetTimestamp, uint32 rampDuration);
}

interface ISaRProbe {
    function isStale(uint32 asset, uint16 dex, uint64 maxAgeSec) external view returns (bool);
}

/// @title SaRGovernorAdapter
/// @notice Testnet governor hook that may only lower an Euler vault's LTV.
///         Public signals, not advice. It does not hold a key and does not
///         move funds. The constructor reverts off Base Sepolia.
/// @dev Operation bits follow the current Euler Vault Kit Constants.sol:
///      OP_LIQUIDATE is bit 11 (1 << 11). Bit 12 is OP_FLASHLOAN.
///      An older kit revision numbered liquidate as bit 12. This adapter
///      does not.
contract SaRGovernorAdapter {
    uint256 public constant BASE_SEPOLIA = 84532;
    uint32 public constant OP_BORROW = 1 << 6;
    /// Liquidation. Bit 11 in the current kit, not bit 12.
    uint32 public constant OP_LIQUIDATE = 1 << 11;
    /// Flash loan. Bit 12. Not a liquidation.
    uint32 public constant OP_FLASHLOAN = 1 << 12;

    address public immutable vault;
    address public immutable collateral;
    address public immutable probe;
    address public immutable curator;
    uint32 public immutable asset;
    uint16 public immutable dex;
    uint16 public immutable minBorrow;
    uint16 public immutable maxBorrow;
    uint16 public immutable minLiq;
    uint16 public immutable maxLiq;

    error WrongChain();
    error ZeroAddress();
    error Bounds();
    error NotLower();
    error Ramp();
    error NotCurator();
    error NoAction();
    error BorrowAboveLiquidation();

    constructor(
        address vault_,
        address collateral_,
        address probe_,
        address curator_,
        uint32 asset_,
        uint16 dex_,
        uint16 minBorrow_,
        uint16 maxBorrow_,
        uint16 minLiq_,
        uint16 maxLiq_
    ) {
        if (block.chainid != BASE_SEPOLIA) revert WrongChain();
        if (vault_ == address(0) || collateral_ == address(0) || probe_ == address(0) || curator_ == address(0)) {
            revert ZeroAddress();
        }
        if (minBorrow_ > maxBorrow_ || minLiq_ > maxLiq_ || maxBorrow_ > maxLiq_ || minBorrow_ > minLiq_) {
            revert Bounds();
        }
        vault = vault_;
        collateral = collateral_;
        probe = probe_;
        curator = curator_;
        asset = asset_;
        dex = dex_;
        minBorrow = minBorrow_;
        maxBorrow = maxBorrow_;
        minLiq = minLiq_;
        maxLiq = maxLiq_;
    }

    /// Ops this hook may be installed for. Liquidation is bit 11.
    /// Flash loan (bit 12) is not included.
    function hookedOps() external pure returns (uint32) {
        return OP_BORROW | OP_LIQUIDATE;
    }

    function opLiquidate() external pure returns (uint32) {
        return OP_LIQUIDATE;
    }

    function opFlashloan() external pure returns (uint32) {
        return OP_FLASHLOAN;
    }

    /// True only for the liquidate bit itself. A flash-loan bit is not a liquidation.
    function isLiquidate(uint32 op) external pure returns (bool) {
        return op == OP_LIQUIDATE;
    }

    /// Lower both LTVs. Equal is not a lower. A stale reading does not call the vault.
    function lowerLTV(uint16 borrowLTV, uint16 liquidationLTV, uint32 rampDuration) external {
        if (msg.sender != curator) revert NotCurator();
        if (rampDuration == 0) revert Ramp();
        if (borrowLTV > liquidationLTV) revert BorrowAboveLiquidation();
        if (borrowLTV < minBorrow || borrowLTV > maxBorrow) revert Bounds();
        if (liquidationLTV < minLiq || liquidationLTV > maxLiq) revert Bounds();
        if (ISaRProbe(probe).isStale(asset, dex, 180)) revert NoAction();
        (uint16 curBorrow, uint16 curLiq,,,) = IEulerVault(vault).LTVFull(collateral);
        if (curBorrow == 0 && curLiq == 0) revert NoAction();
        if (borrowLTV >= curBorrow || liquidationLTV >= curLiq) revert NotLower();
        IEulerVault(vault).setLTV(collateral, borrowLTV, liquidationLTV, rampDuration);
    }
}
