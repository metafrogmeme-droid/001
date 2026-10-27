// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.24;

import {HashKeySeal} from "../src/HashKeySeal.sol";
import {DeployHashKeySeal} from "../script/DeployHashKeySeal.s.sol";

interface Vm {
    function chainId(uint256) external;
    function expectRevert(bytes calldata) external;
    function stopBroadcast() external;
}

/// The deploy script's own guards, driven without a network. Each refused
/// chain gets its own test: after an expected revert the script's
/// startBroadcast is still active, and stopBroadcast clears it.
contract DeployHashKeySealTest {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    address internal constant PUBLISHER = address(0xB0b0);
    /// forge's default sender: inside startBroadcast the deploy is sent by
    /// this account, not by the script contract, in a test as in a script.
    address internal constant BROADCASTER = 0x1804c8AB1F12E6bbf3894d4083f33e07309d1f38;

    function _refuses(uint256 cid, uint256 expected, bytes memory err) internal {
        _refuses(cid, expected, PUBLISHER, address(0), err);
    }

    function _refuses(uint256 cid, uint256 expected, address publisher, address owner, bytes memory err) internal {
        DeployHashKeySeal d = new DeployHashKeySeal();
        vm.chainId(cid);
        vm.expectRevert(err);
        d.runWithCliWallet(expected, publisher, owner);
        vm.stopBroadcast();
    }

    function testRefusesEthereumMainnet() public {
        _refuses(1, 1, abi.encodeWithSelector(DeployHashKeySeal.UnexpectedChain.selector, uint256(1)));
    }

    function testRefusesALocalChain() public {
        _refuses(31337, 31337, abi.encodeWithSelector(DeployHashKeySeal.UnexpectedChain.selector, uint256(31337)));
    }

    function testRefusesMainnetWhenTestnetWasNamed() public {
        _refuses(177, 133, abi.encodeWithSelector(DeployHashKeySeal.ChainMismatch.selector, uint256(177), uint256(133)));
    }

    function testRefusesTestnetWhenMainnetWasNamed() public {
        _refuses(133, 177, abi.encodeWithSelector(DeployHashKeySeal.ChainMismatch.selector, uint256(133), uint256(177)));
    }

    function testRefusesAZeroPublisher() public {
        _refuses(133, 133, address(0), address(0), abi.encodeWithSelector(HashKeySeal.ZeroPublisher.selector));
    }

    function testRefusesAnOwnerOtherThanTheOneShown() public {
        _refuses(
            133,
            133,
            PUBLISHER,
            address(0xdEaD),
            abi.encodeWithSelector(DeployHashKeySeal.OwnerMismatch.selector, BROADCASTER, address(0xdEaD))
        );
    }

    function testDeploysOnTestnetAndMainnetWithTheNamedPublisher() public {
        uint256[2] memory chains = [uint256(133), uint256(177)];
        for (uint256 i = 0; i < 2; i++) {
            DeployHashKeySeal d = new DeployHashKeySeal();
            vm.chainId(chains[i]);
            address at = d.runWithCliWallet(chains[i], PUBLISHER, address(0));
            HashKeySeal seal = HashKeySeal(at);
            require(seal.publishers(PUBLISHER), "first publisher allowlisted");
            require(seal.owner() == BROADCASTER, "the broadcaster is the owner");
            require(!seal.publishers(BROADCASTER), "the owner is not a publisher");
        }
    }

    function testDeploysWhenTheShownOwnerMatches() public {
        DeployHashKeySeal d = new DeployHashKeySeal();
        vm.chainId(133);
        address at = d.runWithCliWallet(133, PUBLISHER, BROADCASTER);
        require(HashKeySeal(at).owner() == BROADCASTER, "owner as shown");
    }
}
