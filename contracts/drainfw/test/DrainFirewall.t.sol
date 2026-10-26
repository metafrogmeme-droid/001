// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {DeployDrainFirewall} from "../script/Deploy.s.sol";
import {DrainErrors} from "../src/DrainErrors.sol";
import {DrainHash} from "../src/DrainHash.sol";
import {DrainHasher} from "../src/DrainHasher.sol";
import {DrainHook7579} from "../src/DrainHook7579.sol";
import {DrainPolicy} from "../src/DrainPolicy.sol";
import {DrainTypes} from "../src/DrainTypes.sol";
import {FirewallAccount} from "../src/FirewallAccount.sol";
import {DomTok, EcrecoverOnlyPermit, Permit2Mock, ReenterTok, RevertTok, Tok} from "./Mocks.sol";

interface Vm {
    function chainId(uint256) external;
    function etch(address, bytes calldata) external;
    function addr(uint256) external returns (address);
    function prank(address) external;
    function deal(address, uint256) external;
    function warp(uint256) external;
    function sign(uint256, bytes32) external returns (uint8, bytes32, bytes32);
    function expectRevert(bytes4) external;
    function expectRevert(bytes calldata) external;
    function expectRevert() external;
    function store(address, bytes32, bytes32) external;
    function skip(bool) external;
    function envOr(string calldata, string calldata) external returns (string memory);
    function resumeGasMetering() external;
    function createSelectFork(string calldata) external returns (uint256);
}

contract MSA {
    DrainPolicy public policy;
    DrainHook7579 public hook;
    address public owner;

    function install(DrainPolicy p, DrainHook7579 h, DrainPolicy.Init memory cfg) external {
        policy = p;
        hook = h;
        owner = cfg.owner;
        cfg.hook = address(h);
        p.initAccount(cfg);
        h.onInstall("");
    }

    receive() external payable {}

    function execute(bytes32 mode, bytes calldata executionCalldata) external payable {
        hook.preCheck(msg.sender, msg.value, msg.data);
        _run(mode, executionCalldata);
        hook.postCheck("");
    }

    function executeFromExecutor(bytes32 mode, bytes calldata executionCalldata) external payable {
        hook.preCheck(msg.sender, msg.value, msg.data);
        _run(mode, executionCalldata);
        hook.postCheck("");
    }

    function _run(bytes32 mode, bytes calldata executionCalldata) internal {
        uint8 callType = uint8(uint256(mode) >> 248);
        uint8 execType = uint8(uint256(mode) >> 240);
        if (callType == 0xFE || callType == 0xFF) return;
        if (callType == 0) {
            address target = address(bytes20(executionCalldata[:20]));
            uint256 value = uint256(bytes32(executionCalldata[20:52]));
            (bool ok,) = target.call{value: value}(executionCalldata[52:]);
            if (!ok && execType == 0) revert("leg");
            return;
        }
        DrainHook7579.Execution[] memory batch = abi.decode(executionCalldata, (DrainHook7579.Execution[]));
        for (uint256 i = 0; i < batch.length; i++) {
            (bool ok,) = batch[i].target.call{value: batch[i].value}(batch[i].callData);
            if (!ok && execType == 0) revert("leg");
        }
    }
}

