// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {SaRVerifier} from "../src/SaRVerifier.sol";
import {ISaRVerifier} from "../src/ISaRVerifier.sol";
import {QuantumCanary} from "../src/QuantumCanary.sol";
import {SaRGovernorAdapter} from "../src/SaRGovernorAdapter.sol";
import {MorphoPreLiqSwitcher} from "../src/MorphoPreLiqSwitcher.sol";

interface Vm {
    function addr(uint256) external pure returns (address);
    function sign(uint256, bytes32) external pure returns (uint8, bytes32, bytes32);
    function warp(uint256) external;
    function chainId(uint256) external;
    function computeCreateAddress(address, uint256) external pure returns (address);
    function prank(address) external;
}

contract SaRVerifierTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 constant PK = 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80;
    uint256 constant OTHER = 0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d;
    uint256 constant NEXT = 0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a;
    uint256 constant N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141;
    bytes32 constant ROOT = 0x1111111111111111111111111111111111111111111111111111111111111111;
    uint8 constant ECDSA = 1;
    uint8 constant MLDSA = 2;
    uint8 constant WOTS = 4;

    function _fresh() internal returns (SaRVerifier v, address guard) {
        vm.warp(1_000_000);
        vm.chainId(84532);
        guard = vm.addr(OTHER);
        address predicted = vm.computeCreateAddress(address(this), 1);
        v = new SaRVerifier(guard, vm.addr(PK));
        require(address(v) == predicted, "create");
    }

    function _sig(SaRVerifier v, uint64 epoch, bytes32 root, uint64 l1, uint256 pk)
        internal
        view
        returns (bytes memory)
    {
        (uint8 vv, bytes32 r, bytes32 s) = vm.sign(
            pk, v.epochDigest(epoch, root, l1, ISaRVerifier.Scheme.SECP256K1_EIP712)
        );
        return abi.encodePacked(r, s, vv);
    }

    function _post(SaRVerifier v, uint64 epoch, bytes32 root, uint256 pk) internal {
        v.postEpoch(epoch, root, 100, ISaRVerifier.Scheme.SECP256K1_EIP712, _sig(v, epoch, root, 100, pk));
    }

    function _metric(uint32 flags) internal pure returns (ISaRVerifier.Metric memory m) {
        m.asset = 0;
        m.dex = 0;
        m.l1Block = 100;
        m.ts = 1000;
        m.seq = 1;
        m.sAdjBps = 150;
        m.neffE2 = 160;
        m.cr1Bps = 7500;
        m.ld2UsdE6 = 5_000_000;
        m.tsar95UsdE6 = 0;
        m.flags = flags;
    }

    function _selector(address who, bytes memory data) internal returns (bytes4) {
        (bool ok, bytes memory err) = who.call(data);
        if (ok || err.length < 4) return bytes4(0);
        bytes4 sel;
        assembly { sel := mload(add(err, 32)) }
        return sel;
    }

    function test_wrong_chain_reverts() public {
        vm.chainId(1);
        bool failed = false;
        try new SaRVerifier(vm.addr(OTHER), vm.addr(PK)) {
            failed = false;
        } catch {
            failed = true;
        }
        require(failed, "mainnet");
        vm.chainId(84532);
    }

    function test_rejects_a_zero_guardian_or_signer() public {
        vm.chainId(84532);
        bool failed = false;
        try new SaRVerifier(address(0), vm.addr(PK)) { failed = false; } catch { failed = true; }
        require(failed, "guardian");
        failed = false;
        try new SaRVerifier(vm.addr(OTHER), address(0)) { failed = false; } catch { failed = true; }
        require(failed, "signer");
    }

    function test_accepts_a_fresh_epoch() public {
        (SaRVerifier v,) = _fresh();
        _post(v, 1, ROOT, PK);
        require(v.roots(1) == ROOT, "root");
        require(v.lastPosted() == 1, "last");
    }

    function test_rejects_a_tampered_root() public {
        (SaRVerifier v,) = _fresh();
        bytes memory sig = _sig(v, 1, ROOT, 100, PK);
        bytes4 sel = _selector(
            address(v),
            abi.encodeCall(
                v.postEpoch,
                (1, bytes32(uint256(ROOT) ^ 1), 100, ISaRVerifier.Scheme.SECP256K1_EIP712, sig)
            )
        );
        require(sel == SaRVerifier.BadSignature.selector || sel == SaRVerifier.HighS.selector, "root");
    }

    function test_rejects_the_wrong_signer() public {
        (SaRVerifier v,) = _fresh();
        bytes4 sel = _selector(
            address(v),
            abi.encodeCall(
                v.postEpoch,
                (1, ROOT, 100, ISaRVerifier.Scheme.SECP256K1_EIP712, _sig(v, 1, ROOT, 100, OTHER))
            )
        );
        require(sel == SaRVerifier.BadSignature.selector, "signer");
    }

    function test_rejects_a_high_s() public {
        (SaRVerifier v,) = _fresh();
        (uint8 vv, bytes32 r, bytes32 s) = vm.sign(
            PK, v.epochDigest(1, ROOT, 100, ISaRVerifier.Scheme.SECP256K1_EIP712)
        );
        bytes32 high = bytes32(N - uint256(s));
        uint8 flipped = vv == 27 ? 28 : 27;
        bytes memory sig = abi.encodePacked(r, high, flipped);
        bytes4 sel = _selector(
            address(v),
            abi.encodeCall(v.postEpoch, (1, ROOT, 100, ISaRVerifier.Scheme.SECP256K1_EIP712, sig))
        );
        require(sel == SaRVerifier.HighS.selector, "malleable");
    }

    function test_mldsa_fails_closed() public {
        (SaRVerifier v,) = _fresh();
        bytes4 sel = _selector(
            address(v),
            abi.encodeCall(v.postEpoch, (1, ROOT, 100, ISaRVerifier.Scheme.MLDSA44_SOL, hex"01"))
        );
        require(sel == SaRVerifier.SchemeUnavailable.selector, "sol");
        sel = _selector(
            address(v),
            abi.encodeCall(v.postEpoch, (1, ROOT, 100, ISaRVerifier.Scheme.MLDSA44_PRECOMPILE, hex"01"))
        );
        require(sel == SaRVerifier.SchemeUnavailable.selector, "pre");
        vm.prank(vm.addr(OTHER));
        sel = _selector(address(v), abi.encodeCall(v.setRequiredSchemes, (ECDSA | MLDSA)));
        require(sel == SaRVerifier.SchemeUnavailable.selector, "require");
    }

    function test_merkle_proof_and_stale_and_absent() public {
        (SaRVerifier v,) = _fresh();
        require(v.metricLeaf(_metric(0)) == 0xe6567fc691c1dc84feceef775c4646f1be33369e086c85e48f338f8998ef44dd, "leaf");
        ISaRVerifier.Metric memory m = _metric(0);
        m.ts = 1_000_000;
        bytes32 leaf = v.metricLeaf(m);
        _post(v, 1, leaf, PK);
        require(v.verifyMetric(m, new bytes32[](0), 1), "proof");
        v.commitMetric(1, m, new bytes32[](0));
        (ISaRVerifier.Metric memory got, uint64 age) = v.latest(0, 0);
        require(got.sAdjBps == 150 && age == 0, "latest");
        require(!v.isStale(0, 0, 180), "fresh");
        vm.warp(1_000_000 + 180);
        require(!v.isStale(0, 0, 180), "boundary");
        vm.warp(1_000_000 + 181);
        require(v.isStale(0, 0, 180), "old");
        m.ts = 1_000_000 + 500;
        bytes32 futureLeaf = v.metricLeaf(m);
        _post(v, 2, futureLeaf, PK);
        v.commitMetric(2, m, new bytes32[](0));
        require(v.isStale(0, 0, 180), "future");
        require(v.isStale(9, 9, 180), "absent");
        (, uint64 absentAge) = v.latest(9, 9);
        require(absentAge == type(uint64).max, "age");
        ISaRVerifier.Metric memory bad = m;
        bad.flags = 1;
        require(!v.verifyMetric(bad, new bytes32[](0), 1), "stale flag");
        bad.sAdjBps = 1;
        bad.flags = 0;
        require(!v.verifyMetric(bad, new bytes32[](0), 1), "tamper");
    }

    function test_trip_only_tightens() public {
        (SaRVerifier v, address guard) = _fresh();
        vm.prank(guard);
        v.trip(1, "first");
        require(v.breaker() == 1, "one");
        vm.prank(guard);
        v.trip(4, "second");
        require(v.breaker() == 5, "or");
        bytes4 sel = _selector(address(v), abi.encodeCall(v.trip, (8, "no")));
        require(sel == SaRVerifier.NotCanary.selector, "stranger");
        require(v.breaker() == 5, "held");
        ISaRVerifier.Metric memory m = _metric(0);
        _post(v, 1, v.metricLeaf(m), PK);
        v.commitMetric(1, m, new bytes32[](0));
        require(v.isStale(0, 0, 10_000_000), "breaker");
        require(!v.verifyMetric(m, new bytes32[](0), 1), "verify");
    }

    function test_canary_is_the_only_extra_caller() public {
        (SaRVerifier v, address guard) = _fresh();
        QuantumCanary c = new QuantumCanary(guard, v);
        vm.prank(guard);
        v.setCanary(address(c));
        vm.prank(guard);
        c.signal(1, "nonce");
        require(v.breaker() == 1, "signalled");
        vm.prank(vm.addr(PK));
        bool failed = false;
        try c.signal(8, "no") { failed = false; } catch { failed = true; }
        require(failed, "watcher");
        require(v.breaker() == 1, "still");
        vm.chainId(1);
        failed = false;
        try new QuantumCanary(guard, v) { failed = false; } catch { failed = true; }
        require(failed, "chain");
        vm.chainId(84532);
    }

    function test_rotation_is_timelocked_and_hash_committed() public {
        (SaRVerifier v, address guard) = _fresh();
        address next = vm.addr(NEXT);
        bytes32 commit = keccak256(abi.encodePacked(next));
        vm.prank(guard);
        v.rotateSigner(0, commit, "");
        require(v.pendingEpoch() == 1, "epoch");
        vm.warp(1_000_000 + 7 days - 1);
        bytes4 sel = _selector(
            address(v),
            abi.encodeCall(
                v.postEpoch,
                (1, ROOT, 100, ISaRVerifier.Scheme.SECP256K1_EIP712, _sig(v, 1, ROOT, 100, NEXT))
            )
        );
        require(sel == SaRVerifier.BadSignature.selector, "early");
        _post(v, 1, ROOT, PK);
        require(v.pendingEpoch() == 2, "slid");
        vm.warp(1_000_000 + 7 days);
        _post(v, 2, ROOT, NEXT);
        require(v.signer() == next, "rotated");
        sel = _selector(
            address(v),
            abi.encodeCall(
                v.postEpoch,
                (3, ROOT, 100, ISaRVerifier.Scheme.SECP256K1_EIP712, _sig(v, 3, ROOT, 100, PK))
            )
        );
        require(sel == SaRVerifier.BadSignature.selector, "old");
    }

    function test_epoch_key_is_one_use() public {
        (SaRVerifier v, address guard) = _fresh();
        address next = vm.addr(NEXT);
        vm.prank(guard);
        v.commitEpochKey(4, 0, keccak256(abi.encodePacked(next)));
        _post(v, 4, ROOT, NEXT);
        require(v.roots(4) == ROOT, "posted");
        bytes4 sel = _selector(
            address(v),
            abi.encodeCall(
                v.postEpoch,
                (5, ROOT, 100, ISaRVerifier.Scheme.SECP256K1_EIP712, _sig(v, 5, ROOT, 100, NEXT))
            )
        );
        require(sel == SaRVerifier.BadSignature.selector, "reuse");
    }

    function test_wots_hybrid_required_and_verified() public {
        (SaRVerifier v, address guard) = _fresh();
        bytes32 digest = v.epochDigest(1, ROOT, 100, ISaRVerifier.Scheme.WOTS_HASHCHAIN);
        (bytes32 commit, bytes memory sig) = _wotsSig(v, digest);
        vm.prank(guard);
        v.setRequiredSchemes(ECDSA | WOTS);
        _post(v, 1, ROOT, PK);
        require(v.roots(1) == bytes32(0), "waiting");
        vm.prank(guard);
        v.commitEpochKey(1, 2, commit);
        v.postEpoch(1, ROOT, 100, ISaRVerifier.Scheme.WOTS_HASHCHAIN, sig);
        require(v.roots(1) == ROOT, "both");
        sig[100] = bytes1(uint8(sig[100]) ^ 0x01);
        (bytes32 commit2, bytes memory sig2) = _wotsSig(v, v.epochDigest(2, ROOT, 100, ISaRVerifier.Scheme.WOTS_HASHCHAIN));
        vm.prank(guard);
        v.commitEpochKey(2, 2, commit2);
        _post(v, 2, ROOT, PK);
        sig2[80] = bytes1(uint8(sig2[80]) ^ 0xff);
        bytes4 sel = _selector(
            address(v),
            abi.encodeCall(v.postEpoch, (2, ROOT, 100, ISaRVerifier.Scheme.WOTS_HASHCHAIN, sig2))
        );
        require(sel == SaRVerifier.BadSignature.selector, "tampered wots");
    }

    function test_cannot_drop_a_required_scheme() public {
        (SaRVerifier v, address guard) = _fresh();
        vm.prank(guard);
        v.setRequiredSchemes(ECDSA | WOTS);
        vm.prank(guard);
        bytes4 sel = _selector(address(v), abi.encodeCall(v.setRequiredSchemes, (ECDSA)));
        require(sel == SaRVerifier.CannotLoosen.selector, "drop");
    }

    /// ethers v6 TypedDataEncoder.hash for this epoch, chain and verifying contract.
    function test_matches_the_ethers_epoch_digest_and_signature() public {
        (SaRVerifier v,) = _fresh();
        require(address(v) == 0x5615dEB798BB3E4dFa0139dFa1b3D433Cc23b72f, "addr");
        bytes32 want = 0x809bcfd0fa8ed9a311a251632dedfda7ebbac924d94a4958df7712dfcdee2d8b;
        require(
            v.epochDigest(1, ROOT, 100, ISaRVerifier.Scheme.SECP256K1_EIP712) == want,
            "digest"
        );
        bytes memory sig = hex"fc904f18e7261bb12d7da3fd9a8212d8d4c119980845279fa6216204a6aadc585fce53a998115fa3ac3773341fd2cb15a14af7c5aba6af342b5df9943bd3b6771b";
        v.postEpoch(1, ROOT, 100, ISaRVerifier.Scheme.SECP256K1_EIP712, sig);
        require(v.roots(1) == ROOT, "posted");
    }

    function _wotsSig(SaRVerifier v, bytes32 digest) internal pure returns (bytes32 commit, bytes memory sig) {
        bytes32[67] memory sk;
        bytes32[67] memory pk;
        bytes32[67] memory chunks;
        uint8[67] memory sym = v.wotsSymbols(digest);
        for (uint256 i = 0; i < 67; i++) {
            sk[i] = keccak256(abi.encodePacked("sar-wots-v1", i));
            pk[i] = v.wotsHash(sk[i], 15);
            chunks[i] = v.wotsHash(sk[i], sym[i]);
        }
        commit = keccak256(abi.encode(pk));
        sig = abi.encode(pk, chunks);
    }

    function test_euler_liquidate_is_bit_11_not_12() public {
        vm.chainId(84532);
        address guard = vm.addr(OTHER);
        EulerVaultMock vault = new EulerVaultMock();
        StaleProbe probe = new StaleProbe();
        SaRGovernorAdapter a = new SaRGovernorAdapter(
            address(vault), address(0xBEEF), address(probe), guard, 0, 0, 5000, 9000, 6000, 9500
        );
        require(a.opLiquidate() == uint32(1 << 11), "bit11");
        require(a.opLiquidate() != uint32(1 << 12), "not12");
        require(a.opFlashloan() == uint32(1 << 12), "flash");
        require(a.isLiquidate(uint32(1 << 11)), "yes");
        require(!a.isLiquidate(uint32(1 << 12)), "no");
        uint32 ops = a.hookedOps();
        require((ops & uint32(1 << 11)) == uint32(1 << 11), "hooked");
        require((ops & uint32(1 << 12)) == 0, "not flash");
        probe.set(false);
        vm.prank(guard);
        a.lowerLTV(7000, 7500, uint32(1 days));
        require(vault.calls() == 1, "lowered");
        require(vault.borrowLTV() == 7000 && vault.liquidationLTV() == 7500, "values");
        vm.prank(guard);
        bytes4 sel = _selector(address(a), abi.encodeCall(a.lowerLTV, (7000, 7500, uint32(1 days))));
        require(sel == SaRGovernorAdapter.NotLower.selector, "equal");
        require(vault.calls() == 1, "held");
        probe.set(true);
        vm.prank(guard);
        sel = _selector(address(a), abi.encodeCall(a.lowerLTV, (6000, 7000, uint32(1 days))));
        require(sel == SaRGovernorAdapter.NoAction.selector, "stale");
        require(vault.calls() == 1, "unstale");
    }

    function test_morpho_switcher_authorizes_only_the_tighter_contract() public {
        vm.chainId(84532);
        MorphoAuthMock morpho = new MorphoAuthMock();
        PreLiqMock tight = new PreLiqMock(5e17);
        PreLiqMock loose = new PreLiqMock(8e17);
        StressMock probe = new StressMock();
        address borrower = vm.addr(OTHER);
        MorphoPreLiqSwitcher s = new MorphoPreLiqSwitcher(
            address(morpho), address(tight), address(loose), address(probe), borrower, 0, 0, 300
        );
        probe.set(false, 301, 10);
        vm.prank(borrower);
        s.activate();
        require(morpho.calls() == 1, "armed");
        require(morpho.isAuthorized(address(s), address(tight)), "tight");
        require(!morpho.isAuthorized(address(s), address(loose)), "loose");
        require(morpho.lastAuthorized() == address(tight) && morpho.lastFlag(), "only tight");
        vm.prank(borrower);
        s.activate();
        require(morpho.calls() == 1, "once");

        MorphoAuthMock heldMorpho = new MorphoAuthMock();
        StressMock quiet = new StressMock();
        MorphoPreLiqSwitcher held = new MorphoPreLiqSwitcher(
            address(heldMorpho), address(tight), address(loose), address(quiet), borrower, 0, 0, 300
        );
        quiet.set(true, 900, 10);
        vm.prank(borrower);
        bytes4 sel = _selector(address(held), abi.encodeCall(held.activate, ()));
        require(sel == MorphoPreLiqSwitcher.NoAction.selector, "stale");
        require(heldMorpho.calls() == 0, "no stale write");
        quiet.set(false, 300, 10);
        vm.prank(borrower);
        sel = _selector(address(held), abi.encodeCall(held.activate, ()));
        require(sel == MorphoPreLiqSwitcher.NoAction.selector, "boundary");
        require(heldMorpho.calls() == 0, "no boundary write");
        quiet.set(false, 900, type(uint64).max);
        vm.prank(borrower);
        sel = _selector(address(held), abi.encodeCall(held.activate, ()));
        require(sel == MorphoPreLiqSwitcher.NoAction.selector, "unset");
        require(heldMorpho.calls() == 0, "no unset write");
        quiet.set(false, 900, 10);
        vm.prank(vm.addr(PK));
        sel = _selector(address(held), abi.encodeCall(held.activate, ()));
        require(sel == MorphoPreLiqSwitcher.NotBorrower.selector, "stranger");
        require(heldMorpho.calls() == 0, "no stranger write");
    }

    function test_morpho_switcher_reverts_off_base_sepolia_and_on_a_zero() public {
        vm.chainId(1);
        bool failed = false;
        try new MorphoPreLiqSwitcher(address(1), address(2), address(3), address(4), address(5), 0, 0, 300) {
            failed = false;
        } catch {
            failed = true;
        }
        require(failed, "mainnet");
        vm.chainId(84532);
        PreLiqMock tight = new PreLiqMock(5e17);
        PreLiqMock loose = new PreLiqMock(8e17);
        PreLiqMock same = new PreLiqMock(5e17);
        PreLiqMock zeroL = new PreLiqMock(0);
        StressMock probe = new StressMock();
        address borrower = vm.addr(OTHER);
        failed = false;
        try new MorphoPreLiqSwitcher(address(0), address(tight), address(loose), address(probe), borrower, 0, 0, 300) {
            failed = false;
        } catch {
            failed = true;
        }
        require(failed, "zero");
        failed = false;
        try new MorphoPreLiqSwitcher(address(1), address(tight), address(same), address(probe), borrower, 0, 0, 300) {
            failed = false;
        } catch {
            failed = true;
        }
        require(failed, "not tighter");
        failed = false;
        try new MorphoPreLiqSwitcher(address(1), address(zeroL), address(loose), address(probe), borrower, 0, 0, 300) {
            failed = false;
        } catch {
            failed = true;
        }
        require(failed, "zero lltv");
        failed = false;
        try new MorphoPreLiqSwitcher(address(1), address(tight), address(loose), address(probe), borrower, 0, 0, 0) {
            failed = false;
        } catch {
            failed = true;
        }
        require(failed, "threshold");
    }
}

