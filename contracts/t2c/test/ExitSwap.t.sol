// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/ExitSwapModel.sol";
import "../src/T2CSignal.sol";

/// Not run in CI: the runner has no forge. test_t2c_exit.py is the gate.
/// This file is the fork shape a local `forge test` would drive.
contract ExitSwapTest is Test {
    function testForkUnwindAccountsTheFeeToLps() public {
        vm.chainId(31337);
        ExitSwapModel model = new ExitSwapModel(0.99e18);
        (uint256 looper, uint256 fee) = model.unwind(1e18, 0.99e18, 10, 0.5e18, 1000e18, 0);
        assertEq(fee, model.lpFeeEth());
        assertGt(looper, 0);
    }

    function testMainnetReverts() public {
        vm.chainId(1);
        ExitSwapModel model = new ExitSwapModel(0.99e18);
        vm.expectRevert(ExitSwapModel.MainnetForbidden.selector);
        model.unwind(1e18, 1e18, 10, 0, 1000e18, 0);
    }

    function testSepoliaReverts() public {
        vm.chainId(11155111);
        ExitSwapModel model = new ExitSwapModel(0.99e18);
        vm.expectRevert();
        model.unwind(1e18, 1e18, 10, 0, 1000e18, 0);
    }

    function testUnreadSignalIsNotAZeroPrice() public {
        T2CSignal signal = new T2CSignal(0, false);
        (uint256 ratio, bool readable) = signal.capSignal();
        assertEq(ratio, 0);
        assertFalse(readable);
    }
}
