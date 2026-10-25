// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

contract Proxy {
    address public impl;

    function exec(bytes calldata data) external returns (bytes memory) {
        (bool ok, bytes memory ret) = impl.delegatecall(data);
        require(ok, "delegatecall failed");
        return ret;
    }
}
