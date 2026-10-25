// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

/// Quantum-custody reading for a testnet.
///
/// Ethereum (1), Base (8453), Arbitrum One (42161) and HyperEVM (999)
/// are refused. Sepolia (11155111), Base Sepolia (84532), Arbitrum
/// Sepolia (421614) and HyperEVM testnet (998) are the chains this
/// contract accepts. There is no deploy script in this tree.
///
/// Two of the three committee keys must sign. Scheme ids are the SaR
/// verifier's: 0 ECDSA, 2 a committed WOTS key. Schemes 1 and 3 revert,
/// and scheme 2 does not verify a signature here. A missing reading
/// reverts. It does not answer zero.
contract QCHOracle {
    uint256 public constant STALE = 1 days;
    uint256 public constant TIMELOCK = 7 days;
    bytes32 public constant READING_TYPEHASH = keccak256(
        "QCHReading(bytes32 asset,uint16 qesBps,uint16 twBps,uint16 flags,uint64 ts,uint8 scheme)"
    );
    bytes32 public constant DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
    uint256 private constant HALF_N =
        0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0;

    struct Reading {
        uint16 qesBps;
        uint16 twBps;
        uint16 flags;
        uint64 ts;
        bool set;
    }

    mapping(bytes32 => Reading) private _readings;
    mapping(address => bool) public isSigner;
    address[3] public signerAt;
    address public immutable guardian;
    uint8 public pendingScheme;
    uint8 public pendingSlot;
    bytes32 public pendingCommit;
    uint64 public pendingReady;
    bytes32 public wotsCommit;

    constructor(address s0, address s1, address s2, address guardian_) {
        require(_allowed(block.chainid), "testnet only");
        require(
            s0 != address(0) && s1 != address(0) && s2 != address(0) && guardian_ != address(0),
            "signer"
        );
        require(s0 != s1 && s0 != s2 && s1 != s2, "signer");
        signerAt[0] = s0;
        signerAt[1] = s1;
        signerAt[2] = s2;
        isSigner[s0] = true;
        isSigner[s1] = true;
        isSigner[s2] = true;
        guardian = guardian_;
    }

    function _allowed(uint256 id) internal pure returns (bool) {
        return id == 11155111 || id == 84532 || id == 421614 || id == 998;
    }

    function domainSeparator() public view returns (bytes32) {
        return keccak256(abi.encode(
            DOMAIN_TYPEHASH,
            keccak256(bytes("QCH Oracle")),
            keccak256(bytes("1")),
            block.chainid,
            address(this)
        ));
    }

    function digest(
        bytes32 asset,
        uint16 qesBps,
        uint16 twBps,
        uint16 flags,
        uint64 ts,
        uint8 scheme
    ) public view returns (bytes32) {
        bytes32 body = keccak256(abi.encode(
            READING_TYPEHASH, asset, qesBps, twBps, flags, ts, scheme
        ));
        return keccak256(abi.encodePacked(hex"1901", domainSeparator(), body));
    }

    /// ECDSA only. Schemes 1 and 3 are the SaR ML-DSA ids and revert.
    /// Scheme 2 is recorded by rotateSigner and is not checked here.
    function submit(
        bytes32 asset,
        uint16 qesBps,
        uint16 twBps,
        uint16 flags,
        uint64 ts,
        uint8 scheme,
        bytes[] calldata sigs
    ) external {
        require(scheme == 0, "scheme unavailable");
        require(qesBps <= 10000 && twBps <= 10000, "qes");
        require(ts > 0 && ts <= block.timestamp, "future");
        require(block.timestamp - ts <= STALE, "stale");
        require(_count(digest(asset, qesBps, twBps, flags, ts, scheme), sigs) >= 2, "threshold");
        _readings[asset] = Reading(qesBps, twBps, flags, ts, true);
    }

    /// Unset reverts. A stored 0 is returned: it is a measured zero.
    function qesOf(bytes32 asset) external view returns (uint16 qesBps, uint16 twBps, uint16 flags, uint64 ts) {
        Reading storage r = _readings[asset];
        require(r.set, "unreadable");
        require(block.timestamp >= r.ts, "unreadable");
        require(block.timestamp - r.ts <= STALE, "stale");
        return (r.qesBps, r.twBps, r.flags, r.ts);
    }

    /// Name the next key. Scheme 0 or 2 only. ML-DSA ids revert.
    function rotateSigner(uint8 scheme, uint8 slot, bytes32 newKeyCommit) external {
        require(msg.sender == guardian, "guardian");
        require(scheme == 0 || scheme == 2, "scheme unavailable");
        require(newKeyCommit != bytes32(0), "commit");
        require(scheme == 2 || slot < 3, "slot");
        pendingScheme = scheme;
        pendingSlot = slot;
        pendingCommit = newKeyCommit;
        pendingReady = uint64(block.timestamp + TIMELOCK);
    }

    function applyRotation(address newSigner) external {
        require(pendingReady != 0 && block.timestamp >= pendingReady, "timelock");
        if (pendingScheme == 0) {
            require(newSigner != address(0) && !isSigner[newSigner], "signer");
            require(keccak256(abi.encodePacked(newSigner)) == pendingCommit, "commit");
            address old = signerAt[pendingSlot];
            isSigner[old] = false;
            signerAt[pendingSlot] = newSigner;
            isSigner[newSigner] = true;
        } else if (pendingScheme == 2) {
            require(newSigner == address(0), "signer");
            wotsCommit = pendingCommit;
        } else {
            revert("scheme unavailable");
        }
        pendingCommit = bytes32(0);
        pendingReady = 0;
    }

    function _count(bytes32 dig, bytes[] calldata sigs) internal view returns (uint256) {
        uint256 n = 0;
        address first = address(0);
        address second = address(0);
        for (uint256 i = 0; i < sigs.length; i++) {
            address rec = _recover(dig, sigs[i]);
            if (!isSigner[rec] || rec == first || rec == second) continue;
            if (n == 0) first = rec;
            else second = rec;
            n += 1;
            if (n >= 2) return n;
        }
        return n;
    }

    function _recover(bytes32 hash, bytes calldata sig) internal pure returns (address) {
        require(sig.length == 65, "signature");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 32))
            v := byte(0, calldataload(add(sig.offset, 64)))
        }
        require(uint256(s) <= HALF_N, "signature");
        if (v < 27) v += 27;
        require(v == 27 || v == 28, "signature");
        address rec = ecrecover(hash, v, r, s);
        require(rec != address(0), "signature");
        return rec;
    }
}