contract DrainFirewallTest {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    address internal constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;
    bytes32 internal constant VECTOR = 0x1a61a748788ed3196a91d7fc40bdd72cbed661f3298274b1f32690279131c4b4;
    bytes32 internal constant VECTOR_B = 0xfb3c852090b3af13b3fd8530415b14d0df667582ffe91b1bf5d5dddcf530bd69;

    uint256 internal constant PK = 0xA11CE;
    address internal guardian = address(0x6111);
    address internal vault = address(0x6222);
    address internal rcpt = address(0x6333);
    address internal spender = address(0x6444);
    address internal executor = address(0x6555);

    DrainPolicy internal policy;
    DrainHasher internal hasher;
    DrainHook7579 internal hook;
    FirewallAccount internal impl;
    Tok internal token;
    address internal eoa;

    event Measured(
        uint256 policyGas,
        uint256 hookGas,
        uint256 accountGas,
        uint256 initGas,
        uint256 ungatedCold,
        uint256 gatedCold,
        uint256 ungatedWarm,
        uint256 gatedWarm
    );

    function setUp() public {
        vm.chainId(84532);
        hasher = new DrainHasher();
        policy = new DrainPolicy(PERMIT2, hasher);
        hasher.bind(address(policy));
        hook = new DrainHook7579(policy);
        impl = new FirewallAccount(policy);
        token = new Tok();
        eoa = _install(PK, _cfg(address(token)));
        token.mint(eoa, 1000);
        vm.deal(eoa, 10 ether);
        require(policy.POLICY_SLOT() == 0x4e0b939384c18a59f03c163642d25b17c6e2ba27f509b7f1e60d5cd9bf301500, "slot");
        require(impl.ACCOUNT_SLOT() == 0x2e30086a22b22174b0577010a5f98e60a12ac559423fa9effef8beb64db4c400, "aslot");
    }

    function test_erc7201_and_chain_table() public pure {
        bytes32 p = keccak256(abi.encode(uint256(keccak256("runeclaw.drainfw.v1")) - 1)) & ~bytes32(uint256(0xff));
        bytes32 a = keccak256(abi.encode(uint256(keccak256("runeclaw.firewall.account.v1")) - 1)) & ~bytes32(uint256(0xff));
        require(p == 0x4e0b939384c18a59f03c163642d25b17c6e2ba27f509b7f1e60d5cd9bf301500, "policy slot");
        require(a == 0x2e30086a22b22174b0577010a5f98e60a12ac559423fa9effef8beb64db4c400, "account slot");
        require(DrainTypes.mainnet(1) && DrainTypes.mainnet(8453) && DrainTypes.mainnet(42161), "m1");
        require(DrainTypes.mainnet(43114) && DrainTypes.mainnet(10) && DrainTypes.mainnet(137) && DrainTypes.mainnet(56), "m2");
        require(!DrainTypes.mainnet(84532) && !DrainTypes.mainnet(43113) && !DrainTypes.mainnet(421614), "testnets");
        require(DrainTypes.supports7702(84532) && DrainTypes.supports7702(421614), "7702");
        require(!DrainTypes.supports7702(43113) && !DrainTypes.supports7702(1), "no 7702");
    }

    function test_constructors_refuse_mainnet_and_fuji_delegate() public {
        vm.chainId(8453);
        vm.expectRevert(DrainErrors.Mainnet.selector);
        new DrainPolicy(PERMIT2, new DrainHasher());
        vm.chainId(43114);
        vm.expectRevert(DrainErrors.Mainnet.selector);
        new DrainHook7579(policy);
        vm.chainId(43113);
        DrainPolicy fuji = new DrainPolicy(PERMIT2, new DrainHasher());
        new DrainHook7579(fuji);
        vm.expectRevert(DrainErrors.ChainRefused.selector);
        new FirewallAccount(fuji);
        DeployDrainFirewall d = new DeployDrainFirewall();
        vm.expectRevert(DrainErrors.ChainRefused.selector);
        d.simulate(true);
        (address p, address h, address a) = d.simulate(false);
        require(p != address(0) && h != address(0) && a == address(0), "fuji module");
        vm.chainId(1);
        vm.expectRevert(DrainErrors.Mainnet.selector);
        d.simulate(false);
        vm.chainId(84532);
        (,, address delegated) = d.simulate(true);
        require(delegated != address(0), "base delegate");
        vm.chainId(421614);
        vm.expectRevert(DrainErrors.ChainRefused.selector);
        d.simulate(true);
    }

    function test_init_is_not_front_runnable() public {
        uint256 pk = 0xB0B;
        address fresh = vm.addr(pk);
        vm.etch(fresh, address(impl).code);
        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.FrontRun.selector);
        FirewallAccount(payable(fresh)).init(address(0xBAD), vault, "");
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(0xBEEF, keccak256("nope"));
        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.FrontRun.selector);
        FirewallAccount(payable(fresh)).init(guardian, vault, abi.encodePacked(r, s, v));

        DrainPolicy.Init memory cfg = _cfg(address(token));
        bytes32 digest = FirewallAccount(payable(fresh)).initDigest(cfg);
        bytes32 ethHash = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", digest));
        (v, r, s) = vm.sign(pk, ethHash);
        bytes memory sig = abi.encodePacked(r, s, v);
        cfg.tokens[0].cap = 1;
        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.FrontRun.selector);
        FirewallAccount(payable(fresh)).initFull(cfg, sig);
        cfg.tokens[0].cap = 1000;
        vm.prank(address(0xBAD));
        FirewallAccount(payable(fresh)).initFull(cfg, sig);
        require(policy.guardianOf(fresh) == guardian, "signed init");
        (uint64 scan,) = policy.pairsOf(fresh);
        require(scan == 12, "scan");

        vm.prank(address(0xBAD));
        policy.initAccount(_cfg(address(token)));
        require(policy.guardianOf(fresh) == guardian, "stranger inits self");
        require(policy.guardianOf(address(0xBAD)) == guardian, "own account");
    }

    function test_designator_and_root_key_bypass() public {
        address who = vm.addr(0xD077);
        vm.etch(who, abi.encodePacked(hex"ef0100", bytes20(address(impl))));
        require(who.code.length == 23, "len");
        require(uint8(who.code[0]) == 0xef && uint8(who.code[1]) == 0x01 && uint8(who.code[2]) == 0x00, "prefix");

        uint256 before = token.balanceOf(eoa);
        vm.prank(eoa);
        token.transfer(address(0x71EF), 25);
        require(token.balanceOf(eoa) == before - 25, "direct");
        (, uint128 used,,,) = policy.tokenOf(eoa, address(token));
        require(used == 0, "policy not consulted");
    }

    function test_known_transfer_cap_and_queue() public {
        _exec(eoa, address(token), 0, _transfer(rcpt, 40));
        require(token.balanceOf(rcpt) == 40, "paid");
        (, uint128 used,,,) = policy.tokenOf(eoa, address(token));
        require(used == 40, "used");

        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.OCAP));
        FirewallAccount(payable(eoa)).execute(address(token), 0, _transfer(rcpt, 2000));
        require(token.balanceOf(rcpt) == 40, "unchanged");

        address newbie = address(0x9991);
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Queued.selector, bytes32(0), DrainTypes.RCPT));
        FirewallAccount(payable(eoa)).execute(address(token), 0, _transfer(newbie, 50));

        vm.prank(eoa);
        bytes32 id = policy.queueOutflow(address(token), 0, _transfer(newbie, 50));
        (,, uint8 path, uint64 eta,,,,) = policy.opOf(id);
        require(path == DrainTypes.PATH_SHORT, "short");
        require(eta == block.timestamp + 24 hours, "24h");
        vm.warp(eta);
        _exec(eoa, address(token), 0, _transfer(newbie, 50));
        require(token.balanceOf(newbie) == 50, "queued paid");
        require((policy.roleOf(eoa, newbie) & DrainTypes.ROLE_RECIPIENT) != 0, "now known");

        vm.prank(eoa);
        id = policy.queueOutflow(address(token), 0, _transfer(newbie, 900));
        vm.warp(block.timestamp + 24 hours);
        _exec(eoa, address(token), 0, _transfer(newbie, 900));
        require(token.balanceOf(newbie) == 950, "over cap after delay");
    }

    function test_freeze_revoke_and_vault_exit() public {
        vm.prank(guardian);
        policy.freeze(eoa, 72 hours);
        _exec(eoa, address(token), 0, abi.encodeWithSelector(bytes4(0x095ea7b3), spender, uint256(0)));
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.FRZN));
        FirewallAccount(payable(eoa)).execute(address(token), 0, _transfer(rcpt, 1));
        _exec(eoa, address(token), 0, _transfer(vault, 15));
        require(token.balanceOf(vault) == 15, "vault while frozen");

        vm.prank(eoa);
        vm.expectRevert(DrainErrors.NotGuardian.selector);
        policy.unfreeze(eoa);
        vm.prank(guardian);
        policy.unfreeze(eoa);
        _exec(eoa, address(token), 0, _transfer(rcpt, 1));
    }

    function test_i1_worst_case_exit_is_eleven_days() public {
        uint256 t0 = block.timestamp;
        vm.prank(eoa);
        bytes32 id = policy.queueUninstall();
        (,, uint8 path, uint64 eta,,,,) = policy.opOf(id);
        require(path == DrainTypes.PATH_LONG && eta == t0 + 7 days, "long");
        vm.prank(guardian);
        policy.extendOnce(id);
        (,,, eta,,,,) = policy.opOf(id);
        require(eta == t0 + 8 days, "extended");
        vm.prank(guardian);
        vm.expectRevert(DrainErrors.BadPath.selector);
        policy.extendOnce(id);
        vm.warp(t0 + 8 days);
        vm.prank(guardian);
        policy.freeze(eoa, 72 hours);
        require(policy.frozenUntilOf(eoa) == t0 + 11 days, "11d");
        vm.warp(t0 + 11 days - 1);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.NotReady.selector);
        policy.executeOp(id);
        vm.warp(t0 + 11 days);
        vm.prank(eoa);
        policy.executeOp(id);
        require(policy.installed(eoa), "record remains");
        _exec(eoa, address(token), 0, abi.encodeWithSelector(bytes4(0x095ea7b3), spender, uint256(0)));
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.UNKN));
        FirewallAccount(payable(eoa)).execute(address(token), 0, _transfer(rcpt, 1));
        _exec(eoa, address(token), 0, _transfer(vault, 4));
        require(token.balanceOf(vault) == 4, "exit after uninstall");
    }

    function test_i2_revoke_keeps_balance_evacuate_sends_to_vault() public {
        _exec(eoa, address(token), 0, abi.encodeWithSelector(bytes4(0x095ea7b3), spender, 70));
        require(token.allowance(eoa, spender) == 70, "allow");
        uint256 before = token.balanceOf(eoa);
        vm.prank(guardian);
        FirewallAccount(payable(eoa)).guardianRevoke(DrainTypes.REV_APPROVE, address(token), spender, 0, 0, 0, bytes32(0));
        require(token.balanceOf(eoa) == before, "revoke keeps funds");
        require(token.allowance(eoa, spender) == 0, "zeroed");
        vm.prank(guardian);
        FirewallAccount(payable(eoa)).evacuate(_one(address(token)));
        require(token.balanceOf(eoa) == 0, "emptied");
        require(token.balanceOf(vault) == before, "only vault");
        require(token.balanceOf(rcpt) == 0, "not rcpt");
    }

    function test_i3_freeze_bounds_and_cooldown() public {
        vm.prank(guardian);
        vm.expectRevert(DrainErrors.BadPath.selector);
        policy.freeze(eoa, 30 minutes);
        vm.prank(guardian);
        vm.expectRevert(DrainErrors.BadPath.selector);
        policy.freeze(eoa, 73 hours);
        vm.prank(guardian);
        policy.freeze(eoa, 1 hours);
        vm.prank(guardian);
        vm.expectRevert(DrainErrors.Cooldown.selector);
        policy.freeze(eoa, 1 hours);
        vm.warp(block.timestamp + 7 days);
        vm.prank(guardian);
        policy.freeze(eoa, 2 hours);
        require(policy.frozenUntilOf(eoa) == block.timestamp + 2 hours, "second");
    }

    function testFuzz_freeze_duration(uint256 raw) public {
        uint256 d = raw % (30 days + 1);
        if (d < 1 hours || d > 72 hours) {
            vm.prank(guardian);
            vm.expectRevert(DrainErrors.BadPath.selector);
            policy.freeze(eoa, uint64(d));
        } else {
            vm.prank(guardian);
            policy.freeze(eoa, uint64(d));
            require(policy.frozenUntilOf(eoa) == block.timestamp + d, "bound");
        }
    }

    function test_i4_loosening_is_long_tightening_is_now() public {
        vm.prank(eoa);
        bytes32 id = policy.queueVault(address(0xABCD));
        (,, uint8 path, uint64 eta,,,,) = policy.opOf(id);
        require(path == DrainTypes.PATH_LONG && eta == block.timestamp + 7 days, "vault");
        vm.prank(guardian);
        vm.expectRevert(DrainErrors.NotGuardian.selector);
        policy.cancel(id);
        vm.prank(eoa);
        policy.cancel(id);

        vm.prank(eoa);
        id = policy.queueRecipient(address(0xABC1));
        (,, path, eta,,,,) = policy.opOf(id);
        require(path == DrainTypes.PATH_SHORT && eta == block.timestamp + 24 hours, "rcpt");
        vm.prank(guardian);
        policy.cancel(id);

        vm.prank(eoa);
        id = policy.queueGuardian(address(0xABC2));
        (,, path,,,,,) = policy.opOf(id);
        require(path == DrainTypes.PATH_LONG, "guardian");
        vm.prank(eoa);
        policy.setDelays(48 hours, 14 days);
        (uint64 s, uint64 l,,) = policy.delaysOf(eoa);
        require(s == 48 hours && l == 14 days, "tighter");
        vm.prank(eoa);
        id = policy.setDelays(24 hours, 7 days);
        uint8 kind;
        (kind,, path, eta,,,,) = policy.opOf(id);
        require(kind == DrainTypes.KIND_POLICY && path == DrainTypes.PATH_LONG, "loosen");
        uint64 nonce = policy.policyNonceOf(eoa);
        vm.prank(eoa);
        policy.removeRecipient(rcpt);
        require(policy.policyNonceOf(eoa) == nonce + 1, "bump");
        vm.warp(block.timestamp + 7 days);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.NotReady.selector);
        policy.executeOp(id);
    }

    function test_queue_full_and_reap() public {
        for (uint256 i = 0; i < 32; i++) {
            vm.prank(eoa);
            policy.queueRecipient(address(uint160(0x8000 + i)));
        }
        require(policy.openOpsOf(eoa) == 32, "full");
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.QueueFull.selector);
        policy.queueRecipient(address(0xBEEB));
        vm.warp(block.timestamp + 24 hours + 14 days + 1);
        vm.prank(eoa);
        policy.queueRecipient(address(0xBEEB));
        require(policy.openOpsOf(eoa) == 32, "reaped one");
    }

    function test_agent_hot_balance_and_no_queue() public {
        bytes memory data = _transfer(rcpt, 40);
        vm.prank(executor);
        FirewallAccount(payable(eoa)).executeForGrant(address(token), 0, data, 7);
        require(policy.hotOf(eoa) == 60, "debited");
        require(token.balanceOf(rcpt) == 40, "agent paid");
        vm.prank(executor);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.HOTB));
        FirewallAccount(payable(eoa)).executeForGrant(address(token), 0, _transfer(rcpt, 80), 7);
        vm.prank(executor);
        vm.expectRevert(DrainErrors.BadCaller.selector);
        FirewallAccount(payable(eoa)).executeForGrant(address(token), 0, _transfer(rcpt, 1), 9);
        vm.prank(executor);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.AGNT));
        FirewallAccount(payable(eoa)).executeForGrant(
            address(policy), 0, abi.encodeWithSelector(DrainPolicy.queueRecipient.selector, address(0x1234)), 7
        );
        vm.prank(executor);
        vm.expectRevert(DrainErrors.NotInstalled.selector);
        policy.queueRecipient(address(0x1234));
        vm.prank(eoa);
        policy.setHot(0);
        require(policy.hotOf(eoa) == 0, "lowered");
        _exec(eoa, address(token), 0, _transfer(rcpt, 1));
        vm.prank(executor);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.HOTB));
        FirewallAccount(payable(eoa)).executeForGrant(address(token), 0, _transfer(rcpt, 1), 7);
    }

    function test_modes_approvals_and_native() public {
        vm.prank(eoa);
        policy.setMode(DrainTypes.MODE_STRICT);
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.UNKN));
        FirewallAccount(payable(eoa)).execute(address(token), 0, abi.encodeWithSelector(bytes4(0x12345678)));
        vm.prank(eoa);
        bytes32 id = policy.setMode(DrainTypes.MODE_QUEUE);
        (,, uint8 path,,,,,) = policy.opOf(id);
        require(path == DrainTypes.PATH_LONG, "loosen mode");

        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.NCAP));
        FirewallAccount(payable(eoa)).execute(address(token), 0, abi.encodeWithSelector(bytes4(0x095ea7b3), spender, uint256(1) << 255));
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.OCAP));
        FirewallAccount(payable(eoa)).execute(address(token), 0, abi.encodeWithSelector(bytes4(0x39509351), spender, 1));
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Queued.selector, bytes32(0), DrainTypes.SPND));
        FirewallAccount(payable(eoa)).execute(address(token), 0, abi.encodeWithSelector(bytes4(0x095ea7b3), address(0x777), 5));

        uint256 exp = block.timestamp + 31 days;
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.EXPY));
        FirewallAccount(payable(eoa)).execute(
            PERMIT2, 0, abi.encodeWithSelector(bytes4(0x87517c45), address(token), spender, uint160(5), uint48(exp))
        );
        _exec(PERMIT2 == address(0) ? eoa : eoa, address(token), 0, abi.encodeWithSelector(bytes4(0xa22cb465), spender, false));

        vm.prank(eoa);
        FirewallAccount(payable(eoa)).execute{value: 0}(rcpt, 1 ether, "");
        require(rcpt.balance == 1 ether, "native");
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Queued.selector, bytes32(0), DrainTypes.RCPT));
        FirewallAccount(payable(eoa)).execute(address(0x4444), 1 ether, "");
    }

    function test_digest_is_built_from_the_permit() public {
        DrainHash.PermitSingle memory fixedOne = _fixed(address(0x2222222222222222222222222222222222222222));
        DrainHash.PermitSingle memory fixedTwo = _fixed(address(0x3333333333333333333333333333333333333333));
        require(DrainHash.single(fixedOne, PERMIT2) == VECTOR, "vector");
        require(_inlineSingle(fixedOne, PERMIT2) == VECTOR, "inline");
        require(DrainHash.single(fixedTwo, PERMIT2) == VECTOR_B, "other");
        require(DrainHash.single(fixedOne, PERMIT2) != DrainHash.single(fixedTwo, PERMIT2), "spender bound");

        Permit2Mock logic = new Permit2Mock();
        vm.etch(PERMIT2, address(logic).code);
        require(Permit2Mock(PERMIT2).hashSingle(fixedOne) == VECTOR, "mock");

        uint256 pk = 0xF00D;
        address fresh = vm.addr(pk);
        DrainHash.PermitSingle memory p = _single(spender);
        p.details.amount = 1000;
        bytes32 digest = DrainHash.single(p, PERMIT2);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(pk, digest);
        bytes memory sig = abi.encodePacked(r, s, v);
        Permit2Mock(PERMIT2).permit(fresh, p, sig);
        require(Permit2Mock(PERMIT2).path() == 1, "undelegated ecrecover");

        vm.etch(fresh, address(impl).code);
        vm.prank(fresh);
        FirewallAccount(payable(fresh)).initFull(_cfg(address(token)), "");
        vm.expectRevert();
        Permit2Mock(PERMIT2).permit(fresh, p, sig);
        require(FirewallAccount(payable(fresh)).isValidSignature(digest, sig) == bytes4(0xffffffff), "fail closed");

        vm.prank(fresh);
        hasher.registerPermit2Single(PERMIT2, p);
        require(policy.checkDigest(fresh, digest), "registered");
        Permit2Mock(PERMIT2).permit(fresh, p, hex"1234");
        require(Permit2Mock(PERMIT2).path() == 2, "1271");

        vm.etch(fresh, "");
        Permit2Mock(PERMIT2).permit(fresh, p, sig);
        require(Permit2Mock(PERMIT2).path() == 1, "code cleared");
    }

    function test_registration_caps_expiry_and_1271_views() public {
        DrainHash.PermitSingle memory first = _single(spender);
        first.details.amount = 600;
        DrainHash.PermitSingle memory second = first;
        second.details.nonce = 1;
        vm.prank(eoa);
        hasher.registerPermit2Single(PERMIT2, first);
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.OverCap.selector, DrainTypes.OCAP));
        hasher.registerPermit2Single(PERMIT2, second);
        require(policy.checkDigest(eoa, DrainHash.single(first, PERMIT2)), "first stays");

        DrainHash.PermitSingle memory unknown = _single(address(0x999));
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.NeedsQueue.selector, DrainTypes.SPND));
        hasher.registerPermit2Single(PERMIT2, unknown);

        vm.warp(block.timestamp + 30 days);
        require(!policy.checkDigest(eoa, DrainHash.single(first, PERMIT2)), "window end");

        vm.prank(guardian);
        policy.clearDigest(eoa, keccak256("z"));
        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.NotGuardian.selector);
        policy.clearDigest(eoa, keccak256("z"));
    }

    function test_other_permit_shapes_and_ecrecover_limit() public {
        DrainHash.PermitDetails[] memory details = new DrainHash.PermitDetails[](1);
        details[0] = _single(spender).details;
        details[0].amount = 10;
        vm.prank(eoa);
        hasher.registerPermit2Batch(PERMIT2, details, spender, 1_800_000_000);
        DrainHash.PermitTransferFrom memory t;
        t.permitted.token = address(token);
        t.permitted.amount = 10;
        t.nonce = 1;
        t.deadline = 1_800_000_000;
        vm.prank(eoa);
        hasher.registerPermit2Transfer(PERMIT2, t, spender);
        DrainHash.TokenPermissions[] memory many = new DrainHash.TokenPermissions[](1);
        many[0] = t.permitted;
        vm.prank(eoa);
        hasher.registerPermit2BatchTransfer(PERMIT2, many, spender, 3, 1_800_000_000);
        vm.prank(eoa);
        hasher.registerPermit2Witness(PERMIT2, t, spender, keccak256("w"), "Mock(bytes32 note)Mock(bytes32 note)");

        DomTok dom = new DomTok();
        address domAcct = _install(0xD061, _cfg(address(dom)));
        vm.prank(domAcct);
        hasher.registerEIP2612(address(dom), domAcct, spender, 5, 7, 1_800_000_000);
        vm.prank(domAcct);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.OverCap.selector, DrainTypes.EXPY));
        hasher.registerEIP2612(address(dom), domAcct, spender, 5, 8, 1_800_000_000);
        vm.prank(domAcct);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        hasher.registerEIP2612(address(token), domAcct, spender, 5, 7, 1_800_000_000);

        vm.prank(domAcct);
        hasher.registerEIP3009(address(dom), DrainHash.TRANSFER_AUTH, domAcct, rcpt, 5, 0, 1_800_000_000, bytes32(uint256(1)));
        vm.prank(domAcct);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.NeedsQueue.selector, DrainTypes.RCPT));
        hasher.registerEIP3009(address(dom), DrainHash.RECEIVE_AUTH, domAcct, address(0x9), 5, 0, 1_800_000_000, bytes32(uint256(2)));
        vm.prank(domAcct);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        hasher.registerEIP3009(address(dom), keccak256("nope"), domAcct, rcpt, 5, 0, 1_800_000_000, bytes32(uint256(3)));
        require(DrainHash.eip3009(bytes32(uint256(1)), keccak256("nope"), eoa, rcpt, 1, 0, 2, bytes32(0)) == bytes32(0), "type");

        EcrecoverOnlyPermit oz = new EcrecoverOnlyPermit();
        bytes32 hash = keccak256(abi.encode(eoa, spender, uint256(1), uint256(1), uint256(1_800_000_000)));
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(PK, hash);
        oz.permit(eoa, spender, 1, 1, 1_800_000_000, v, r, s);
        require(oz.asked1271() == 0, "1271 not asked");
        require(oz.lastSigner() == eoa, "ecrecover");
        require(!policy.checkDigest(eoa, hash), "not a gate");
    }

    function test_hook_batch_rescue_and_1271() public {
        MSA msa = new MSA();
        DrainPolicy.Init memory cfg = _cfg(address(token));
        cfg.owner = address(0x0A11);
        msa.install(policy, hook, cfg);
        token.mint(address(msa), 500);
        vm.deal(address(msa), 2 ether);
        require(hook.isModuleType(2) && hook.isModuleType(4) && hook.isModuleType(8), "types");
        require(!hook.isModuleType(1), "not validator");

        bytes memory one = abi.encodePacked(address(token), uint256(0), _transfer(rcpt, 20));
        vm.prank(cfg.owner);
        msa.execute(bytes32(0), one);
        require(token.balanceOf(rcpt) == 20, "hook paid");

        DrainHook7579.Execution[] memory batch = new DrainHook7579.Execution[](2);
        batch[0] = DrainHook7579.Execution(address(token), 0, _transfer(rcpt, 600));
        batch[1] = DrainHook7579.Execution(address(token), 0, _transfer(rcpt, 600));
        vm.prank(cfg.owner);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.OCAP));
        msa.execute(bytes32(uint256(1) << 248), abi.encode(batch));
        require(token.balanceOf(rcpt) == 20, "batch atomic");

        bytes32 dcall = bytes32(uint256(0xFF) << 248);
        vm.prank(cfg.owner);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.DCAL));
        msa.execute(dcall, abi.encodePacked(address(token), uint256(0), bytes("")));

        bytes32 staticMode = bytes32(uint256(0xFE) << 248);
        vm.prank(cfg.owner);
        msa.execute(staticMode, abi.encodePacked(address(token), uint256(0), bytes("")));

        vm.prank(executor);
        vm.expectRevert(DrainErrors.AgentNoQueue.selector);
        msa.executeFromExecutor(
            bytes32(0),
            abi.encodePacked(address(policy), uint256(0), abi.encodeWithSelector(DrainPolicy.queueRecipient.selector, address(0x42)))
        );

        vm.prank(cfg.owner);
        hook.evacuate(address(msa), _one(address(token)));
        require(token.balanceOf(vault) == 480, "evacuated");
        require(token.balanceOf(address(msa)) == 0, "left");

        DrainHash.PermitSingle memory p = _single(spender);
        p.details.amount = 5;
        vm.prank(address(msa));
        hasher.registerPermit2Single(PERMIT2, p);
        vm.prank(address(msa));
        hook.preValidationHookERC1271(address(msa), DrainHash.single(p, PERMIT2), "");
        vm.prank(address(msa));
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.OCAP));
        hook.preValidationHookERC1271(address(msa), keccak256("no"), "");

        vm.prank(address(msa));
        bytes32 uninstall = policy.queueUninstall();
        vm.warp(block.timestamp + 7 days);
        vm.prank(address(msa));
        hook.onUninstall("");
        uninstall;
        require(!policy.hookBound(address(msa)), "unbound");
    }

    function test_reentrancy_and_bad_calls() public {
        ReenterTok bad = new ReenterTok();
        address acct = _install(0xCE01, _cfg(address(bad)));
        bad.set(acct);
        vm.prank(acct);
        vm.expectRevert(DrainErrors.BadCaller.selector);
        FirewallAccount(payable(acct)).execute(address(bad), 0, _transfer(rcpt, 1));
        vm.store(eoa, FirewallAccount(payable(eoa)).ACCOUNT_SLOT(), bytes32(uint256(0x0101)));
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.DCAL));
        FirewallAccount(payable(eoa)).execute(address(token), 0, _transfer(rcpt, 1));
        // The cheatcode write survives the revert, so the lock stays set.
        vm.store(eoa, FirewallAccount(payable(eoa)).ACCOUNT_SLOT(), bytes32(uint256(0x01)));

        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.UNKN));
        FirewallAccount(payable(eoa)).execute(address(token), 0, hex"abcdef");
        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.BadCaller.selector);
        FirewallAccount(payable(eoa)).execute(address(token), 0, _transfer(rcpt, 1));
    }

    function test_shared_instance_gas() public {
        vm.resumeGasMetering();
        DrainHasher sharedHasher = new DrainHasher();
        DrainPolicy p = new DrainPolicy(PERMIT2, sharedHasher);
        DrainHook7579 h = new DrainHook7579(p);
        FirewallAccount a = new FirewallAccount(p);
        // Code-deposit floor: 32,000 plus 200 gas per deployed byte. Forge's
        // CREATE trace is higher by the constructor. A second account calls
        // init on this policy; it does not deploy another copy.
        uint256 policyGas = 32000 + 200 * address(p).code.length;
        uint256 hookGas = 32000 + 200 * address(h).code.length;
        uint256 accountGas = 32000 + 200 * address(a).code.length;
        uint256 hasherGas = 32000 + 200 * address(hasher).code.length;
        // Coverage instrumentation inflates runtime code past the deploy limit.
        // The uninstrumented sizes are what a chain enforces.
        if (address(p).code.length < 40000) {
            require(address(p).code.length <= 24576, "policy eip-170");
            require(address(h).code.length <= 24576 && address(a).code.length <= 24576, "eip-170");
            require(address(hasher).code.length <= 24576, "hasher eip-170");
        }
        uint256 beforeLen = address(policy).code.length;
        address second = vm.addr(0x5ECD);
        vm.etch(second, address(impl).code);
        uint256 g = gasleft();
        vm.prank(second);
        FirewallAccount(payable(second)).init(guardian, vault, "");
        uint256 initGas = g - gasleft();
        require(address(policy).code.length == beforeLen, "no second module");
        require(policy.installed(eoa) && policy.installed(second), "two accounts");
        (uint256 ungatedCold, uint256 gatedCold) = _cold();
        (uint256 ungatedWarm, uint256 gatedWarm) = _warm();
        emit Measured(policyGas, hookGas, accountGas, initGas, ungatedCold, gatedCold, ungatedWarm, gatedWarm);
        hasherGas;
        require(initGas < policyGas + hookGas, "init cheaper than a second module");
        require(gatedCold > ungatedCold && gatedWarm > ungatedWarm, "gate costs");
    }

    function _cold() internal returns (uint256 ungated, uint256 gated) {
        address bare = vm.addr(0x0BA4E);
        token.mint(bare, 100);
        vm.prank(bare);
        uint256 g = gasleft();
        token.transfer(address(0xC01D), 1);
        ungated = g - gasleft();
        address acct = _install(0x6A7E, _cfg(address(token)));
        token.mint(acct, 100);
        vm.prank(acct);
        g = gasleft();
        FirewallAccount(payable(acct)).execute(address(token), 0, _transfer(rcpt, 1));
        gated = g - gasleft();
    }

    function _warm() internal returns (uint256 ungated, uint256 gated) {
        address bare = vm.addr(0x0BA5E);
        token.mint(bare, 100);
        vm.prank(bare);
        token.transfer(address(0xC02D), 1);
        vm.prank(bare);
        uint256 g = gasleft();
        token.transfer(address(0xC02D), 1);
        ungated = g - gasleft();
        address acct = _install(0x6A7F, _cfg(address(token)));
        token.mint(acct, 100);
        vm.prank(acct);
        FirewallAccount(payable(acct)).execute(address(token), 0, _transfer(rcpt, 1));
        vm.prank(acct);
        g = gasleft();
        FirewallAccount(payable(acct)).execute(address(token), 0, _transfer(rcpt, 1));
        gated = g - gasleft();
    }

    function test_nft_self_and_window_reset() public {
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Queued.selector, bytes32(0), DrainTypes.RCPT));
        FirewallAccount(payable(eoa)).execute(address(token), 0, abi.encodeWithSelector(bytes4(0x42842e0e), eoa, rcpt, uint256(1)));
        _exec(eoa, address(token), 0, abi.encodeWithSelector(bytes4(0x42842e0e), eoa, vault, uint256(1)));
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Queued.selector, bytes32(0), DrainTypes.SELF));
        FirewallAccount(payable(eoa)).execute(eoa, 0, _transfer(rcpt, 1));

        _exec(eoa, address(token), 0, _transfer(rcpt, 1000));
        vm.prank(eoa);
        vm.expectRevert(abi.encodeWithSelector(DrainErrors.Denied.selector, DrainTypes.OCAP));
        FirewallAccount(payable(eoa)).execute(address(token), 0, _transfer(rcpt, 1));
        vm.warp(block.timestamp + 30 days);
        token.mint(eoa, 10);
        _exec(eoa, address(token), 0, _transfer(rcpt, 1));
    }

    function test_reverts_on_the_other_arm() public {
        try new DrainPolicy(address(0), hasher) returns (DrainPolicy) {
            require(false, "zero permit2");
        } catch (bytes memory err) {
            require(bytes4(err) == DrainErrors.BadTarget.selector, "permit2");
        }
        try new DrainPolicy(PERMIT2, DrainHasher(address(0))) returns (DrainPolicy) {
            require(false, "zero hasher");
        } catch (bytes memory err) {
            require(bytes4(err) == DrainErrors.BadTarget.selector, "hasher");
        }
        try new FirewallAccount(DrainPolicy(address(0))) returns (FirewallAccount) {
            require(false, "zero policy");
        } catch (bytes memory err) {
            require(bytes4(err) == DrainErrors.BadTarget.selector, "account");
        }
        try new DrainHook7579(DrainPolicy(address(0))) returns (DrainHook7579) {
            require(false, "zero hook");
        } catch (bytes memory err) {
            require(bytes4(err) == DrainErrors.BadTarget.selector, "hook");
        }

        DrainHasher freshHasher = new DrainHasher();
        vm.expectRevert(DrainErrors.BadCaller.selector);
        freshHasher.bind(address(0));
        vm.expectRevert(DrainErrors.BadCaller.selector);
        freshHasher.bind(address(policy));
        DrainPolicy secondPolicy = new DrainPolicy(PERMIT2, freshHasher);
        freshHasher.bind(address(secondPolicy));
        vm.expectRevert(DrainErrors.AlreadyInit.selector);
        freshHasher.bind(address(secondPolicy));

        address neu = vm.addr(0xA11CE0);
        DrainPolicy.Init memory bad = _cfg(address(token));
        bad.guardian = address(0);
        vm.prank(neu);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        policy.initAccount(bad);
        bad.guardian = guardian;
        bad.vault = address(0);
        vm.prank(neu);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        policy.initAccount(bad);
        bad.vault = vault;
        bad.tokens = new DrainPolicy.TokenInit[](33);
        vm.prank(neu);
        vm.expectRevert(DrainErrors.BadPath.selector);
        policy.initAccount(bad);
        bad.tokens = new DrainPolicy.TokenInit[](0);
        bad.known = new DrainPolicy.KnownInit[](33);
        vm.prank(neu);
        vm.expectRevert(DrainErrors.BadPath.selector);
        policy.initAccount(bad);
        bad.known = new DrainPolicy.KnownInit[](1);
        bad.known[0] = DrainPolicy.KnownInit({who: address(0), role: 1, grantId: 0});
        vm.prank(neu);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        policy.initAccount(bad);
        bad.known[0] = DrainPolicy.KnownInit({who: address(0x777), role: DrainTypes.ROLE_EXECUTOR, grantId: 0});
        vm.prank(neu);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        policy.initAccount(bad);
        bad.mode = 9;
        bad.known[0] = DrainPolicy.KnownInit({who: address(0x777), role: DrainTypes.ROLE_RECIPIENT, grantId: 0});
        vm.prank(neu);
        vm.expectRevert(DrainErrors.BadPath.selector);
        policy.initAccount(bad);
        bad.mode = DrainTypes.MODE_STRICT;
        bad.shortDelay = 1;
        vm.prank(neu);
        vm.expectRevert(DrainErrors.BadPath.selector);
        policy.initAccount(bad);
        bad.shortDelay = 25 hours;
        bad.longDelay = 8 days;
        bad.maxFreeze = 2 hours;
        bad.freezeCooldown = 8 days;
        bad.owner = address(0xB0B);
        vm.prank(neu);
        policy.initAccount(bad);
        require(policy.ownerOf(neu) == address(0xB0B), "owner");
        vm.prank(neu);
        vm.expectRevert(DrainErrors.AlreadyInit.selector);
        policy.initAccount(bad);

        vm.expectRevert(DrainErrors.BadCaller.selector);
        policy.reserveFor(eoa, address(token), 1, block.timestamp + 1 days, spender);
        vm.expectRevert(DrainErrors.BadCaller.selector);
        policy.keepFor(eoa, bytes32(uint256(1)), uint64(block.timestamp + 1 days));

        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        policy.queueRecipient(address(0));
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        policy.queueSpender(address(0));
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        policy.queueExecutor(address(0x1), 0);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        policy.queueGuardian(address(0));
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        policy.queueVault(address(0));
        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.NotInstalled.selector);
        policy.queueUninstall();

        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.BadCaller.selector);
        policy.check(eoa, address(token), 0, _transfer(rcpt, 1), 0);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadCaller.selector);
        policy.check(neu, address(token), 0, _transfer(rcpt, 1), 0);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.NotInstalled.selector);
        policy.check(address(0xDEAD), address(token), 0, _transfer(rcpt, 1), 0);

        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadPath.selector);
        policy.setCap(address(token), 1, 0);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadPath.selector);
        policy.setMode(9);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadPath.selector);
        policy.setDelays(1, 0);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadPath.selector);
        policy.setMaxFreeze(1);
        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.NotGuardian.selector);
        policy.freeze(eoa, 1 hours);
        vm.prank(guardian);
        vm.expectRevert(DrainErrors.BadPath.selector);
        policy.freeze(eoa, 30 minutes);
        vm.prank(guardian);
        vm.expectRevert(DrainErrors.BadPath.selector);
        policy.freeze(eoa, 80 hours);
        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.NotGuardian.selector);
        policy.unfreeze(eoa);
        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.NotInstalled.selector);
        policy.freeze(address(0x123), 1 hours);

        bytes32 missing = keccak256("no-such-op");
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.OpMissing.selector);
        policy.executeOp(missing);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.OpMissing.selector);
        policy.cancel(missing);
        vm.prank(guardian);
        vm.expectRevert(DrainErrors.OpMissing.selector);
        policy.extendOnce(missing);

        vm.prank(eoa);
        FirewallAccount(payable(eoa)).guardianRevoke(DrainTypes.REV_APPROVE, address(token), spender, 0, 0, 0, bytes32(0));
        vm.prank(eoa);
        FirewallAccount(payable(eoa)).guardianRevoke(DrainTypes.REV_FORALL, address(token), spender, 0, 0, 0, bytes32(0));
        vm.prank(eoa);
        FirewallAccount(payable(eoa)).guardianRevoke(DrainTypes.REV_LOCKDOWN, address(token), spender, 0, 0, 0, bytes32(0));
        vm.prank(eoa);
        FirewallAccount(payable(eoa)).guardianRevoke(DrainTypes.REV_NONCE, address(token), spender, 1, 0, 0, bytes32(0));
        vm.prank(eoa);
        FirewallAccount(payable(eoa)).guardianRevoke(DrainTypes.REV_UNORDERED, address(token), spender, 0, 1, 1, bytes32(0));
        vm.prank(eoa);
        FirewallAccount(payable(eoa)).guardianRevoke(DrainTypes.REV_DIGEST, address(token), spender, 0, 0, 0, keccak256("z"));
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        FirewallAccount(payable(eoa)).guardianRevoke(DrainTypes.REV_APPROVE, address(0x1234), spender, 0, 0, 0, bytes32(0));
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadPath.selector);
        FirewallAccount(payable(eoa)).guardianRevoke(9, address(token), spender, 0, 0, 0, bytes32(0));
        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.NotGuardian.selector);
        FirewallAccount(payable(eoa)).guardianRevoke(0, address(token), spender, 0, 0, 0, bytes32(0));

        address[] memory tooMany = new address[](33);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadPath.selector);
        FirewallAccount(payable(eoa)).evacuate(tooMany);
        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.BadCaller.selector);
        FirewallAccount(payable(eoa)).pull(address(token), vault);
        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.BadCaller.selector);
        FirewallAccount(payable(eoa)).zero(address(token), spender);

        FirewallAccount.Call[] memory calls = new FirewallAccount.Call[](1);
        calls[0] = FirewallAccount.Call({target: address(token), value: 0, data: _transfer(rcpt, 1)});
        vm.prank(eoa);
        FirewallAccount(payable(eoa)).executeBatch(calls);
        vm.prank(address(0xBAD));
        vm.expectRevert(DrainErrors.BadCaller.selector);
        FirewallAccount(payable(eoa)).executeBatch(calls);
        vm.prank(executor);
        vm.expectRevert(DrainErrors.BadCaller.selector);
        FirewallAccount(payable(eoa)).executeForGrant(address(token), 0, _transfer(rcpt, 1), 1);

        DrainHash.PermitDetails[] memory none = new DrainHash.PermitDetails[](0);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadPath.selector);
        hasher.registerPermit2Batch(PERMIT2, none, spender, 1_800_000_000);
        DrainHash.PermitDetails[] memory two = new DrainHash.PermitDetails[](2);
        two[0] = _single(spender).details;
        two[0].amount = 4;
        two[1] = two[0];
        two[1].nonce = 1;
        two[1].amount = 4;
        vm.prank(eoa);
        hasher.registerPermit2Batch(PERMIT2, two, spender, 1_800_000_000);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        hasher.registerPermit2Single(address(0x1), _single(spender));
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        hasher.registerEIP2612(address(token), address(0x123), spender, 1, 0, 1_800_000_000);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        hasher.registerEIP3009(address(token), DrainHash.TRANSFER_AUTH, address(0x123), rcpt, 1, 0, 1_800_000_000, bytes32(uint256(4)));
        vm.prank(address(0x123));
        vm.expectRevert(DrainErrors.NotInstalled.selector);
        hasher.registerEIP3009(address(token), DrainHash.TRANSFER_AUTH, address(0x123), rcpt, 1, 0, 1_800_000_000, bytes32(uint256(5)));

        DrainHash.TokenPermissions[] memory emptyPerms = new DrainHash.TokenPermissions[](0);
        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadPath.selector);
        hasher.registerPermit2BatchTransfer(PERMIT2, emptyPerms, spender, 9, 1_800_000_000);
        DrainHash.TokenPermissions[] memory twoPerms = new DrainHash.TokenPermissions[](2);
        twoPerms[0] = DrainHash.TokenPermissions({token: address(token), amount: 3});
        twoPerms[1] = DrainHash.TokenPermissions({token: address(token), amount: 3});
        vm.prank(eoa);
        hasher.registerPermit2BatchTransfer(PERMIT2, twoPerms, spender, 9, 1_800_000_000);

        address raw = vm.addr(0xBEE0);
        vm.etch(raw, address(impl).code);
        vm.prank(raw);
        vm.expectRevert(DrainErrors.NotInstalled.selector);
        FirewallAccount(payable(raw)).guardianRevoke(0, address(token), spender, 0, 0, 0, bytes32(0));

        vm.prank(eoa);
        vm.expectRevert(DrainErrors.BadTarget.selector);
        hasher.registerEIP3009(address(token), DrainHash.TRANSFER_AUTH, eoa, rcpt, 1, 0, 1_800_000_000, bytes32(uint256(9)));

        vm.deal(address(this), 1 ether);
        (bool paid,) = payable(eoa).call{value: 1}("");
        require(paid, "receive");
    }

    function _install(uint256 pk, DrainPolicy.Init memory cfg) internal returns (address acct) {
        acct = vm.addr(pk);
        vm.etch(acct, address(impl).code);
        vm.prank(acct);
        FirewallAccount(payable(acct)).initFull(cfg, "");
    }

    function _cfg(address tok) internal view returns (DrainPolicy.Init memory cfg) {
        cfg.guardian = guardian;
        cfg.vault = vault;
        cfg.mode = DrainTypes.MODE_QUEUE;
        cfg.hot = 100;
        cfg.scanBlock = 12;
        cfg.pairsHash = bytes32(uint256(0x1234));
        cfg.tokens = new DrainPolicy.TokenInit[](2);
        cfg.tokens[0] = DrainPolicy.TokenInit({token: tok, cap: 1000, windowLen: 30 days});
        cfg.tokens[1] = DrainPolicy.TokenInit({token: address(0), cap: 5 ether, windowLen: 30 days});
        cfg.known = new DrainPolicy.KnownInit[](3);
        cfg.known[0] = DrainPolicy.KnownInit({who: rcpt, role: DrainTypes.ROLE_RECIPIENT, grantId: 0});
        cfg.known[1] = DrainPolicy.KnownInit({who: spender, role: DrainTypes.ROLE_SPENDER, grantId: 0});
        cfg.known[2] = DrainPolicy.KnownInit({who: executor, role: DrainTypes.ROLE_EXECUTOR, grantId: 7});
    }

    function _exec(address acct, address target, uint256 value, bytes memory data) internal {
        vm.prank(acct);
        FirewallAccount(payable(acct)).execute(target, value, data);
    }

    function _transfer(address to, uint256 amount) internal pure returns (bytes memory) {
        return abi.encodeWithSelector(bytes4(0xa9059cbb), to, amount);
    }

    function _one(address a) internal pure returns (address[] memory list) {
        list = new address[](1);
        list[0] = a;
    }

    function _fixed(address who) internal pure returns (DrainHash.PermitSingle memory p) {
        p.details.token = address(0x1111111111111111111111111111111111111111);
        p.details.amount = 1000;
        p.details.expiration = 1_800_000_000;
        p.details.nonce = 0;
        p.spender = who;
        p.sigDeadline = 1_800_000_000;
    }

    function _single(address who) internal view returns (DrainHash.PermitSingle memory p) {
        p.details.token = address(token);
        p.details.amount = 1000;
        p.details.expiration = 1_800_000_000;
        p.details.nonce = 0;
        p.spender = who;
        p.sigDeadline = 1_800_000_000;
    }

    function _inlineSingle(DrainHash.PermitSingle memory p, address permit2) internal view returns (bytes32) {
        bytes32 detailsType = keccak256("PermitDetails(address token,uint160 amount,uint48 expiration,uint48 nonce)");
        bytes32 singleType = keccak256(
            "PermitSingle(PermitDetails details,address spender,uint256 sigDeadline)PermitDetails(address token,uint160 amount,uint48 expiration,uint48 nonce)"
        );
        bytes32 domainType = keccak256("EIP712Domain(string name,uint256 chainId,address verifyingContract)");
        bytes32 detailsHash = keccak256(abi.encode(detailsType, p.details.token, p.details.amount, p.details.expiration, p.details.nonce));
        bytes32 structHash = keccak256(abi.encode(singleType, detailsHash, p.spender, p.sigDeadline));
        bytes32 domain = keccak256(abi.encode(domainType, keccak256("Permit2"), block.chainid, permit2));
        return keccak256(abi.encodePacked(hex"1901", domain, structHash));
    }
}

