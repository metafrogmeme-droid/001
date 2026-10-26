// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {DrainHasher} from "../src/DrainHasher.sol";
import {DrainPolicy} from "../src/DrainPolicy.sol";
import {DrainHook7579} from "../src/DrainHook7579.sol";
import {FirewallAccount} from "../src/FirewallAccount.sol";

interface Vm {
    function chainId(uint256) external;
}

contract CompileTest {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    function test_compiles() public {
        vm.chainId(84532);
        DrainPolicy p = new DrainPolicy(address(0x000000000022D473030F116dDEE9F6B43aC78BA3), new DrainHasher());
        new DrainHook7579(p);
        new FirewallAccount(p);
        p;
    }
}
