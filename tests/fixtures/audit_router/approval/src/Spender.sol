// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
}

contract Spender {
    function allow(IERC20 token, address who, uint256 amount) external {
        token.approve(who, amount);
    }
}
