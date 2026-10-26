// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {DrainErrors} from "../src/DrainErrors.sol";
import {DrainHasher} from "../src/DrainHasher.sol";
import {DrainHook7579} from "../src/DrainHook7579.sol";
import {DrainPolicy} from "../src/DrainPolicy.sol";
import {DrainTypes} from "../src/DrainTypes.sol";
import {FirewallAccount} from "../src/FirewallAccount.sol";

interface Vm {
    function envUint(string calldata) external view returns (uint256);
    function startBroadcast() external;
    function startBroadcast(uint256) external;
    function stopBroadcast() external;
}

/// @notice Testnet deploy. One policy and one hook per chain. The 7702
///         implementation is deployed only when `delegate` is true, and only
///         on Base Sepolia, where it is a wallet-delegation test.
/// @dev Clean-room. The server never runs this. No key is stored in the file.
contract DeployDrainFirewall {
    bool public IS_SCRIPT = true;

    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    address internal constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;

    function run(bool delegate) external {
        vm.startBroadcast(vm.envUint("ETH_PRIVATE_KEY"));
        _deploy(delegate);
        vm.stopBroadcast();
    }

    function runWithCliWallet(bool delegate) external {
        vm.startBroadcast();
        _deploy(delegate);
        vm.stopBroadcast();
    }

    function simulate(bool delegate) external returns (address policy, address hook, address account) {
        return _deploy(delegate);
    }

    function _deploy(bool delegate) internal returns (address policy, address hook, address account) {
        if (DrainTypes.mainnet(block.chainid)) revert DrainErrors.Mainnet();
        if (delegate && block.chainid != 84532) revert DrainErrors.ChainRefused();
        DrainHasher hasher = new DrainHasher();
        DrainPolicy p = new DrainPolicy(PERMIT2, hasher);
        hasher.bind(address(p));
        DrainHook7579 h = new DrainHook7579(p);
        if (delegate) account = address(new FirewallAccount(p));
        return (address(p), address(h), account);
    }
}
