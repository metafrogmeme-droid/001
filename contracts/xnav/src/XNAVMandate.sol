// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

import {XNAVTestnet} from "./XNAVTestnet.sol";

/// @notice Per-coin notional cap for an agent mandate.
///         A coin with no cap on record is not unlimited: the check pauses
///         deposits. The de-risk call uses a placeholder encoding of the
///         configured action id. It is not the live CoreWriter layout.
contract XNAVMandate is XNAVTestnet {
    address public immutable admin;
    address public immutable coreWriter;
    uint256 public immutable actionId;
    bool public depositsPaused;
    mapping(bytes32 => uint256) public maxNotional;
    mapping(bytes32 => bool) public capSet;

    event Breach(bytes32 indexed coin, bytes32 reason);
    event Derisk(bytes32 indexed coin, uint256 notional);

    constructor(address admin_, address coreWriter_, uint256 actionId_) {
        require(admin_ != address(0) && coreWriter_ != address(0), "admin");
        admin = admin_;
        coreWriter = coreWriter_;
        actionId = actionId_;
    }

    function setCap(bytes32 coin, uint256 maxN) external {
        require(msg.sender == admin, "admin");
        capSet[coin] = true;
        maxNotional[coin] = maxN;
    }

    function check(bytes32 coin, uint256 notional) external returns (bool) {
        if (!capSet[coin]) {
            depositsPaused = true;
            emit Breach(coin, "no_cap");
            return false;
        }
        if (notional > maxNotional[coin]) {
            depositsPaused = true;
            emit Breach(coin, "over_cap");
            return false;
        }
        return true;
    }

    /// @notice Asks the configured writer to de-risk. A failed call does
    ///         not clear the pause.
    function requestDerisk(bytes32 coin, uint256 notional) external {
        require(msg.sender == admin, "admin");
        depositsPaused = true;
        emit Derisk(coin, notional);
        bytes memory payload = abi.encode(actionId, coin, notional);
        (bool ok,) = coreWriter.call(payload);
        if (!ok) emit Breach(coin, "derisk_unsent");
    }
}
