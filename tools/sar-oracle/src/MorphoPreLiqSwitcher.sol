// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

interface IMorphoAuth {
    function setAuthorization(address authorized, bool newIsAuthorized) external;
    function isAuthorized(address authorizer, address authorized) external view returns (bool);
}

interface IPreLiqParams {
    function preLiquidationParams()
        external
        view
        returns (uint256 preLltv, uint256 preLCF1, uint256 preLCF2, uint256 preLIF1, uint256 preLIF2, address oracle);
}

interface IStressProbe {
    function isStale(uint32 asset, uint16 dex, uint64 maxAgeSec) external view returns (bool);
    function latest(uint32 asset, uint16 dex) external view returns (uint32 sAdjBps, uint64 ageSec);
}

/// @title MorphoPreLiqSwitcher
/// @notice Testnet borrower-side switch. When the SaR reading is fresh and
///         above a threshold, it authorizes the tighter pre-deployed
///         PreLiquidation contract on Morpho. It never authorizes the looser
///         one and it never revokes the tighter one. A stale or unread
///         reading does not call Morpho. The constructor reverts off Base
///         Sepolia and on a zero address. Public signals, not advice.
/// @dev The Morpho position owner is this contract: setAuthorization records
///      the authorizer as msg.sender. This contract does not call
///      preLiquidate and does not move funds.
contract MorphoPreLiqSwitcher {
    uint256 public constant BASE_SEPOLIA = 84532;
    uint64 public constant UNSET_AGE = type(uint64).max;

    address public immutable morpho;
    address public immutable tight;
    address public immutable loose;
    address public immutable probe;
    address public immutable borrower;
    uint32 public immutable asset;
    uint16 public immutable dex;
    uint32 public immutable thresholdBps;

    error WrongChain();
    error ZeroAddress();
    error Bounds();
    error NotBorrower();
    error NoAction();

    constructor(
        address morpho_,
        address tight_,
        address loose_,
        address probe_,
        address borrower_,
        uint32 asset_,
        uint16 dex_,
        uint32 thresholdBps_
    ) {
        if (block.chainid != BASE_SEPOLIA) revert WrongChain();
        if (
            morpho_ == address(0) || tight_ == address(0) || loose_ == address(0) || probe_ == address(0)
                || borrower_ == address(0)
        ) revert ZeroAddress();
        if (thresholdBps_ == 0) revert Bounds();
        (uint256 tightLltv,,,,,) = IPreLiqParams(tight_).preLiquidationParams();
        (uint256 looseLltv,,,,,) = IPreLiqParams(loose_).preLiquidationParams();
        if (tightLltv == 0 || looseLltv == 0 || tightLltv >= looseLltv) revert Bounds();
        morpho = morpho_;
        tight = tight_;
        loose = loose_;
        probe = probe_;
        borrower = borrower_;
        asset = asset_;
        dex = dex_;
        thresholdBps = thresholdBps_;
    }

    /// Authorize the tighter contract. Already authorized is a no-op, not a
    /// second write. Anything that is not a fresh reading above the threshold
    /// reverts NoAction and does not call Morpho.
    function activate() external {
        if (msg.sender != borrower) revert NotBorrower();
        if (IStressProbe(probe).isStale(asset, dex, 180)) revert NoAction();
        (uint32 sAdjBps, uint64 ageSec) = IStressProbe(probe).latest(asset, dex);
        if (ageSec == UNSET_AGE || sAdjBps <= thresholdBps) revert NoAction();
        if (IMorphoAuth(morpho).isAuthorized(address(this), tight)) return;
        IMorphoAuth(morpho).setAuthorization(tight, true);
    }
}
