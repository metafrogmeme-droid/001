// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.24;

import {HashKeySeal} from "../src/HashKeySeal.sol";

interface Vm {
    function envAddress(string calldata name) external view returns (address);
    function envUint(string calldata name) external view returns (uint256);
    function startBroadcast() external;
    function startBroadcast(uint256 privateKey) external;
    function stopBroadcast() external;
}

/// Owner deploy. The shell picks the wallet. This file does not store a key.
contract DeployHashKeySeal {
    bool public IS_SCRIPT = true;

    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    uint256 internal constant TESTNET_CHAIN_ID = 133;
    uint256 internal constant MAINNET_CHAIN_ID = 177;

    error UnexpectedChain(uint256 chainId);

    function run() external {
        vm.startBroadcast(vm.envUint("ETH_PRIVATE_KEY"));
        _deploy();
        vm.stopBroadcast();
    }

    function runWithCliWallet() external {
        vm.startBroadcast();
        _deploy();
        vm.stopBroadcast();
    }

    function _deploy() internal {
        uint256 chainId = block.chainid;
        if (chainId != TESTNET_CHAIN_ID && chainId != MAINNET_CHAIN_ID) {
            revert UnexpectedChain(chainId);
        }
        new HashKeySeal(vm.envAddress("HASHKEY_SEAL_PUBLISHER"));
    }
}
