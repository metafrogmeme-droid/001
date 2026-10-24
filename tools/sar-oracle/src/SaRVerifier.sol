// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {ISaRVerifier} from "./ISaRVerifier.sol";

/// @title SaRVerifier
/// @notice Checks a SaR epoch root on Base Sepolia. Public signals, not advice.
///         This contract does not hold a key and does not move funds.
/// @dev Testnet only. The constructor reverts on any chain other than
///      Base Sepolia (84532). ML-DSA schemes revert: a verifier this
///      contract cannot check must not accept a signature.
contract SaRVerifier is ISaRVerifier {
    uint256 public constant BASE_SEPOLIA = 84532;
    uint256 public constant TIMELOCK = 7 days;
    uint8 public constant BIT_STALE = 1;
    /// @dev Required-scheme bits. 0 ECDSA, 1 ML-DSA Solidity, 2 WOTS, 3 ML-DSA precompile.
    uint8 public constant BIT_ECDSA = 1;
    uint8 public constant BIT_MLDSA_SOL = 2;
    uint8 public constant BIT_WOTS = 4;
    uint8 public constant BIT_MLDSA_PRE = 8;

    uint256 private constant HALF_N =
        0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0;

    bytes32 private constant EPOCH_TYPEHASH = keccak256(
        "Epoch(uint64 epoch,bytes32 root,uint64 l1Block,uint8 scheme)"
    );
    bytes32 private constant ROTATE_TYPEHASH = keccak256(
        "Rotate(uint8 scheme,bytes32 newKeyCommit,uint64 nonce)"
    );
    bytes32 private constant DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
    bytes32 private constant NAME_HASH = keccak256("SaR Oracle");
    bytes32 private constant VERSION_HASH = keccak256("1");

    address public immutable guardian;
    address public signer;
    address public canary;
    uint8 public requiredSchemes = BIT_ECDSA;
    uint32 public breaker;
    uint64 public lastPosted;
    uint64 public rotateNonce;

    uint8 public pendingScheme;
    bytes32 public pendingCommit;
    uint64 public pendingReady;
    uint64 public pendingEpoch;

    mapping(uint64 => bytes32) public roots;
    mapping(uint64 => bytes32) public pendingRoot;
    mapping(uint64 => uint8) public postedMask;
    mapping(uint64 => bytes32) public epochCommits;
    mapping(uint64 => bytes32) public epochWots;
    bytes32 public wotsCommit;

    struct Stored {
        bool set;
        Metric m;
    }

    mapping(uint32 => mapping(uint16 => Stored)) private book;

    error WrongChain();
    error BadSignature();
    error HighS();
    error SchemeUnavailable();
    error NotGuardian();
    error NotCanary();
    error Timelock();
    error NoEpoch();
    error BadProof();
    error RootMismatch();
    error CannotLoosen();
    error AlreadyPosted();

    constructor(address guardian_, address signer_) {
        if (block.chainid != BASE_SEPOLIA) revert WrongChain();
        if (guardian_ == address(0) || signer_ == address(0)) revert BadSignature();
        guardian = guardian_;
        signer = signer_;
    }

    modifier onlyGuardian() {
        if (msg.sender != guardian) revert NotGuardian();
        _;
    }

    function domainSeparator() public view returns (bytes32) {
        return keccak256(abi.encode(
            DOMAIN_TYPEHASH,
            NAME_HASH,
            VERSION_HASH,
            block.chainid,
            address(this)
        ));
    }

    function epochDigest(uint64 epoch, bytes32 root, uint64 l1Block, Scheme scheme)
        public
        view
        returns (bytes32)
    {
        bytes32 structHash = keccak256(abi.encode(
            EPOCH_TYPEHASH,
            epoch,
            root,
            l1Block,
            uint8(scheme)
        ));
        return keccak256(abi.encodePacked("\x19\x01", domainSeparator(), structHash));
    }

    function metricLeaf(Metric calldata m) public pure returns (bytes32) {
        return keccak256(abi.encode(
            m.asset, m.dex, m.l1Block, m.ts, m.seq,
            m.sAdjBps, m.neffE2, m.cr1Bps, m.ld2UsdE6, m.tsar95UsdE6, m.flags
        ));
    }

    function wotsHash(bytes32 x, uint256 n) public pure returns (bytes32) {
        for (uint256 i = 0; i < n; i++) {
            x = keccak256(abi.encodePacked(x));
        }
        return x;
    }

    /// @dev 64 message nibbles plus 3 checksum nibbles. Checksum max is 960.
    function wotsSymbols(bytes32 digest) public pure returns (uint8[67] memory s) {
        uint256 sum = 0;
        for (uint256 i = 0; i < 32; i++) {
            uint8 b = uint8(digest[i]);
            uint8 hi = b >> 4;
            uint8 lo = b & 0x0f;
            s[i * 2] = hi;
            s[i * 2 + 1] = lo;
            sum += uint256(15 - hi) + uint256(15 - lo);
        }
        s[64] = uint8((sum >> 8) & 0x0f);
        s[65] = uint8((sum >> 4) & 0x0f);
        s[66] = uint8(sum & 0x0f);
    }

    function postEpoch(uint64 epoch, bytes32 root, uint64 l1Block, Scheme scheme, bytes calldata sig)
        external
    {
        uint8 id = uint8(scheme);
        if (id == 1 || id == 3) revert SchemeUnavailable();
        if ((requiredSchemes & (BIT_MLDSA_SOL | BIT_MLDSA_PRE)) != 0) revert SchemeUnavailable();
        if (roots[epoch] != bytes32(0)) revert AlreadyPosted();
        uint8 bit = uint8(1 << id);
        if (id == 0) _acceptEcdsa(epoch, epochDigest(epoch, root, l1Block, scheme), sig);
        else if (id == 2) _acceptWots(epoch, epochDigest(epoch, root, l1Block, scheme), sig);
        else revert SchemeUnavailable();
        if (pendingRoot[epoch] != bytes32(0) && pendingRoot[epoch] != root) revert RootMismatch();
        pendingRoot[epoch] = root;
        postedMask[epoch] |= bit;
        if ((postedMask[epoch] & requiredSchemes) == requiredSchemes) {
            roots[epoch] = root;
            delete pendingRoot[epoch];
            if (epoch > lastPosted) lastPosted = epoch;
            emit EpochPosted(epoch, root, scheme, l1Block);
        }
    }

    function verifyMetric(Metric calldata m, bytes32[] calldata proof, uint64 epoch)
        external
        view
        returns (bool)
    {
        if (breaker != 0) return false;
        if ((m.flags & BIT_STALE) != 0) return false;
        bytes32 root = roots[epoch];
        if (root == bytes32(0)) return false;
        return _proof(metricLeaf(m), proof, root);
    }

    function latest(uint32 asset, uint16 dex) external view returns (Metric memory m, uint64 ageSec) {
        Stored memory s = book[asset][dex];
        if (!s.set || s.m.ts == 0) return (m, type(uint64).max);
        if (block.timestamp < s.m.ts) return (s.m, type(uint64).max);
        uint256 age = block.timestamp - s.m.ts;
        if (age > type(uint64).max) age = type(uint64).max;
        return (s.m, uint64(age));
    }

    function isStale(uint32 asset, uint16 dex, uint64 maxAgeSec) external view returns (bool) {
        Stored memory s = book[asset][dex];
        if (!s.set || s.m.ts == 0 || breaker != 0 || (s.m.flags & BIT_STALE) != 0) return true;
        if (block.timestamp < s.m.ts) return true;
        return block.timestamp > uint256(s.m.ts) + uint256(maxAgeSec);
    }

    /// @notice Stores a metric whose leaf sits under a posted root, so latest() can answer.
    ///         A missing proof does not store a zero metric.
    function commitMetric(uint64 epoch, Metric calldata m, bytes32[] calldata proof) external {
        bytes32 root = roots[epoch];
        if (root == bytes32(0)) revert NoEpoch();
        if (!_proof(metricLeaf(m), proof, root)) revert BadProof();
        book[m.asset][m.dex] = Stored(true, m);
    }

    function rotateSigner(uint8 scheme, bytes32 newKeyCommit, bytes calldata authSig) external {
        if (scheme != 0 && scheme != 2) revert SchemeUnavailable();
        if (newKeyCommit == bytes32(0)) revert BadSignature();
        if (msg.sender != guardian) {
            bytes32 dig = keccak256(abi.encodePacked(
                "\x19\x01",
                domainSeparator(),
                keccak256(abi.encode(ROTATE_TYPEHASH, scheme, newKeyCommit, rotateNonce))
            ));
            if (_recover(dig, authSig) != guardian) revert NotGuardian();
            rotateNonce += 1;
        }
        pendingScheme = scheme;
        pendingCommit = newKeyCommit;
        pendingReady = uint64(block.timestamp + TIMELOCK);
        pendingEpoch = lastPosted + 1;
        emit SignerRotated(scheme, newKeyCommit, pendingEpoch);
    }

    /// @notice Hash of the key that must sign `epoch`, committed before the key is used.
    ///         ECDSA commits keccak256(abi.encodePacked(address)). WOTS commits the public key.
    function commitEpochKey(uint64 epoch, uint8 scheme, bytes32 keyCommit) external onlyGuardian {
        if (keyCommit == bytes32(0) || epoch <= lastPosted) revert BadSignature();
        if (scheme == 0) epochCommits[epoch] = keyCommit;
        else if (scheme == 2) epochWots[epoch] = keyCommit;
        else revert SchemeUnavailable();
    }

    /// @notice Bits may be added. ML-DSA bits revert: this contract cannot check them.
    function setRequiredSchemes(uint8 bitmask) external onlyGuardian {
        if ((bitmask & requiredSchemes) != requiredSchemes) revert CannotLoosen();
        if ((bitmask & (BIT_MLDSA_SOL | BIT_MLDSA_PRE)) != 0) revert SchemeUnavailable();
        if ((bitmask & ~(BIT_ECDSA | BIT_WOTS)) != 0) revert SchemeUnavailable();
        requiredSchemes = bitmask;
    }

    function setCanary(address canary_) external onlyGuardian {
        if (canary_ == address(0)) revert BadSignature();
        canary = canary_;
    }

    /// @notice Guardian or the canary. Bits are OR-ed. There is no clear.
    function trip(uint32 flags, string calldata reason) external {
        if (msg.sender != guardian && msg.sender != canary) revert NotCanary();
        breaker |= flags;
        emit CircuitBreaker(flags, reason);
    }

    function _acceptEcdsa(uint64 epoch, bytes32 digest, bytes calldata sig) internal {
        address recovered = _recover(digest, sig);
        if (pendingCommit != bytes32(0) && pendingScheme == 0 && epoch == pendingEpoch) {
            if (block.timestamp < pendingReady) {
                if (recovered != signer) revert BadSignature();
                pendingEpoch = epoch + 1;
                return;
            }
            if (keccak256(abi.encodePacked(recovered)) != pendingCommit) revert BadSignature();
            signer = recovered;
            pendingCommit = bytes32(0);
            return;
        }
        bytes32 commit = epochCommits[epoch];
        if (commit != bytes32(0)) {
            if (keccak256(abi.encodePacked(recovered)) != commit) revert BadSignature();
            delete epochCommits[epoch];
            return;
        }
        if (recovered != signer) revert BadSignature();
    }

    function _acceptWots(uint64 epoch, bytes32 digest, bytes calldata sig) internal {
        bytes32 expect = epochWots[epoch];
        if (expect == bytes32(0) && pendingCommit != bytes32(0) && pendingScheme == 2 && epoch == pendingEpoch) {
            if (block.timestamp < pendingReady) revert Timelock();
            expect = pendingCommit;
        }
        if (expect == bytes32(0)) expect = wotsCommit;
        if (expect == bytes32(0)) revert SchemeUnavailable();
        if (!_wots(digest, sig, expect)) revert BadSignature();
        if (pendingCommit != bytes32(0) && pendingScheme == 2 && epoch == pendingEpoch && block.timestamp >= pendingReady) {
            wotsCommit = pendingCommit;
            pendingCommit = bytes32(0);
        }
        if (epochWots[epoch] != bytes32(0)) delete epochWots[epoch];
    }

    function _wots(bytes32 digest, bytes calldata sig, bytes32 expect) internal pure returns (bool) {
        // abi.encode of two bytes32[67] is 134 words and no offset.
        if (sig.length != 67 * 32 * 2) return false;
        (bytes32[67] memory pk, bytes32[67] memory chunks) = abi.decode(sig, (bytes32[67], bytes32[67]));
        if (keccak256(abi.encode(pk)) != expect) return false;
        uint8[67] memory sym = wotsSymbols(digest);
        for (uint256 i = 0; i < 67; i++) {
            uint256 steps = 15 - uint256(sym[i]);
            if (wotsHash(chunks[i], steps) != pk[i]) return false;
        }
        return true;
    }

    function _proof(bytes32 leaf, bytes32[] calldata proof, bytes32 root) internal pure returns (bool) {
        bytes32 h = leaf;
        for (uint256 i = 0; i < proof.length; i++) {
            bytes32 s = proof[i];
            h = h < s ? keccak256(abi.encodePacked(h, s)) : keccak256(abi.encodePacked(s, h));
        }
        return h == root;
    }

    function _recover(bytes32 hash, bytes calldata sig) internal pure returns (address) {
        if (sig.length != 65) revert BadSignature();
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 32))
            v := byte(0, calldataload(add(sig.offset, 64)))
        }
        if (uint256(s) > HALF_N) revert HighS();
        if (v < 27) v += 27;
        if (v != 27 && v != 28) revert BadSignature();
        address rec = ecrecover(hash, v, r, s);
        if (rec == address(0)) revert BadSignature();
        return rec;
    }
}
