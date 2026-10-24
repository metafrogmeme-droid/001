// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {SaRVerifier} from "./SaRVerifier.sol";

/// @notice The on-chain half of the quantum canary.
///         Solidity cannot read an EOA nonce. A watcher outside this
///         repository polls eth_getTransactionCount on a NUMS address and,
///         if the nonce rises, calls signal(). This contract holds no key
///         and does not decide that the nonce moved.
///         A keccak preimage is not an ECDLP canary and is not accepted here.
contract QuantumCanary {
    uint256 public constant BASE_SEPOLIA = 84532;

    address public immutable watcher;
    SaRVerifier public immutable verifier;

    error WrongChain();
    error NotWatcher();

    constructor(address watcher_, SaRVerifier verifier_) {
        if (block.chainid != BASE_SEPOLIA) revert WrongChain();
        if (watcher_ == address(0) || address(verifier_) == address(0)) revert NotWatcher();
        watcher = watcher_;
        verifier = verifier_;
    }

    /// @dev Forwards to SaRVerifier.trip. The verifier ORs the bits. Nothing here clears them.
    function signal(uint32 flags, string calldata reason) external {
        if (msg.sender != watcher) revert NotWatcher();
        verifier.trip(flags, reason);
    }
}
