// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

/// @title DrainHash
/// @notice EIP-712 digests for Permit2, EIP-2612 and EIP-3009, built from the
///         full preimage. There is no entry that accepts a caller-supplied
///         digest plus a separate spender.
/// @dev Clean-room. Type strings match the public Permit2 and EIP-2612 layouts.
library DrainHash {
    bytes32 internal constant DETAILS = keccak256("PermitDetails(address token,uint160 amount,uint48 expiration,uint48 nonce)");
    bytes32 internal constant SINGLE = keccak256(
        "PermitSingle(PermitDetails details,address spender,uint256 sigDeadline)PermitDetails(address token,uint160 amount,uint48 expiration,uint48 nonce)"
    );
    bytes32 internal constant BATCH = keccak256(
        "PermitBatch(PermitDetails[] details,address spender,uint256 sigDeadline)PermitDetails(address token,uint160 amount,uint48 expiration,uint48 nonce)"
    );
    bytes32 internal constant TOKEN_PERMISSIONS = keccak256("TokenPermissions(address token,uint256 amount)");
    bytes32 internal constant TRANSFER_FROM = keccak256(
        "PermitTransferFrom(TokenPermissions permitted,address spender,uint256 nonce,uint256 deadline)TokenPermissions(address token,uint256 amount)"
    );
    bytes32 internal constant BATCH_TRANSFER = keccak256(
        "PermitBatchTransferFrom(TokenPermissions[] permitted,address spender,uint256 nonce,uint256 deadline)TokenPermissions(address token,uint256 amount)"
    );
    bytes32 internal constant EIP2612 = keccak256("Permit(address owner,address spender,uint256 value,uint256 nonce,uint256 deadline)");
    bytes32 internal constant TRANSFER_AUTH = keccak256(
        "TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
    );
    bytes32 internal constant RECEIVE_AUTH = keccak256(
        "ReceiveWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
    );
    bytes32 internal constant DOMAIN = keccak256("EIP712Domain(string name,uint256 chainId,address verifyingContract)");

    struct PermitDetails {
        address token;
        uint160 amount;
        uint48 expiration;
        uint48 nonce;
    }

    struct PermitSingle {
        PermitDetails details;
        address spender;
        uint256 sigDeadline;
    }

    struct TokenPermissions {
        address token;
        uint256 amount;
    }

    struct PermitTransferFrom {
        TokenPermissions permitted;
        uint256 nonce;
        uint256 deadline;
    }

    function permit2Domain(address permit2) internal view returns (bytes32) {
        return keccak256(abi.encode(DOMAIN, keccak256("Permit2"), block.chainid, permit2));
    }

    function _details(PermitDetails memory d) private pure returns (bytes32) {
        return keccak256(abi.encode(DETAILS, d.token, d.amount, d.expiration, d.nonce));
    }

    function single(PermitSingle memory p, address permit2) internal view returns (bytes32) {
        bytes32 structHash = keccak256(abi.encode(SINGLE, _details(p.details), p.spender, p.sigDeadline));
        return keccak256(abi.encodePacked(hex"1901", permit2Domain(permit2), structHash));
    }

    function batch(PermitDetails[] memory details, address spender, uint256 sigDeadline, address permit2)
        internal
        view
        returns (bytes32)
    {
        bytes32[] memory parts = new bytes32[](details.length);
        for (uint256 i = 0; i < details.length; i++) {
            parts[i] = _details(details[i]);
        }
        bytes32 structHash = keccak256(abi.encode(BATCH, keccak256(abi.encodePacked(parts)), spender, sigDeadline));
        return keccak256(abi.encodePacked(hex"1901", permit2Domain(permit2), structHash));
    }

    function transferFrom(PermitTransferFrom memory p, address spender, address permit2) internal view returns (bytes32) {
        bytes32 perm = keccak256(abi.encode(TOKEN_PERMISSIONS, p.permitted.token, p.permitted.amount));
        bytes32 structHash = keccak256(abi.encode(TRANSFER_FROM, perm, spender, p.nonce, p.deadline));
        return keccak256(abi.encodePacked(hex"1901", permit2Domain(permit2), structHash));
    }

    function batchTransfer(TokenPermissions[] memory permitted, address spender, uint256 nonce, uint256 deadline, address permit2)
        internal
        view
        returns (bytes32)
    {
        bytes32[] memory parts = new bytes32[](permitted.length);
        for (uint256 i = 0; i < permitted.length; i++) {
            parts[i] = keccak256(abi.encode(TOKEN_PERMISSIONS, permitted[i].token, permitted[i].amount));
        }
        bytes32 structHash = keccak256(abi.encode(BATCH_TRANSFER, keccak256(abi.encodePacked(parts)), spender, nonce, deadline));
        return keccak256(abi.encodePacked(hex"1901", permit2Domain(permit2), structHash));
    }

    /// @param witnessTypeString The witness field and its types, including the
    ///        TokenPermissions type, as Permit2 appends it to the fixed stub.
    function witness(
        PermitTransferFrom memory p,
        address spender,
        bytes32 witnessHash,
        string memory witnessTypeString,
        address permit2
    ) internal view returns (bytes32) {
        bytes32 typeHash = keccak256(
            abi.encodePacked(
                "PermitWitnessTransferFrom(TokenPermissions permitted,address spender,uint256 nonce,uint256 deadline,",
                witnessTypeString
            )
        );
        bytes32 perm = keccak256(abi.encode(TOKEN_PERMISSIONS, p.permitted.token, p.permitted.amount));
        bytes32 structHash = keccak256(abi.encode(typeHash, perm, spender, p.nonce, p.deadline, witnessHash));
        return keccak256(abi.encodePacked(hex"1901", permit2Domain(permit2), structHash));
    }

    function eip2612(bytes32 domainSeparator, address owner, address spender, uint256 value, uint256 nonce, uint256 deadline)
        internal
        pure
        returns (bytes32)
    {
        bytes32 structHash = keccak256(abi.encode(EIP2612, owner, spender, value, nonce, deadline));
        return keccak256(abi.encodePacked(hex"1901", domainSeparator, structHash));
    }

    function eip3009(
        bytes32 domainSeparator,
        bytes32 typeHash,
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce
    ) internal pure returns (bytes32) {
        if (typeHash != TRANSFER_AUTH && typeHash != RECEIVE_AUTH) return bytes32(0);
        bytes32 structHash = keccak256(abi.encode(typeHash, from, to, value, validAfter, validBefore, nonce));
        return keccak256(abi.encodePacked(hex"1901", domainSeparator, structHash));
    }
}
