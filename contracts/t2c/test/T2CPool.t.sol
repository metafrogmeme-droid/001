// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/T2CPool.sol";

/// Not run in CI: the runner has no forge. tests/test_t2c_pool.py is the gate.
contract T2CPoolTest is Test {
    address constant OWNER = address(0xA11CE);
    address constant USER = address(0xB0B);

    function _pool() internal returns (T2CPool) {
        return new T2CPool(100 ether, OWNER, address(0));
    }

    function testForkRehearsalCapsAndPauses() public {
        vm.chainId(31337);
        T2CPool pool = _pool();
        vm.deal(USER, 200 ether);
        vm.prank(USER);
        vm.expectRevert(T2CPool.Paused.selector);
        pool.deposit{value: 1 ether}();
        vm.prank(OWNER);
        pool.unpause();
        vm.prank(USER);
        pool.deposit{value: 40 ether}();
        assertEq(pool.assets(), 40 ether);
        assertEq(pool.shareBalance(USER), 40 ether);
        vm.prank(USER);
        vm.expectRevert(T2CPool.DepositCap.selector);
        pool.deposit{value: 61 ether}();
        assertEq(pool.assets(), 40 ether);
        vm.prank(OWNER);
        pool.pause();
        vm.prank(USER);
        vm.expectRevert(T2CPool.Paused.selector);
        pool.deposit{value: 1 ether}();
    }

    function testWithdrawalNftRefusesMainnet() public {
        vm.chainId(1);
        T2CPool pool = new T2CPool(100 ether, OWNER, address(0x1234));
        vm.prank(OWNER);
        pool.unpause();
        vm.prank(USER);
        vm.expectRevert(T2CPool.WithdrawalNftRefused.selector);
        pool.depositWithdrawalNft(1);
    }
}
