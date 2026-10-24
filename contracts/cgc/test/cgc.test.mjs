/**
 * Conservation-Gated Collateral. Compiles the contracts and runs the bytecode
 * on an in-process EVM. No network, no mainnet fork, no deploy script.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { VM } from '@ethereumjs/vm';
import { Common, Hardfork } from '@ethereumjs/common';
import { Account, Address, hexToBytes, bytesToHex } from '@ethereumjs/util';
import { Interface, AbiCoder, getBytes, keccak256, id, Wallet, Signature } from 'ethers';
import solc from 'solc';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => readFileSync(join(ROOT, name), 'utf8');
const chains = JSON.parse(read('chains.json'));
const hyper = JSON.parse(read('fixtures/hyperevm.json'));
const abiCoder = AbiCoder.defaultAbiCoder();

const input = {
  language: 'Solidity',
  sources: {
    'CGC.sol': { content: read('CGC.sol') },
    'CGCGwrsETH.sol': { content: read('CGCGwrsETH.sol') },
    'CGCMorphoRoute.sol': { content: read('CGCMorphoRoute.sol') },
    'fixtures.sol': { content: read('test/fixtures.sol') },
  },
  settings: {
    optimizer: { enabled: true, runs: 200 },
    evmVersion: 'paris',
    outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object', 'evm.deployedBytecode.object'] } },
  },
};
const compiled = JSON.parse(solc.compile(JSON.stringify(input)));
const errors = (compiled.errors || []).filter((e) => e.severity === 'error');
if (errors.length) throw new Error(errors.map((e) => e.formattedMessage).join('\n'));

const art = (file, name) => compiled.contracts[file][name];
const ifaceOf = (a) => new Interface(a.abi);

const GAS = 30_000_000n;
const E18 = 10n ** 18n;
const T0 = 1_700_000_000n;
const WEEK = 7n * 86400n;
const ETH = 11155111;
const BASE = 84532;
const UNI = 1301;
const ARB = 421614;
const HYPER = 998;

const alice = '0x' + '11'.repeat(20);
const bob = '0x' + '22'.repeat(20);
const carol = '0x' + '33'.repeat(20);
const dave = '0x' + '44'.repeat(20);
const FUND = [alice, bob, carol, dave];

const ASSET = id('cgc-asset');
const EVIDENCE = id('nonce-gap');
const PATH = id('lz');
const OPEN = 1n;
const CONFIRMED = 2n;
const REFUNDED = 3n;
const SLASHED = 4n;

function commitment(num, den, evidence = EVIDENCE) {
  return keccak256(abiCoder.encode(['bytes32', 'uint256', 'uint256'], [evidence, num, den]));
}

function reason(exec) {
  const ret = bytesToHex(exec.returnValue || new Uint8Array());
  if (ret.startsWith('0x08c379a0')) {
    return new Interface(['function Error(string)']).decodeFunctionData('Error', ret)[0];
  }
  return exec.exceptionError ? String(exec.exceptionError.error) : null;
}

function header(clock) {
  return {
    timestamp: clock, number: clock, coinbase: Address.zero(), difficulty: 0n,
    prevRandao: new Uint8Array(32), gasLimit: GAS, baseFeePerGas: 0n,
    getBlobGasPrice: () => 0n,
  };
}

async function newVm(chainId) {
  const common = Common.custom({ chainId }, { hardfork: Hardfork.Shanghai });
  const vm = await VM.create({ common });
  for (const who of FUND) {
    await vm.stateManager.putAccount(new Address(hexToBytes(who)), new Account(0n, 1_000_000n * E18));
  }
  return vm;
}

function deployData(a, args) {
  const face = ifaceOf(a);
  const code = a.evm.bytecode.object;
  const tail = args === undefined ? '' : face.encodeDeploy(args).slice(2);
  return '0x' + code + tail;
}

async function boot(chainId) {
  const vm = await newVm(chainId);
  let clock = T0;
  async function raw(from, to, data, value = 0n, ts) {
    if (ts !== undefined) clock = BigInt(ts);
    const opts = {
      caller: new Address(hexToBytes(from)), data: getBytes(data), gasLimit: GAS,
      value: BigInt(value), block: { header: header(clock) },
    };
    if (to) opts.to = new Address(hexToBytes(to));
    return vm.evm.runCall(opts);
  }
  async function deploy(a, args, ts) {
    const r = await raw(alice, null, deployData(a, args), 0n, ts);
    assert.ok(!r.execResult.exceptionError, `deploy reverted: ${reason(r.execResult)}`);
    return r.createdAddress.toString();
  }
  async function call(from, to, face, fn, args = [], value = 0n, ts) {
    const r = await raw(from, to, face.encodeFunctionData(fn, args), value, ts);
    return r.execResult;
  }
  async function view(to, face, fn, args = [], ts) {
    const res = await call(alice, to, face, fn, args, 0n, ts);
    assert.ok(!res.exceptionError, `${fn} reverted: ${reason(res)}`);
    return face.decodeFunctionResult(fn, bytesToHex(res.returnValue));
  }
  async function bal(who) {
    const acct = await vm.stateManager.getAccount(new Address(hexToBytes(who)));
    return acct ? acct.balance : 0n;
  }
  return { vm, deploy, call, view, bal, now: () => clock };
}

function mustOk(res, label) {
  assert.ok(!res.exceptionError, `${label} reverted: ${reason(res)}`);
}
function mustRevert(res, text) {
  assert.ok(res.exceptionError, `expected revert ${text}`);
  assert.equal(reason(res), text);
}

const Brake = art('CGC.sol', 'CGCBrake');
const brakeIface = ifaceOf(Brake);

async function deployBare(chainId, which, args) {
  const vm = await newVm(chainId);
  const r = await vm.evm.runCall({
    caller: new Address(hexToBytes(alice)),
    data: getBytes(deployData(which, args)),
    gasLimit: GAS,
    block: { header: header(T0) },
  });
  return r;
}

test('the brake bytecode fits a testnet deploy, and the source has no oracle-output path and no deploy script', () => {
  const size = art('CGC.sol', 'CGCBrake').evm.deployedBytecode.object.length / 2;
  assert.ok(size <= 24576, `brake runtime is ${size} bytes`);
  const blob = ['CGC.sol', 'CGCGwrsETH.sol', 'CGCMorphoRoute.sol'].map(read).join('\n');
  assert.equal(blob.includes('L2OutputOracle'), false);
  assert.equal(blob.includes('https://'), false);
  assert.match(blob, /OP_LIQUIDATE = 1 << 11/);
  assert.equal(/OP_LIQUIDATE = 1 << 12/.test(blob), false);
  assert.equal(readdirSync(ROOT).some((n) => /deploy/i.test(n)), false);
  const ids = [...read('CGC.sol').matchAll(/chainId == (\d+)/g)].map((m) => Number(m[1]));
  const evm = chains.chains.filter((c) => c.family === 'evm').map((c) => c.chain_id).sort((a, b) => a - b);
  assert.deepEqual([...new Set(ids)].sort((a, b) => a - b), evm);
});

test('per-chain windows match the config, and every mainnet id is refused', async () => {
  const bare = [alice, alice, alice, alice, alice, alice, bob, 1000n];
  for (const chain of chains.chains.filter((c) => c.family === 'evm')) {
    assert.ok(chain.w_final_max > chain.w_final, chain.key);
    const r = await deployBare(chain.chain_id, Brake, bare);
    assert.ok(!r.execResult.exceptionError, `${chain.key} deploy: ${reason(r.execResult)}`);
    const to = r.createdAddress.toString();
    const vm = r; // placeholder to keep the result's vm via a second call below
    void vm;
    const common = Common.custom({ chainId: chain.chain_id }, { hardfork: Hardfork.Shanghai });
    const live = await VM.create({ common });
    // The deploy above used a throwaway VM. Redeploy inside one we can read.
    await live.stateManager.putAccount(new Address(hexToBytes(alice)), new Account(0n, E18));
    const deployed = await live.evm.runCall({
      caller: new Address(hexToBytes(alice)),
      data: getBytes(deployData(Brake, bare)),
      gasLimit: GAS,
      block: { header: header(T0) },
    });
    assert.ok(!deployed.execResult.exceptionError, reason(deployed.execResult));
    const read = await live.evm.runCall({
      caller: new Address(hexToBytes(alice)),
      to: deployed.createdAddress,
      data: getBytes(brakeIface.encodeFunctionData('chainDefaults', [])),
      gasLimit: GAS,
      block: { header: header(T0) },
    });
    assert.ok(!read.execResult.exceptionError, reason(read.execResult));
    const d = brakeIface.decodeFunctionResult('chainDefaults', bytesToHex(read.execResult.returnValue));
    assert.equal(d[0], BigInt(chain.w_brake), chain.key);
    assert.equal(d[1], BigInt(chain.w_prov), chain.key);
    assert.equal(d[2], BigInt(chain.w_final), chain.key);
    assert.equal(d[3], BigInt(chain.w_final_max), chain.key);
    assert.equal(d[4], BigInt(chain.mode_id), chain.key);
    assert.equal(d[5], BigInt(chain.game_type), chain.key);
    const main = await deployBare(chain.mainnet_chain_id, Brake, bare);
    assert.equal(reason(main.execResult), 'not testnet', String(chain.mainnet_chain_id));
    void to;
  }
  const mantle = await deployBare(5003, Brake, bare);
  assert.equal(reason(mantle.execResult), 'not testnet');
  const solana = chains.chains.find((c) => c.key === 'solana-devnet');
  assert.equal(solana.brake, false);
  assert.equal(solana.chain_id, undefined);
  assert.equal(solana.w_final_max > solana.w_final, true);
});

test('gwrsETH and the Morpho route refuse every mainnet chain id', async () => {
  const wrap = art('CGCGwrsETH.sol', 'CGCGwrsETH');
  const route = art('CGCMorphoRoute.sol', 'CGCMorphoRoute');
  const argsW = [alice, alice, alice, ASSET, alice];
  const argsR = [alice, alice, alice, ASSET];
  for (const chain of chains.chains.filter((c) => c.family === 'evm')) {
    const okW = await deployBare(chain.chain_id, wrap, argsW);
    assert.ok(!okW.execResult.exceptionError, `${chain.key} wrapper: ${reason(okW.execResult)}`);
    const badW = await deployBare(chain.mainnet_chain_id, wrap, argsW);
    assert.equal(reason(badW.execResult), 'not testnet', `wrapper ${chain.mainnet_chain_id}`);
    const badR = await deployBare(chain.mainnet_chain_id, route, argsR);
    assert.equal(reason(badR.execResult), 'not testnet', `route ${chain.mainnet_chain_id}`);
  }
  const mainnets = [1, 10, 8453, 130, 57073, 42161, 56, 43114, 143, 999, 9745, 5000];
  for (const id of mainnets) {
    assert.equal(reason((await deployBare(id, wrap, argsW)).execResult), 'not testnet', `wrap ${id}`);
    assert.equal(reason((await deployBare(id, route, argsR)).execResult), 'not testnet', `route ${id}`);
  }
});

async function world(chainId, { gameType = 0, floor = 1000n } = {}) {
  const h = await boot(chainId);
  const A = {
    fact: art('fixtures.sol', 'FactStub'),
    system: art('fixtures.sol', 'SystemStub'),
    under: art('fixtures.sol', 'UnderlyingStub'),
    short: art('fixtures.sol', 'ShortUnderlying'),
    factory: art('fixtures.sol', 'FactoryStub'),
    game: art('fixtures.sol', 'GameStub'),
    greedy: art('fixtures.sol', 'Greedy'),
    board: art('CGC.sol', 'CGCRootBoard'),
    reg: art('CGC.sol', 'CGCRegistry'),
    watch: art('CGC.sol', 'CGCWatchers'),
    brake: Brake,
    inflow: art('CGC.sol', 'CGCInflow'),
    hook: art('CGC.sol', 'CGCEulerHook'),
    cap: art('CGC.sol', 'CGCCapAutomator'),
    oracle: art('CGC.sol', 'CGCOracleAdapter'),
    wrap: art('CGCGwrsETH.sol', 'CGCGwrsETH'),
    route: art('CGCMorphoRoute.sol', 'CGCMorphoRoute'),
  };
  const face = Object.fromEntries(Object.entries(A).map(([k, v]) => [k, ifaceOf(v)]));
  const addr = {};
  addr.fact = await h.deploy(A.fact, []);
  addr.system = await h.deploy(A.system, []);
  addr.under = await h.deploy(A.under, []);
  addr.short = await h.deploy(A.short, []);
  addr.factory = await h.deploy(A.factory, []);
  addr.game = await h.deploy(A.game, []);
  addr.game2 = await h.deploy(A.game, []);
  addr.board = await h.deploy(A.board, [bob]);
  addr.watch = await h.deploy(A.watch, [alice, 2n]);
  addr.brake = await h.deploy(A.brake, [addr.fact, addr.system, addr.board, addr.factory, addr.watch, alice, carol, floor]);
  addr.reg = await h.deploy(A.reg, [alice, alice]);
  addr.inflow = await h.deploy(A.inflow, [addr.reg, addr.brake, alice, 3600n]);
  addr.oracle = await h.deploy(A.oracle, [addr.brake, ASSET, alice]);
  addr.hook = await h.deploy(A.hook, [addr.inflow, addr.brake, alice, ASSET]);
  addr.cap = await h.deploy(A.cap, [addr.inflow, addr.brake, alice, 3600n]);
  addr.wrap = await h.deploy(A.wrap, [addr.under, addr.inflow, addr.brake, ASSET, alice]);
  addr.route = await h.deploy(A.route, [addr.wrap, addr.brake, addr.oracle, ASSET]);
  addr.greedy = await h.deploy(A.greedy, []);
  const send = (from, key, fn, args = [], value = 0n, ts) => h.call(from, addr[key], face[key], fn, args, value, ts);
  mustOk(await send(alice, 'system', 'set', [false, false, gameType]), 'system.set');
  mustOk(await send(alice, 'fact', 'set', [true, true]), 'fact.set');
  mustOk(await send(alice, 'brake', 'setBorrowed', [ASSET, 0n, true]), 'borrowed');
  mustOk(await send(alice, 'inflow', 'bindHook', [addr.hook]), 'bindHook');
  mustOk(await send(alice, 'inflow', 'bindWrapper', [addr.wrap]), 'bindWrapper');
  mustOk(await send(alice, 'hook', 'setMarket', [dave]), 'setMarket');
  mustOk(await send(alice, 'wrap', 'bindRoute', [addr.route]), 'bindRoute');
  mustOk(await send(alice, 'oracle', 'setUnderlying', [152577n]), 'price');
  const w1 = Wallet.createRandom();
  const w2 = Wallet.createRandom();
  mustOk(await send(alice, 'watch', 'add', [w1.address]), 'add w1');
  mustOk(await send(alice, 'watch', 'add', [w2.address]), 'add w2');
  async function sign(claimId, num, den, wallets = [w1, w2]) {
    const digest = keccak256(abiCoder.encode(
      ['uint256', 'uint256', 'bytes32', 'uint256', 'uint256'],
      [BigInt(chainId), BigInt(claimId), EVIDENCE, BigInt(num), BigInt(den)],
    ));
    const sigs = [];
    for (const w of wallets) sigs.push(Signature.from(await w.signMessage(getBytes(digest))));
    return [sigs.map((s) => s.r), sigs.map((s) => s.s), sigs.map((s) => s.v)];
  }
  async function brake(from, value, ts, asset = ASSET) {
    return send(from, 'brake', 'brake', [asset, 1, EVIDENCE], value, ts);
  }
  return { ...h, A, face, addr, send, sign, brake, w1, w2 };
}

async function publish(w, floor, floorOk = true) {
  mustOk(await w.send(alice, 'reg', 'publish', [ASSET, PATH, floor, floorOk, 0n, false, alice, T0, true, id('risk')]), 'publish');
  mustOk(await w.send(alice, 'inflow', 'setBaseGap', [ASSET, 1000n, true]), 'gap');
}

async function core(w, id) {
  return w.view(w.addr.brake, w.face.brake, 'claimCore', [id]);
}

test('a false or unreadable claim reverts before the bond is kept', async () => {
  const w = await world(ETH);
  const bond = 1000n;
  mustOk(await w.send(alice, 'fact', 'set', [true, false]), 'facts off');
  let before = await w.bal(alice);
  mustRevert(await w.brake(alice, bond, T0), 'no violation');
  assert.equal(await w.bal(alice), before);
  assert.equal((await w.view(w.addr.brake, w.face.brake, 'nextId'))[0], 1n);
  assert.equal((await w.view(w.addr.brake, w.face.brake, 'frozen', [ASSET]))[0], false);

  mustOk(await w.send(alice, 'fact', 'set', [false, true]), 'unreadable flag');
  before = await w.bal(alice);
  mustRevert(await w.brake(alice, bond, T0), 'facts unreadable');
  assert.equal(await w.bal(alice), before);

  mustOk(await w.send(alice, 'fact', 'setBoom', []), 'boom');
  before = await w.bal(alice);
  mustRevert(await w.brake(alice, bond, T0), 'facts unreadable');
  assert.equal(await w.bal(alice), before);

  mustOk(await w.send(alice, 'fact', 'set', [true, true]), 'facts on');
  mustOk(await w.send(alice, 'brake', 'setBorrowed', [ASSET, 0n, false]), 'unset borrowed');
  before = await w.bal(alice);
  mustRevert(await w.brake(alice, bond, T0), 'borrowed unreadable');
  assert.equal(await w.bal(alice), before);

  mustOk(await w.send(alice, 'brake', 'setBorrowed', [ASSET, 0n, true]), 'borrowed again');
  before = await w.bal(alice);
  mustRevert(await w.brake(alice, 1n, T0), 'bond');
  assert.equal(await w.bal(alice), before);
});

test('an honest root posted at the prov deadline confirms when it finalises, and the brake lifting pays nobody', async () => {
  const w = await world(ETH);
  const bond = 1000n;
  const before = await w.bal(alice);
  mustOk(await w.brake(alice, bond, T0), 'brake');
  assert.equal(await w.bal(alice), before - bond);
  assert.equal((await core(w, 1n))[2], OPEN);
  assert.equal((await w.view(w.addr.brake, w.face.brake, 'frozen', [ASSET]))[0], true);
  assert.equal((await w.view(w.addr.oracle, w.face.oracle, 'price'))[0], 152577n);
  mustRevert(await w.send(alice, 'oracle', 'quoteNewExposure'), 'frozen');

  const leaf = commitment(40373n, 152577n);
  const posted = T0 + 3600n;
  mustOk(await w.send(bob, 'board', 'post', [leaf], 0n, posted), 'post');
  mustOk(await w.send(alice, 'brake', 'proposeRoot', [1n, 40373n, 152577n], 0n, posted), 'propose');
  const roots = await w.view(w.addr.brake, w.face.brake, 'claimRoots', [1n]);
  assert.equal(roots[0], leaf);
  assert.equal(roots[1], posted + 7200n);
  const until = await w.view(w.addr.brake, w.face.brake, 'freezeUntil', [ASSET]);
  assert.equal(until[0], T0 + 21600n, 'a shorter finality must not pull the brake in');

  const pool = await w.bal(carol);
  const prover = await w.bal(bob);
  mustRevert(await w.send(bob, 'brake', 'confirm', [1n], 0n, posted + 7200n - 1n), 'not final');
  assert.equal(await w.bal(alice), before - bond);
  assert.equal((await w.view(w.addr.oracle, w.face.oracle, 'price'))[0], 152577n);

  mustOk(await w.send(bob, 'brake', 'confirm', [1n], 0n, posted + 7200n), 'confirm');
  assert.equal(await w.bal(alice), before);
  assert.equal(await w.bal(carol), pool);
  assert.equal(await w.bal(bob), prover);
  assert.equal((await core(w, 1n))[2], CONFIRMED);
  assert.equal((await w.view(w.addr.oracle, w.face.oracle, 'price'))[0], 40373n);
  assert.equal((await w.view(w.addr.brake, w.face.brake, 'frozen', [ASSET]))[0], true);
  assert.equal((await w.view(w.addr.brake, w.face.brake, 'frozen', [ASSET], T0 + 21600n))[0], false);
});

test('lifting the brake at W_brake does not slash, and a missing final root refunds at W_final_max', async () => {
  const w = await world(ETH);
  const bond = 1000n;
  const before = await w.bal(alice);
  const pool = await w.bal(carol);
  mustOk(await w.brake(alice, bond, T0), 'brake');
  assert.equal((await w.view(w.addr.brake, w.face.brake, 'frozen', [ASSET], T0 + 21599n))[0], true);
  assert.equal((await w.view(w.addr.brake, w.face.brake, 'frozen', [ASSET], T0 + 21600n))[0], false);
  assert.equal(await w.bal(alice), before - bond);
  assert.equal(await w.bal(carol), pool);
  assert.equal((await core(w, 1n))[2], OPEN);
  mustRevert(await w.send(bob, 'brake', 'refundNoFinalRoot', [1n], 0n, T0 + 86400n - 1n), 'final max');
  mustOk(await w.send(bob, 'brake', 'refundNoFinalRoot', [1n], 0n, T0 + 86400n), 'refund');
  assert.equal(await w.bal(alice), before);
  assert.equal(await w.bal(carol), pool);
  assert.equal((await core(w, 1n))[2], REFUNDED);
  assert.equal((await w.view(w.addr.oracle, w.face.oracle, 'price'))[0], 152577n);
});

test('a finalized clear root slashes 50/50, and a root that is not final by W_final_max refunds', async () => {
  const w = await world(ETH, { floor: 1001n });
  mustOk(await w.brake(alice, 1001n, T0), 'brake');
  const clear = commitment(1n, 1n);
  mustOk(await w.send(bob, 'board', 'post', [clear], 0n, T0), 'post clear');
  const until = (await w.view(w.addr.brake, w.face.brake, 'freezeUntil', [ASSET]))[0];
  mustOk(await w.send(bob, 'brake', 'proposeClearRoot', [1n], 0n, T0), 'attach clear');
  assert.equal((await w.view(w.addr.brake, w.face.brake, 'freezeUntil', [ASSET]))[0], until);
  mustRevert(await w.send(bob, 'brake', 'slash', [1n], 0n, T0 + 7199n), 'not final');
  const aliceBefore = await w.bal(alice);
  const bobBefore = await w.bal(bob);
  const poolBefore = await w.bal(carol);
  mustOk(await w.send(bob, 'brake', 'slash', [1n], 0n, T0 + 7200n), 'slash');
  assert.equal(await w.bal(alice), aliceBefore);
  assert.equal(await w.bal(bob), bobBefore + 500n);
  assert.equal(await w.bal(carol), poolBefore + 501n);
  assert.equal((await core(w, 1n))[2], SLASHED);
  assert.equal((await w.view(w.addr.oracle, w.face.oracle, 'price'))[0], 152577n);

  const w2 = await world(ETH);
  mustOk(await w2.brake(alice, 1000n, T0), 'brake 2');
  const late = T0 + 79201n;
  const leaf = commitment(1n, 1n);
  mustOk(await w2.send(bob, 'board', 'post', [leaf], 0n, late), 'late post');
  mustOk(await w2.send(bob, 'brake', 'proposeClearRoot', [1n], 0n, late), 'late attach');
  const before = await w2.bal(alice);
  const pool = await w2.bal(carol);
  mustOk(await w2.send(alice, 'brake', 'refundNoFinalRoot', [1n], 0n, T0 + 86400n), 'late refund');
  assert.equal(await w2.bal(alice), before + 1000n);
  assert.equal(await w2.bal(carol), pool);
});

test('pause, retirement, and an unreadable system refund without a slash', async () => {
  async function primed() {
    const w = await world(ETH);
    mustOk(await w.brake(alice, 1000n, T0), 'brake');
    const leaf = commitment(1n, 1n);
    mustOk(await w.send(bob, 'board', 'post', [leaf], 0n, T0), 'post');
    mustOk(await w.send(bob, 'brake', 'proposeClearRoot', [1n], 0n, T0), 'clear');
    return w;
  }
  const paused = await primed();
  mustOk(await paused.send(alice, 'system', 'set', [true, false, 0]), 'pause');
  const before = await paused.bal(alice);
  const pool = await paused.bal(carol);
  mustRevert(await paused.send(bob, 'brake', 'slash', [1n], 0n, T0 + 7200n), 'system unavailable');
  mustOk(await paused.send(bob, 'brake', 'refundPaused', [1n], 0n, T0 + 7200n), 'refund pause');
  assert.equal(await paused.bal(alice), before + 1000n);
  assert.equal(await paused.bal(carol), pool);

  const retired = await primed();
  mustOk(await retired.send(alice, 'system', 'set', [false, true, 0]), 'retire');
  const beforeR = await retired.bal(alice);
  mustOk(await retired.send(bob, 'brake', 'refundPaused', [1n]), 'refund retire');
  assert.equal(await retired.bal(alice), beforeR + 1000n);
  assert.equal((await core(retired, 1n))[2], REFUNDED);

  const dark = await world(ETH);
  mustOk(await dark.brake(alice, 1000n, T0), 'brake');
  mustOk(await dark.send(alice, 'system', 'setBoom'), 'boom');
  mustRevert(await dark.send(bob, 'brake', 'slash', [1n], 0n, T0 + 86400n), 'no root');
  const beforeD = await dark.bal(alice);
  mustOk(await dark.send(bob, 'brake', 'refundNoFinalRoot', [1n], 0n, T0 + 86400n), 'refund dark');
  assert.equal(await dark.bal(alice), beforeD + 1000n);
});

test('Base: posting at the prov deadline extends the brake, and the bond survives until finality', async () => {
  const w = await world(BASE, { gameType: 1 });
  const bond = 1000n;
  const before = await w.bal(alice);
  mustOk(await w.brake(alice, bond, T0), 'brake');
  assert.equal((await w.view(w.addr.brake, w.face.brake, 'frozen', [ASSET], T0 + 21600n))[0], false);
  assert.equal(await w.bal(alice), before - bond);
  const leaf = commitment(1n, 2n);
  const at = T0 + 86400n;
  mustOk(await w.send(alice, 'game', 'set', [0, leaf, 0n], 0n, at), 'game');
  mustOk(await w.send(alice, 'factory', 'set', [0n, 1, at, w.addr.game], 0n, at), 'factory');
  mustRevert(await w.send(alice, 'brake', 'proposeGame', [1n, 0n, 1n, 2n], 0n, at + 1n), 'prov window');
  mustOk(await w.send(alice, 'brake', 'proposeGame', [1n, 0n, 1n, 2n], 0n, at), 'propose');
  const finalAt = at + 453600n;
  assert.equal((await w.view(w.addr.brake, w.face.brake, 'frozen', [ASSET], T0 + 21601n))[0], true);
  mustRevert(await w.send(bob, 'brake', 'confirm', [1n], 0n, finalAt - 1n), 'not final');
  assert.equal(await w.bal(alice), before - bond);
  mustOk(await w.send(alice, 'game', 'set', [2, leaf, finalAt], 0n, finalAt), 'defender');
  mustRevert(await w.send(bob, 'brake', 'confirm', [1n], 0n, finalAt - 1n), 'not final');
  mustOk(await w.send(bob, 'brake', 'confirm', [1n], 0n, finalAt), 'confirm');
  assert.equal(await w.bal(alice), before);
  assert.equal((await w.view(w.addr.oracle, w.face.oracle, 'price'))[0], 76288n);
});

test('Unichain reads the dispute game factory and refunds when the game type changes', async () => {
  const w = await world(UNI, { gameType: 8 });
  const before = await w.bal(alice);
  mustRevert(await w.brake(alice, 1000n, T0), 'game type');
  assert.equal(await w.bal(alice), before);

  mustOk(await w.send(alice, 'system', 'set', [false, false, 9]), 'type 9');
  mustOk(await w.brake(alice, 1000n, T0), 'brake');
  const leaf = commitment(1n, 2n);
  mustOk(await w.send(alice, 'game', 'set', [0, leaf, 0]), 'game');
  mustOk(await w.send(alice, 'factory', 'set', [0n, 8, T0, w.addr.game]), 'wrong type');
  mustRevert(await w.send(alice, 'brake', 'proposeGame', [1n, 0n, 1n, 2n]), 'game type');
  mustOk(await w.send(alice, 'factory', 'set', [0n, 9, T0, w.addr.game]), 'type 9 game');
  mustOk(await w.send(alice, 'game', 'set', [0, commitment(9n, 10n), 0]), 'other root');
  mustRevert(await w.send(alice, 'brake', 'proposeGame', [1n, 0n, 1n, 2n]), 'root');
  mustOk(await w.send(alice, 'game', 'set', [0, leaf, 0]), 'right root');
  mustOk(await w.send(alice, 'brake', 'proposeGame', [1n, 0n, 1n, 2n]), 'propose');
  const clear = commitment(1n, 1n);
  mustOk(await w.call(alice, w.addr.game2, w.face.game, 'set', [0, clear, 0]), 'clear game');
  mustOk(await w.send(alice, 'factory', 'set', [1n, 9, T0, w.addr.game2]), 'clear index');
  mustOk(await w.send(bob, 'brake', 'proposeClearGame', [1n, 1n]), 'clear');
  const finalAt = T0 + 626400n;
  mustOk(await w.send(alice, 'game', 'set', [1, leaf, finalAt], 0n, finalAt), 'challenger');
  mustRevert(await w.send(bob, 'brake', 'confirm', [1n], 0n, finalAt), 'not defender');
  mustOk(await w.send(alice, 'system', 'set', [false, false, 1], 0n, finalAt), 'type moved');
  const pool = await w.bal(carol);
  const held = await w.bal(alice);
  mustRevert(await w.send(bob, 'brake', 'slash', [1n], 0n, finalAt), 'game type changed');
  mustOk(await w.send(bob, 'brake', 'refundGameTypeChanged', [1n], 0n, finalAt), 'refund type');
  assert.equal(await w.bal(alice), held + 1000n);
  assert.equal(await w.bal(carol), pool);
  assert.equal((await w.view(w.addr.oracle, w.face.oracle, 'price'))[0], 152577n);
});

test('Arbitrum slash waits out 6d8h+6h, and a same-instant clear root closes the refund', async () => {
  const w = await world(ARB);
  mustOk(await w.brake(alice, 1000n, T0), 'brake');
  const leaf = commitment(1n, 1n);
  mustOk(await w.send(bob, 'board', 'post', [leaf]), 'post');
  mustOk(await w.send(bob, 'brake', 'proposeClearRoot', [1n]), 'clear');
  const fin = T0 + 568800n;
  mustRevert(await w.send(bob, 'brake', 'slash', [1n], 0n, fin - 1n), 'not final');
  const bobBefore = await w.bal(bob);
  const pool = await w.bal(carol);
  mustOk(await w.send(bob, 'brake', 'slash', [1n], 0n, fin), 'slash');
  assert.equal(await w.bal(bob), bobBefore + 500n);
  assert.equal(await w.bal(carol), pool + 500n);
  mustRevert(await w.send(alice, 'brake', 'refundNoFinalRoot', [1n], 0n, T0 + 1296000n), 'not open');
});

test('watcher votes confirm or refund, and an unresolved vote refunds at W_final_max', async () => {
  const w = await world(HYPER);
  const before = await w.bal(alice);
  const pool = await w.bal(carol);
  mustOk(await w.brake(alice, 1000n, T0), 'brake');
  assert.equal((await w.view(w.addr.brake, w.face.brake, 'frozen', [ASSET], T0 + 21600n))[0], false);
  const sig = await w.sign(1n, 40373n, 152577n);
  mustRevert(await w.send(bob, 'brake', 'vote', [1n, 40373n, 152577n, ...sig], 0n, T0 + 21601n), 'prov window');
  mustOk(await w.send(bob, 'brake', 'vote', [1n, 40373n, 152577n, ...sig], 0n, T0 + 21600n), 'vote');
  assert.equal(await w.bal(alice), before);
  assert.equal(await w.bal(carol), pool);
  assert.equal((await core(w, 1n))[2], CONFIRMED);
  assert.equal((await w.view(w.addr.oracle, w.face.oracle, 'price'))[0], 40373n);
  mustRevert(await w.send(bob, 'brake', 'slash', [1n]), 'not open');

  const before2 = await w.bal(alice);
  mustOk(await w.brake(alice, 1000n, T0 + 21600n), 'second');
  const neg = await w.sign(2n, 1n, 1n);
  mustOk(await w.send(bob, 'brake', 'vote', [2n, 1n, 1n, ...neg], 0n, T0 + 21600n), 'negative');
  assert.equal(await w.bal(alice), before2);
  assert.equal((await core(w, 2n))[2], REFUNDED);
  assert.equal((await w.view(w.addr.oracle, w.face.oracle, 'price'))[0], 40373n);

  const before3 = await w.bal(alice);
  mustOk(await w.brake(alice, 1000n, T0 + 30000n), 'third');
  const one = await w.sign(3n, 1n, 2n, [w.w1]);
  mustRevert(await w.send(bob, 'brake', 'vote', [3n, 1n, 2n, ...one], 0n, T0 + 30000n), 'watchers');
  const dup = await w.sign(3n, 1n, 2n, [w.w1, w.w1]);
  mustRevert(await w.send(bob, 'brake', 'vote', [3n, 1n, 2n, ...dup], 0n, T0 + 30000n), 'watchers');
  mustRevert(await w.send(bob, 'brake', 'refundWatcherUnresolved', [3n], 0n, T0 + 30000n + 86400n - 1n), 'final max');
  mustOk(await w.send(bob, 'brake', 'refundWatcherUnresolved', [3n], 0n, T0 + 30000n + 86400n), 'unresolved');
  assert.equal(await w.bal(alice), before3);
  assert.equal(await w.bal(carol), pool);
});

test('pairing proofs are rejected, the window cannot shrink, and a refund cannot be taken twice', async () => {
  const w = await world(ETH);
  for (const kind of [2, 4, 5, 6]) {
    mustRevert(await w.send(alice, 'brake', 'submitProof', [1n, kind, '0x']), 'rejected kind');
  }
  mustRevert(await w.send(alice, 'brake', 'submitProof', [1n, 1, '0x']), 'kind');
  mustRevert(await w.send(alice, 'brake', 'setWindows', [21599n, 3600n, 7200n, 86400n]), 'window too short');
  mustRevert(await w.send(alice, 'brake', 'setWindows', [21600n, 3600n, 86400n, 86400n]), 'final max');
  mustOk(await w.brake(alice, 1000n, T0), 'brake');
  mustOk(await w.send(alice, 'brake', 'setWindows', [21600n, 3600n, 7200n, 90000n]), 'lengthen');
  const times = await w.view(w.addr.brake, w.face.brake, 'claimTimes', [1n]);
  assert.equal(times[3], T0 + 86400n);

  const g = await world(ETH);
  mustOk(await g.send(alice, 'greedy', 'open', [g.addr.brake, ASSET, 1, EVIDENCE], 1000n, T0), 'greedy open');
  const id = (await g.view(g.addr.greedy, g.face.greedy, 'id'))[0];
  mustOk(await g.send(bob, 'brake', 'refundNoFinalRoot', [id], 0n, T0 + 86400n), 'refund greedy');
  assert.equal(await g.bal(g.addr.greedy), 1000n);
  assert.equal((await g.view(g.addr.greedy, g.face.greedy, 'reentered'))[0], true);
});

test('Euler liquidations use bit 11, and the inflow cap is shared with the wrapper', async () => {
  const w = await world(ETH);
  const hook = w.face.hook;
  const liq = (await w.view(w.addr.hook, hook, 'OP_LIQUIDATE'))[0];
  const flash = (await w.view(w.addr.hook, hook, 'OP_FLASHLOAN'))[0];
  assert.equal(liq, 1n << 11n);
  assert.equal(flash, 1n << 12n);
  assert.notEqual(liq, flash);
  mustOk(await w.brake(alice, 1000n, T0), 'brake');
  mustOk(await w.send(dave, 'hook', 'hook', [dave, alice, 1n, liq]), 'liquidate');
  mustRevert(await w.send(dave, 'hook', 'hook', [dave, alice, 1n, flash]), 'frozen');
  mustRevert(await w.send(dave, 'hook', 'hook', [dave, alice, 1n, 1n << 6n]), 'frozen');
  mustOk(await w.send(dave, 'hook', 'hook', [dave, alice, 1n, 1n << 7n]), 'repay');
  mustOk(await w.send(dave, 'hook', 'checkWithdraw', [ASSET, alice, 1n]), 'withdraw');
  mustRevert(await w.send(dave, 'hook', 'checkBorrow', [ASSET, 1n]), 'frozen');
  mustRevert(await w.send(dave, 'hook', 'checkDeposit', [ASSET, alice, 1n]), 'frozen');
  mustRevert(await w.send(alice, 'hook', 'checkRepay', [ASSET, 1n]), 'market');

  const open = await world(ETH);
  await publish(open, 1);
  mustOk(await open.send(dave, 'hook', 'checkDeposit', [ASSET, alice, 100n]), 'cap');
  mustRevert(await open.send(dave, 'hook', 'checkDeposit', [ASSET, alice, 1n]), 'inflow cap');
  mustRevert(await open.send(dave, 'hook', 'checkDeposit', [ASSET, alice, 60n], 0n, T0), 'inflow cap');
  const quiet = await world(ETH);
  await publish(quiet, 1, false);
  mustRevert(await quiet.send(dave, 'hook', 'checkDeposit', [ASSET, alice, 1n]), 'inflow unreadable');
  const zero = await world(ETH);
  await publish(zero, 0, true);
  mustRevert(await zero.send(dave, 'hook', 'checkDeposit', [ASSET, alice, 1n]), 'inflow cap');
  const mid = await world(ETH);
  mustOk(await mid.send(alice, 'reg', 'publish', [ASSET, PATH, 2, true, 0n, false, alice, T0, true, id('risk')]), 'f2');
  mustOk(await mid.send(alice, 'inflow', 'setBaseGap', [ASSET, 1000n, true]), 'gap');
  assert.equal((await mid.view(mid.addr.inflow, mid.face.inflow, 'maxInflow', [ASSET]))[0], 500n);
  mustOk(await mid.send(alice, 'reg', 'publish', [id('other'), PATH, 3, true, 0n, false, alice, T0, true, id('risk')]), 'f3');
  mustOk(await mid.send(alice, 'inflow', 'setBaseGap', [id('other'), 1000n, true]), 'gap3');
  assert.equal((await mid.view(mid.addr.inflow, mid.face.inflow, 'maxInflow', [id('other')]))[0], 1000n);

  const raised = await world(ETH);
  mustOk(await raised.send(alice, 'inflow', 'setBaseGap', [ASSET, 0n, false]), 'gap unread');
  mustRevert(await raised.send(alice, 'cap', 'commitRaise', [ASSET, 10n]), 'cap unreadable');
  assert.equal((await raised.view(raised.addr.cap, raised.face.cap, 'capSet', [ASSET]))[0], false);
  await publish(raised, 1);
  mustOk(await raised.send(alice, 'cap', 'commitRaise', [ASSET, 10n]), 'raise');
  assert.equal((await raised.view(raised.addr.cap, raised.face.cap, 'storedCap', [ASSET]))[0], 110n);
  mustOk(await raised.brake(alice, 1000n, T0), 'brake');
  mustRevert(await raised.send(alice, 'cap', 'commitRaise', [ASSET, 10n], 0n, T0 + 3600n), 'frozen');

  assert.equal((await raised.view(raised.addr.reg, raised.face.reg, 'configChange', [ASSET, PATH], T0 + WEEK - 1n))[0], 1n);
  assert.equal((await raised.view(raised.addr.reg, raised.face.reg, 'configChange', [ASSET, PATH], T0 + WEEK))[0], 2n);
  assert.equal((await raised.view(raised.addr.reg, raised.face.reg, 'configChange', [ASSET, id('missing')]))[0], 0n);
});

test('gwrsETH mints only for the curator or the Morpho route, and the cap is one reading', async () => {
  const w = await world(ETH);
  assert.equal((await w.view(w.addr.wrap, w.face.wrap, 'symbol'))[0], 'gwrsETH');
  await publish(w, 1);
  mustOk(await w.send(alice, 'under', 'mint', [alice, 1000n]), 'fund');
  mustOk(await w.send(alice, 'under', 'approve', [w.addr.wrap, 1000n]), 'approve');
  const under = async (who) => (await w.view(w.addr.under, w.face.under, 'balanceOf', [who]))[0];
  const before = await under(alice);
  mustRevert(await w.send(bob, 'wrap', 'mint', [alice, alice, 1n]), 'minter');
  assert.equal(await under(alice), before);

  mustOk(await w.send(alice, 'route', 'supplyCollateral', [100n]), 'supply');
  assert.equal(await under(alice), before - 100n);
  assert.equal((await w.view(w.addr.wrap, w.face.wrap, 'totalSupply'))[0], 100n);
  assert.equal((await w.view(w.addr.wrap, w.face.wrap, 'balanceOf', [w.addr.route]))[0], 100n);
  assert.equal(await under(w.addr.wrap), 100n);
  mustRevert(await w.send(alice, 'route', 'supplyCollateral', [1n]), 'inflow cap');
  mustOk(await w.send(alice, 'route', 'borrow', [40n]), 'borrow');
  mustRevert(await w.send(alice, 'route', 'withdrawCollateral', [100n]), 'short');
  mustOk(await w.brake(alice, 1000n, T0), 'brake');
  mustRevert(await w.send(alice, 'route', 'borrow', [1n]), 'frozen');
  mustRevert(await w.send(alice, 'route', 'supplyCollateral', [1n]), 'frozen');
  mustOk(await w.send(alice, 'route', 'repay', [40n]), 'repay');
  mustOk(await w.send(alice, 'route', 'withdrawCollateral', [100n]), 'exit');
  assert.equal(await under(alice), before);
  assert.equal((await w.view(w.addr.wrap, w.face.wrap, 'totalSupply'))[0], 0n);
  assert.equal((await w.view(w.addr.oracle, w.face.oracle, 'price'))[0], 152577n);
  assert.equal((await w.view(w.addr.route, w.face.route, 'price'))[0], 152577n);

  mustOk(await w.send(alice, 'short', 'mint', [alice, 50n]), 'short fund');
  const shortWrap = await w.deploy(w.A.wrap, [w.addr.short, w.addr.inflow, w.addr.brake, ASSET, alice]);
  mustRevert(await w.call(alice, shortWrap, w.face.wrap, 'mint', [alice, alice, 50n], 0n, T0 + 21600n), 'underlying short');
  assert.equal((await w.view(w.addr.short, w.face.short, 'balanceOf', [alice]))[0], 50n);
});

test('HyperEVM scope is sUSDe and cmETH, dated 23 Oct 2026', async () => {
  assert.equal(hyper.dated, '2026-10-23');
  assert.equal(hyper.chain_id, 998);
  assert.deepEqual(hyper.hyperlend.oft_collateral, ['sUSDe']);
  assert.equal(hyper.hyperlend.usde.collateral_factor, '0');
  assert.equal(hyper.hyperlend.usde.oft, false);
  assert.equal(hyper.hyperlend.usr.status, 'frozen');
  assert.equal(Object.hasOwn(hyper.hyperlend.usr, 'collateral_factor'), false);
  assert.equal(hyper.hyperlend.usdt0.symbol, 'USD₮0');
  assert.equal(hyper.hyperlend.usdt0.accepted, true);
  assert.equal(hyper.hyperlend.usdt0.oft, false);
  assert.deepEqual(hyper.morpho_felix.material, ['cmETH']);
  assert.deepEqual(hyper.cgc_assets, ['sUSDe', 'cmETH']);
  for (const name of ['USDe', 'USR', 'USD₮0', 'gwrsETH']) {
    assert.equal(hyper.cgc_assets.includes(name), false, name);
  }
  const w = await world(HYPER);
  for (const name of hyper.cgc_assets) {
    const asset = id(name);
    mustOk(await w.send(alice, 'reg', 'publish', [asset, PATH, 2, true, 0n, true, alice, T0, true, id('risk')]), name);
    assert.equal((await w.view(w.addr.reg, w.face.reg, 'pathwayCount', [asset]))[0], 1n);
  }
  for (const name of ['USDe', 'USR', 'USD₮0']) {
    assert.equal((await w.view(w.addr.reg, w.face.reg, 'pathwayCount', [id(name)]))[0], 0n, name);
  }
  assert.equal(JSON.stringify(chains).includes('bond_ratio'), false);
  assert.equal(JSON.stringify(hyper).includes('0x'), false);
});
