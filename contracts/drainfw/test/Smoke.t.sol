// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {DeployDrainFirewall} from "../script/Deploy.s.sol";
import {DrainErrors} from "../src/DrainErrors.sol";
import {DrainHasher} from "../src/DrainHasher.sol";
import {DrainPolicy} from "../src/DrainPolicy.sol";
import {DrainTypes} from "../src/DrainTypes.sol";
import {FirewallAccount} from "../src/FirewallAccount.sol";

interface Vm {
    function chainId(uint256) external;
    function etch(address, bytes calldata) external;
    function addr(uint256) external returns (address);
    function prank(address) external;
    function expectRevert(bytes4) external;
    function sign(uint256, bytes32) external returns (uint8, bytes32, bytes32);
}

contract SmokeTest {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    address internal constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;

    function test_smoke_init_and_frontrun() public {
        vm.chainId(84532);
        DrainPolicy policy = new DrainPolicy(PERMIT2, new DrainHasher());
        FirewallAccount impl = new FirewallAccount(policy);
        require(impl.initialized(), "impl");
        vm.expectRevert(DrainErrors.AlreadyInit.selector);
        impl.init(address(0xBEEF), address(0xCAFE), "");

        uint256 pk = 0xA11CE;
        address eoa = vm.addr(pk);
        address guardian = address(0x1111);
        address vault = address(0x2222);
        vm.etch(eoa, address(impl).code);
        require(address(FirewallAccount(payable(eoa)).POLICY()) == address(policy), "immutable");

        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.FrontRun.selector);
        FirewallAccount(payable(eoa)).init(address(0xBAD), vault, "");

        bytes32 digest = keccak256(abi.encode(block.chainid, eoa, guardian, vault));
        bytes32 ethHash = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", digest));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, ethHash);
        vm.prank(address(0xBAD));
        FirewallAccount(payable(eoa)).init(guardian, vault, abi.encodePacked(r, s, v));
        require(policy.guardianOf(eoa) == guardian, "guardian");
        require(policy.ownerOf(eoa) == eoa, "owner");
        require(uint256(policy.modeOf(eoa)) == uint256(DrainTypes.MODE_QUEUE), "mode");

        vm.prank(eoa);
        vm.expectRevert(DrainErrors.AlreadyInit.selector);
        FirewallAccount(payable(eoa)).init(guardian, vault, "");
    }

    function test_mainnet_and_fuji() public {
        vm.chainId(1);
        try this.deployPolicy() returns (DrainPolicy) {
            require(false, "mainnet policy");
        } catch (bytes memory err) {
            require(bytes4(err) == DrainErrors.Mainnet.selector, "mainnet reason");
        }
        vm.chainId(43113);
        DrainPolicy p = new DrainPolicy(PERMIT2, new DrainHasher());
        require(address(p) != address(0), "fuji policy");
        try new FirewallAccount(p) returns (FirewallAccount) {
            require(false, "fuji delegate");
        } catch (bytes memory err) {
            require(bytes4(err) == DrainErrors.ChainRefused.selector, "fuji reason");
        }
        require(DrainTypes.supports7702(84532), "base");
        require(!DrainTypes.supports7702(43113), "fuji");
    }

    function deployPolicy() external returns (DrainPolicy) {
        return new DrainPolicy(PERMIT2, new DrainHasher());
    }

    /// One policy and one hook. The 7702 implementation is only on Base Sepolia.
    function test_deploy_script_is_one_shared_module() public {
        DeployDrainFirewall deploy = new DeployDrainFirewall();
        vm.chainId(84532);
        (address policy, address hook, address account) = deploy.simulate(true);
        require(policy != address(0) && hook != address(0) && account != address(0), "base");
        vm.chainId(43113);
        (policy, hook, account) = deploy.simulate(false);
        require(policy != address(0) && hook != address(0) && account == address(0), "fuji");
        try deploy.simulate(true) returns (address, address, address) {
            require(false, "fuji 7702");
        } catch (bytes memory err) {
            require(bytes4(err) == DrainErrors.ChainRefused.selector, "fuji 7702 reason");
        }
        vm.chainId(1);
        try deploy.simulate(false) returns (address, address, address) {
            require(false, "mainnet");
        } catch (bytes memory err) {
            require(bytes4(err) == DrainErrors.Mainnet.selector, "mainnet reason");
        }
    }
}