contract DrainForkTest {
    Vm internal constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    address internal constant PERMIT2 = 0x000000000022D473030F116dDEE9F6B43aC78BA3;

    function test_base_sepolia_fork_skips_without_rpc() public {
        string memory rpc = vm.envOr("BASE_SEPOLIA_RPC", string(""));
        if (bytes(rpc).length == 0) {
            vm.skip(true);
            return;
        }
        vm.createSelectFork(rpc);
        DrainPolicy p = new DrainPolicy(PERMIT2, new DrainHasher());
        require(DrainTypes.supports7702(block.chainid), "base 7702");
        new FirewallAccount(p);
    }

    function test_arbitrum_sepolia_fork_skips_without_rpc() public {
        string memory rpc = vm.envOr("ARBITRUM_SEPOLIA_RPC", string(""));
        if (bytes(rpc).length == 0) {
            vm.skip(true);
            return;
        }
        vm.createSelectFork(rpc);
        require(DrainTypes.supports7702(block.chainid), "arb 7702");
        new DrainPolicy(PERMIT2, new DrainHasher());
    }

    function test_fuji_fork_skips_without_rpc() public {
        string memory rpc = vm.envOr("AVALANCHE_FUJI_RPC", string(""));
        if (bytes(rpc).length == 0) {
            vm.skip(true);
            return;
        }
        vm.createSelectFork(rpc);
        require(!DrainTypes.supports7702(block.chainid), "fuji");
        DrainPolicy p = new DrainPolicy(PERMIT2, new DrainHasher());
        new DrainHook7579(p);
    }
}
