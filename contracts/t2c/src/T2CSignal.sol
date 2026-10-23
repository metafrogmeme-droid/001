// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

/// A cap ratio, published once. This is not an oracle and it has no price().
/// A zero ratio with capReadable false is unread, not a price of zero.
contract T2CSignal {
    uint256 public capRatioWad;
    bool public capReadable;

    error UnreadableIsNotZero();

    constructor(uint256 ratioWad, bool readable) {
        if (!readable && ratioWad != 0) revert UnreadableIsNotZero();
        capRatioWad = ratioWad;
        capReadable = readable;
    }

    function capSignal() external view returns (uint256 ratioWad, bool readable) {
        return (capRatioWad, capReadable);
    }
}