contract EulerVaultMock {
    uint16 public borrowLTV = 8000;
    uint16 public liquidationLTV = 8500;
    uint256 public calls;

    function LTVFull(address)
        external
        view
        returns (uint16, uint16, uint16, uint48, uint32)
    {
        return (borrowLTV, liquidationLTV, liquidationLTV, 0, 0);
    }

    function setLTV(address, uint16 borrow, uint16 liq, uint32) external {
        calls += 1;
        borrowLTV = borrow;
        liquidationLTV = liq;
    }
}

contract StaleProbe {
    bool public stale;
    function set(bool v) external { stale = v; }
    function isStale(uint32, uint16, uint64) external view returns (bool) { return stale; }
}

contract MorphoAuthMock {
    mapping(address => mapping(address => bool)) public isAuthorized;
    uint256 public calls;
    address public lastAuthorized;
    bool public lastFlag;

    function setAuthorization(address authorized, bool newIsAuthorized) external {
        calls += 1;
        lastAuthorized = authorized;
        lastFlag = newIsAuthorized;
        isAuthorized[msg.sender][authorized] = newIsAuthorized;
    }
}

contract PreLiqMock {
    uint256 public preLltv;
    constructor(uint256 v) { preLltv = v; }
    function preLiquidationParams()
        external
        view
        returns (uint256, uint256, uint256, uint256, uint256, address)
    {
        return (preLltv, 0, 0, 0, 0, address(0));
    }
}

contract StressMock {
    bool public stale;
    uint32 public sAdj;
    uint64 public age;
    function set(bool s, uint32 bps, uint64 a) external {
        stale = s;
        sAdj = bps;
        age = a;
    }
    function isStale(uint32, uint16, uint64) external view returns (bool) { return stale; }
    function latest(uint32, uint16) external view returns (uint32, uint64) { return (sAdj, age); }
}
