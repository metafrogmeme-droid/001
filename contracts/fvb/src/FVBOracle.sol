// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

/// Fair-value oracle for a testnet.
///
/// The constructor refuses Ethereum mainnet (1), Base (8453), Arbitrum One
/// (42161), HyperEVM mainnet (999) and Robinhood Chain mainnet (4663).
/// Sepolia (11155111) and Robinhood Chain testnet (46630) are the EVM
/// networks this bridge names. There is no deploy script in this tree.
///
/// A reading is stored only when `k` distinct committee keys have signed
/// the EIP-712 digest of a confirmed packet, bound to `block.chainid` and
/// `address(this)`. `latestRoundData` reverts while no reading is stored.
/// It does not answer zero.
contract FVBOracle {
    bytes32 public constant PACKET_TYPEHASH = keccak256(
        "FVBPacket(bytes32 symbol,uint256 fv,uint256 bandLo,uint256 bandHi,uint8 session,bool confirmed,uint64 ts,uint32 version,bytes32 nextEpoch)"
    );
    bytes32 public constant DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );

    struct Packet {
        bytes32 symbol;
        uint256 fv;
        uint256 bandLo;
        uint256 bandHi;
        uint8 session;
        bool confirmed;
        uint64 ts;
        uint32 version;
        bytes32 nextEpoch;
    }

    struct Reading {
        uint256 fv;
        uint256 bandLo;
        uint256 bandHi;
        uint64 ts;
        bool set;
    }

    mapping(bytes32 => Reading) private _readings;
    mapping(address => bool) public isSigner;
    uint256 public threshold;
    uint256 public signerCount;

    constructor(address[] memory signers, uint256 k) {
        uint256 id = block.chainid;
        if (id == 1 || id == 8453 || id == 42161 || id == 999 || id == 4663) {
            revert("testnet only");
        }
        require(k > 0 && k <= signers.length, "threshold");
        uint256 n = 0;
        for (uint256 i = 0; i < signers.length; i++) {
            address who = signers[i];
            require(who != address(0) && !isSigner[who], "signer");
            isSigner[who] = true;
            n += 1;
        }
        threshold = k;
        signerCount = n;
    }

    function packetHash(Packet calldata p) public pure returns (bytes32) {
        return keccak256(abi.encode(
            PACKET_TYPEHASH,
            p.symbol,
            p.fv,
            p.bandLo,
            p.bandHi,
            p.session,
            p.confirmed,
            p.ts,
            p.version,
            p.nextEpoch
        ));
    }

    /// Digest for a stated chain and contract. The frozen Python vector
    /// calls this. `submit` does not: it binds the digest to this chain
    /// and this address.
    function digestFor(uint256 chainId, address verifying, Packet calldata p) public pure returns (bytes32) {
        bytes32 domain = keccak256(abi.encode(
            DOMAIN_TYPEHASH,
            keccak256(bytes("Fair-Value Bridge")),
            keccak256(bytes("1")),
            chainId,
            verifying
        ));
        return keccak256(abi.encodePacked(hex"1901", domain, packetHash(p)));
    }

    function digest(Packet calldata p) public view returns (bytes32) {
        return digestFor(block.chainid, address(this), p);
    }

    function submit(Packet calldata p, bytes[] calldata sigs) external {
        require(p.confirmed, "unconfirmed");
        require(p.fv > 0 && p.bandLo > 0 && p.bandHi > 0 && p.ts > 0, "unreadable");
        require(p.version == 1, "version");
        require(_count(digest(p), sigs) >= threshold, "threshold");
        _readings[p.symbol] = Reading({
            fv: p.fv,
            bandLo: p.bandLo,
            bandHi: p.bandHi,
            ts: p.ts,
            set: true
        });
    }

    /// The confirmed fair value, scaled by the publisher (1e8). Reverts
    /// when nothing has been stored. A missing price is not zero.
    function fvOf(bytes32 symbol) external view returns (uint256) {
        Reading storage r = _readings[symbol];
        require(r.set && r.fv > 0, "unreadable");
        return r.fv;
    }

    /// Chainlink-shaped. Reverts when this symbol has no reading, so a
    /// caller cannot treat "never published" as a price of zero.
    function latestRoundData(bytes32 symbol) external view returns (
        uint80 roundId,
        int256 answer,
        uint256 startedAt,
        uint256 updatedAt,
        uint80 answeredInRound
    ) {
        Reading storage r = _readings[symbol];
        require(r.set && r.fv > 0, "no reading");
        return (1, int256(r.fv), r.ts, r.ts, 1);
    }

    function _count(bytes32 hash, bytes[] calldata sigs) internal view returns (uint256) {
        uint256 n = 0;
        address[] memory seen = new address[](sigs.length);
        for (uint256 i = 0; i < sigs.length; i++) {
            if (sigs[i].length != 65) continue;
            bytes32 r;
            bytes32 s;
            uint8 v;
            bytes calldata sig = sigs[i];
            r = bytes32(sig[0:32]);
            s = bytes32(sig[32:64]);
            v = uint8(sig[64]);
            if (v < 27) v += 27;
            address who = ecrecover(hash, v, r, s);
            if (who == address(0) || !isSigner[who]) continue;
            bool dup = false;
            for (uint256 j = 0; j < n; j++) {
                if (seen[j] == who) {
                    dup = true;
                    break;
                }
            }
            if (dup) continue;
            seen[n] = who;
            n += 1;
        }
        return n;
    }
}
