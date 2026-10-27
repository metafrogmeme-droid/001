// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.24;

import {HashKeySeal} from "../src/HashKeySeal.sol";

interface Vm {
    function envUint(string calldata name) external view returns (uint256);
    function startBroadcast() external;
    function startBroadcast(uint256 privateKey) external;
    function stopBroadcast() external;
    function toString(address value) external pure returns (string memory);
    function toString(uint256 value) external pure returns (string memory);
}

/// Owner deploy. The shell picks the wallet. This file does not store a key.
///
/// `expected` is the chain id the shell resolved from networks.json. It is
/// passed as a script argument and compared with block.chainid here, so the
/// chain forge connected to is the chain the operator named. It must NOT be
/// passed as `--chain` to forge script: that flag overrides block.chainid in
/// the simulation while the broadcast still signs for the endpoint's real
/// chain, which would make this comparison true by construction.
///
/// `publisher` is the first allowlisted publisher. `expectedOwner`, when not
/// zero, is the address the shell showed the operator as the owner-to-be;
/// the deploy reverts if the broadcaster differs. Both travel as script
/// arguments, not environment variables, so a test cannot race another
/// test's environment and the shell's validation is the only reading.
contract DeployHashKeySeal {
    bool public IS_SCRIPT = true;

    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    address internal constant CONSOLE = 0x000000000000000000636F6e736F6c652e6c6f67;

    uint256 internal constant TESTNET_CHAIN_ID = 133;
    uint256 internal constant MAINNET_CHAIN_ID = 177;

    error UnexpectedChain(uint256 chainId);
    error ChainMismatch(uint256 chainId, uint256 expected);
    error OwnerMismatch(address owner, address expected);

    function run(uint256 expectedChain, address publisher, address expectedOwner) external returns (address) {
        vm.startBroadcast(vm.envUint("ETH_PRIVATE_KEY"));
        address seal = _deploy(expectedChain, publisher, expectedOwner);
        vm.stopBroadcast();
        return seal;
    }

    function runWithCliWallet(uint256 expectedChain, address publisher, address expectedOwner)
        external
        returns (address)
    {
        vm.startBroadcast();
        address seal = _deploy(expectedChain, publisher, expectedOwner);
        vm.stopBroadcast();
        return seal;
    }

    function _deploy(uint256 expectedChain, address publisher, address expectedOwner) internal returns (address) {
        uint256 chainId = block.chainid;
        if (chainId != TESTNET_CHAIN_ID && chainId != MAINNET_CHAIN_ID) {
            revert UnexpectedChain(chainId);
        }
        if (chainId != expectedChain) revert ChainMismatch(chainId, expectedChain);
        HashKeySeal seal = new HashKeySeal(publisher);
        address deployer = seal.owner();
        if (expectedOwner != address(0) && deployer != expectedOwner) {
            revert OwnerMismatch(deployer, expectedOwner);
        }
        _log(
            string.concat(
                "hashkey-seal: chain ", vm.toString(chainId),
                " seal ", vm.toString(address(seal)),
                " owner ", vm.toString(deployer),
                " publisher ", vm.toString(publisher)
            )
        );
        return address(seal);
    }

    /// forge prints this under "== Logs ==" before any broadcast, so the
    /// operator sees the owner-to-be. No forge-std: the console contract is
    /// called directly, and a failure to log is not a failure to deploy.
    function _log(string memory line) internal view {
        (bool ok,) = CONSOLE.staticcall(abi.encodeWithSignature("log(string)", line));
        ok;
    }
}
