// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

contract Vault {
    function deposit() external payable {}

    function withdraw() external {
        (bool ok, ) = msg.sender.call{value: address(this).balance}("");
        require(ok, "send failed");
    }
}
