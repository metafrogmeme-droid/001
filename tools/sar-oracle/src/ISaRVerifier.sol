// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @notice Crypto-agile checker for one SaR epoch root.
///         Public signals, not advice. Dollar amounts are not this interface.
interface ISaRVerifier {
    struct Metric {
        uint32 asset;
        uint16 dex;
        uint64 l1Block;
        uint64 ts;
        uint64 seq;
        uint32 sAdjBps;
        uint32 neffE2;
        uint32 cr1Bps;
        uint128 ld2UsdE6;
        uint128 tsar95UsdE6;
        uint32 flags;
    }

    enum Scheme {
        SECP256K1_EIP712,
        MLDSA44_SOL,
        WOTS_HASHCHAIN,
        MLDSA44_PRECOMPILE
    }

    event EpochPosted(uint64 indexed epoch, bytes32 root, Scheme scheme, uint64 l1Block);
    event SignerRotated(uint8 scheme, bytes32 newKeyCommit, uint64 effectiveEpoch);
    event CircuitBreaker(uint32 flags, string reason);

    function postEpoch(uint64 epoch, bytes32 root, uint64 l1Block, Scheme scheme, bytes calldata sig) external;
    function verifyMetric(Metric calldata m, bytes32[] calldata proof, uint64 epoch) external view returns (bool);
    function latest(uint32 asset, uint16 dex) external view returns (Metric memory m, uint64 ageSec);
    function isStale(uint32 asset, uint16 dex, uint64 maxAgeSec) external view returns (bool);
    function rotateSigner(uint8 scheme, bytes32 newKeyCommit, bytes calldata authSig) external;
    function setRequiredSchemes(uint8 bitmask) external;
    function trip(uint32 flags, string calldata reason) external;
}
