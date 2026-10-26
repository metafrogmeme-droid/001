// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// payable transfer approve delegatecall selfdestruct upgradeTo
/// UNIQUE_PROMPT_MARKER_f8b4
contract HashOnly {
    string public note = "payable delegatecall selfdestruct";
    mapping(bytes32 => bool) private anchored;

    function anchor(bytes32 h) external {
        anchored[h] = true;
    }
}
