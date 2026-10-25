// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

import {XNAVTestnet} from "./XNAVTestnet.sol";
import {XNAVOracle} from "./XNAVOracle.sol";

interface IXNAVAsset {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

/// @notice ERC-4626 shaped wrapper. totalAssets is the equity precompile
///         plus swing cash that stayed behind. A failed precompile reverts.
///         It is not treated as zero equity, and a negative reading reverts.
///
///         The precompile call encoding is a placeholder: abi.encode(vault)
///         returning one int256. It was not re-checked against a live node.
///         The raw integer is the asset unit. An unverified decimal scale
///         is not applied here.
contract SwingVault4626 is XNAVTestnet {
    IXNAVAsset public immutable asset;
    XNAVOracle public immutable oracle;
    address public immutable hlVault;
    address public immutable equityPrecompile;
    uint256 public immutable thresholdBps;
    uint256 public immutable maxSwingBps;
    uint256 public immutable epochSeconds;

    string public name = "XNAV Swing Vault";
    string public symbol = "xNAV";
    uint8 public decimals = 6;

    uint256 public totalSupply;
    uint256 public retained;
    uint256 public epochFlow;
    uint256 public flowEpoch;
    mapping(address => uint256) public balanceOf;

    event Deposit(address indexed caller, address indexed owner, uint256 assets, uint256 shares);
    event Withdraw(address indexed caller, address indexed receiver, address indexed owner, uint256 assets, uint256 shares);

    constructor(
        address asset_,
        address oracle_,
        address hlVault_,
        address equityPrecompile_,
        uint256 thresholdBps_,
        uint256 maxSwingBps_,
        uint256 epochSeconds_
    ) {
        require(asset_ != address(0) && oracle_ != address(0), "asset");
        require(hlVault_ != address(0) && equityPrecompile_ != address(0), "precompile");
        require(thresholdBps_ <= 10000 && maxSwingBps_ <= 10000 && epochSeconds_ > 0, "bounds");
        asset = IXNAVAsset(asset_);
        oracle = XNAVOracle(oracle_);
        hlVault = hlVault_;
        equityPrecompile = equityPrecompile_;
        thresholdBps = thresholdBps_;
        maxSwingBps = maxSwingBps_;
        epochSeconds = epochSeconds_;
    }

    /// @notice Equity leg. Reverts when the precompile does not answer or
    ///         answers a negative value. Zero is a real reading.
    function vaultEquity() public view returns (uint256) {
        (bool ok, bytes memory data) = equityPrecompile.staticcall(abi.encode(hlVault));
        if (!ok || data.length < 32) revert("unreadable");
        int256 value = abi.decode(data, (int256));
        if (value < 0) revert("unreadable");
        return uint256(value);
    }

    function totalAssets() public view returns (uint256) {
        return vaultEquity() + retained;
    }

    function _flow() internal view returns (uint256) {
        if (block.timestamp / epochSeconds != flowEpoch) return 0;
        return epochFlow;
    }

    function _note(uint256 amount) internal {
        uint256 epoch = block.timestamp / epochSeconds;
        if (epoch != flowEpoch) {
            flowEpoch = epoch;
            epochFlow = 0;
        }
        epochFlow += amount;
    }

    /// @notice Same split as app/lib/xnav/swing.js. The portion under the
    ///         epoch threshold is at net asset value. The rest pays a
    ///         pro-rata slice of the haircut, capped.
    function _split(uint256 total, uint256 amount)
        internal
        view
        returns (uint256 credited, uint256 swingBps, uint256 kept)
    {
        uint256 threshold = total * thresholdBps / 10000;
        uint256 soFar = _flow();
        uint256 room = soFar >= threshold ? 0 : threshold - soFar;
        uint256 under = amount < room ? amount : room;
        uint256 taxed = amount - under;
        if (taxed > 0 && total > 0) {
            uint256 sliceBps = taxed * 10000 / total;
            (uint32 haircut,,) = oracle.latest(hlVault);
            swingBps = uint256(haircut) * sliceBps / 10000;
            if (swingBps > maxSwingBps) swingBps = maxSwingBps;
        }
        uint256 paid = taxed * (10000 - swingBps) / 10000;
        credited = under + paid;
        kept = taxed - paid;
    }

    function convertToAssets(uint256 shares) public view returns (uint256) {
        require(totalSupply > 0, "no supply");
        return shares * totalAssets() / totalSupply;
    }

    function previewRedeem(uint256 shares) public view returns (uint256) {
        require(totalSupply > 0, "no supply");
        uint256 atNav = shares * totalAssets() / totalSupply;
        (uint256 credited,,) = _split(totalAssets(), atNav);
        return credited;
    }

    function previewDeposit(uint256 assets) public view returns (uint256) {
        require(assets > 0, "assets");
        if (totalSupply == 0) return assets;
        (uint256 credited,,) = _split(totalAssets(), assets);
        require(credited > 0, "assets");
        return credited * totalSupply / totalAssets();
    }

    function deposit(uint256 assets, address receiver) external returns (uint256 shares) {
        require(assets > 0 && receiver != address(0), "assets");
        uint256 supply = totalSupply;
        if (supply == 0) {
            shares = assets;
        } else {
            uint256 total = totalAssets();
            (uint256 credited,,) = _split(total, assets);
            require(credited > 0, "assets");
            shares = credited * supply / total;
            require(shares > 0, "assets");
        }
        require(asset.transferFrom(msg.sender, address(this), assets), "transfer");
        retained += assets;
        _note(assets);
        totalSupply = supply + shares;
        balanceOf[receiver] += shares;
        emit Deposit(msg.sender, receiver, assets, shares);
    }

    function redeem(uint256 shares, address receiver, address owner) external returns (uint256 assetsOut) {
        require(shares > 0 && receiver != address(0), "shares");
        require(msg.sender == owner && balanceOf[owner] >= shares, "owner");
        uint256 supply = totalSupply;
        uint256 total = totalAssets();
        uint256 atNav = shares * total / supply;
        (assetsOut,,) = _split(total, atNav);
        require(assetsOut <= retained, "idle");
        balanceOf[owner] -= shares;
        totalSupply = supply - shares;
        retained -= assetsOut;
        _note(atNav);
        require(asset.transfer(receiver, assetsOut), "transfer");
        emit Withdraw(msg.sender, receiver, owner, assetsOut, shares);
    }
}
