// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

/// @title IDrainPolicy
/// @notice The surface a later Agent Leash module can call. One singleton
///         policy per chain; accounts do not deploy their own copy.
/// @dev Clean-room. Not derived from Zodiac Roles (BUSL-1.1).
interface IDrainPolicy {
    function check(address account, address target, uint256 value, bytes calldata data, uint256 grantId)
        external
        returns (uint8 verdict, bytes4 reason);

    function checkDigest(address account, bytes32 digest) external view returns (bool);

    function hotOf(address account) external view returns (uint128);

    function vaultOf(address account) external view returns (address);

    function guardianOf(address account) external view returns (address);

    function installed(address account) external view returns (bool);

    function isPolicyAdmin(bytes4 sel) external pure returns (bool);
}
