/**
 * Quantum custody haircut. Compiled with solc and run on an in-process
 * EVM. No network, no deploy script, no mainnet key.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import solc from 'solc';
import { VM } from '@ethereumjs/vm';
import { Common, Hardfork } from '@ethereumjs/common';
import { Account, Address, hexToBytes, bytesToHex } from '@ethereumjs/util';
import { AbiCoder, Interface, Wallet, getBytes, keccak256 as k256 } from 'ethers';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) => readFileSync(join(ROOT, name), 'utf8');
const chains = JSON.parse(read('chains.json'));
const GAS = 30_000_000n;
const E18 = 10n ** 18n;
const T0 = 1_700_000_000n;
const WEEK = 7n * 86400n;
const DAY = 86400n;

const input = {
  language: 'Solidity',
  sources: {
    'QCHOracle.sol': { content: read('src/QCHOracle.sol') },
    'QCHSentinel.sol': { content: read('src/QCHSentinel.sol') },
  },
  settings: {
    optimizer: { enabled: true, runs: 200 },
    evmVersion: 'paris',
    outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object'] } },
  },
};
const compiled = JSON.parse(solc.compile(JSON.stringify(input)));
const errors = (compiled.errors || []).filter((e) => e.severity === 'error');
if (errors.length) throw new Error(errors.map((e) => e.formattedMessage).join('\n'));

const art = (file, name) => compiled.contracts[file][name];
const face = (a) => new Interface(a.abi);
const ORACLE = art('QCHOracle.sol', 'QCHOracle');
const SENTINEL = art('QCHSentinel.sol', 'QCHSentinel');
const oracleIface = face(ORACLE);
const sentinelIface = face(SENTINEL);

const alice = '0x' + '11'.repeat(20);
const bob = '0x' + '22'.repeat(20);
const carol = '0x' + '33'.repeat(20);
const dave = '0x' + '44'.repeat(20);
const KEYS = ['11', '22', '33'].map((b) => new Wallet('0x' + b.repeat(32)));
const NEXT = new Wallet('0x' + '55'.repeat(32));
const ASSET = '0x' + 'ab'.repeat(32);
const MARKET = '0x' + 'cd'.repeat(32);

const TYPES = {
  QCHReading: [
    { name: 'asset', type: 'bytes32' },
    { name: 'qesBps', type: 'uint16' },
    { name: 'twBps', type: 'uint16' },
    { name: 'flags', type: 'uint16' },
    { name: 'ts', type: 'uint64' },
    { name: 'scheme', type: 'uint8' },
  ],
};

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

function deployData(a, args) {
  const tail = face(a).encodeDeploy(args).slice(2);
  return '0x' + a.evm.bytecode.object + tail;
}

async function boot(chainId) {
  const common = Common.custom({ chainId }, { hardfork: Hardfork.Shanghai });
  const vm = await VM.create({ common });
  for (const who of [alice, bob, carol, dave, ...KEYS.map((k) => k.address), NEXT.address]) {
    await vm.stateManager.putAccount(new Address(hexToBytes(who)), new Account(0n, 1_000n * E18));
  }
  let clock = T0;
  async function raw(from, to, data, ts) {
    if (ts !== undefined) clock = BigInt(ts);
    const opts = {
      caller: new Address(hexToBytes(from)), data: getBytes(data), gasLimit: GAS,
      value: 0n, block: { header: header(clock) },
    };
    if (to) opts.to = new Address(hexToBytes(to));
    return vm.evm.runCall(opts);
  }
  async function deploy(a, args, from = alice, ts) {
    const r = await raw(from, null, deployData(a, args), ts);
    assert.ok(!r.execResult.exceptionError, `deploy reverted: ${reason(r.execResult)}`);
    return r.createdAddress.toString();
  }
  async function call(from, to, iface, fn, args = [], ts) {
    const r = await raw(from, to, iface.encodeFunctionData(fn, args), ts);
    return r.execResult;
  }
  async function view(to, iface, fn, args = [], ts) {
    const res = await call(alice, to, iface, fn, args, ts);
    assert.ok(!res.exceptionError, `${fn} reverted: ${reason(res)}`);
    return iface.decodeFunctionResult(fn, bytesToHex(res.returnValue));
  }
  return { deploy, call, view, now: () => clock };
}

function mustOk(res, label) {
  assert.ok(!res.exceptionError, `${label} reverted: ${reason(res)}`);
}
function mustRevert(res, text) {
  assert.ok(res.exceptionError, `expected revert ${text}`);
  assert.equal(reason(res), text);
}

async function signs(chainId, verifying, value, wallets) {
  const domain = { name: 'QCH Oracle', version: '1', chainId, verifyingContract: verifying };
  const out = [];
  for (const w of wallets) out.push(await w.signTypedData(domain, TYPES, value));
  return out;
}

function reading(over = {}) {
  return {
    asset: ASSET,
    qesBps: 9816,
    twBps: 9816,
    flags: 0,
    ts: T0,
    scheme: 0,
    ...over,
  };
}

test('the source has no deploy script and names the testnet allowlist', () => {
  const names = readdirSync(ROOT);
  assert.equal(names.some((n) => /deploy/i.test(n)), false);
  const blob = read('src/QCHOracle.sol') + read('src/QCHSentinel.sol');
  assert.equal(blob.includes('https://'), false);
  assert.equal(/function liquidate/.test(blob), false);
  const ids = [...blob.matchAll(/id == (\d+)/g)].map((m) => Number(m[1]));
  const unique = [...new Set(ids)].sort((a, b) => a - b);
  const want = chains.testnets.map((c) => c.chain_id).sort((a, b) => a - b);
  assert.deepEqual(unique, want);
  for (const id of chains.mainnets_refused) assert.equal(unique.includes(id), false);
});

test('mainnet chain ids are refused and the four testnets deploy', async () => {
  const args = [KEYS[0].address, KEYS[1].address, KEYS[2].address, dave];
  for (const id of chains.mainnets_refused) {
    const common = Common.custom({ chainId: id }, { hardfork: Hardfork.Shanghai });
    const vm = await VM.create({ common });
    await vm.stateManager.putAccount(new Address(hexToBytes(alice)), new Account(0n, E18));
    const r = await vm.evm.runCall({
      caller: new Address(hexToBytes(alice)),
      data: getBytes(deployData(ORACLE, args)),
      gasLimit: GAS,
      block: { header: header(T0) },
    });
    assert.equal(reason(r.execResult), 'testnet only', `chain ${id}`);
  }
  for (const chain of chains.testnets) {
    const box = await boot(chain.chain_id);
    const addr = await box.deploy(ORACLE, args);
    assert.ok(addr);
  }
});

test('two of three stores a score, one does not, and a missing score is not zero', async () => {
  const box = await boot(84532);
  const oracle = await box.deploy(ORACLE, [KEYS[0].address, KEYS[1].address, KEYS[2].address, dave]);
  const value = reading();
  const one = await signs(84532, oracle, value, [KEYS[0]]);
  mustRevert(await box.call(alice, oracle, oracleIface, 'submit', [
    value.asset, value.qesBps, value.twBps, value.flags, value.ts, value.scheme, one,
  ]), 'threshold');
  const missing = await box.call(alice, oracle, oracleIface, 'qesOf', [ASSET]);
  mustRevert(missing, 'unreadable');
  const two = await signs(84532, oracle, value, [KEYS[0], KEYS[1]]);
  mustOk(await box.call(alice, oracle, oracleIface, 'submit', [
    value.asset, value.qesBps, value.twBps, value.flags, value.ts, value.scheme, two,
  ]), 'submit');
  const got = await box.view(oracle, oracleIface, 'qesOf', [ASSET]);
  assert.equal(got[0], 9816n);
  const zero = reading({ qesBps: 0, twBps: 0 });
  const zsigs = await signs(84532, oracle, zero, [KEYS[0], KEYS[2]]);
  mustOk(await box.call(alice, oracle, oracleIface, 'submit', [
    zero.asset, zero.qesBps, zero.twBps, zero.flags, zero.ts, zero.scheme, zsigs,
  ]), 'zero');
  const stored = await box.view(oracle, oracleIface, 'qesOf', [ASSET]);
  assert.equal(stored[0], 0n);
});

test('schemes 1, 2 and 3 do not store a reading, and a WOTS commit can be scheduled', async () => {
  const box = await boot(11155111);
  const oracle = await box.deploy(ORACLE, [KEYS[0].address, KEYS[1].address, KEYS[2].address, dave]);
  for (const scheme of [1, 2, 3]) {
    const value = reading({ scheme });
    const sigs = await signs(11155111, oracle, value, [KEYS[0], KEYS[1]]);
    mustRevert(await box.call(alice, oracle, oracleIface, 'submit', [
      value.asset, value.qesBps, value.twBps, value.flags, value.ts, scheme, sigs,
    ]), 'scheme unavailable');
  }
  mustRevert(await box.call(dave, oracle, oracleIface, 'rotateSigner', [1, 0, '0x' + '11'.repeat(32)]), 'scheme unavailable');
  const commit = k256(hexToBytes(NEXT.address));
  mustOk(await box.call(dave, oracle, oracleIface, 'rotateSigner', [0, 0, commit]), 'rotate ecdsa');
  mustRevert(await box.call(dave, oracle, oracleIface, 'applyRotation', [NEXT.address], T0 + WEEK - 1n), 'timelock');
  mustOk(await box.call(dave, oracle, oracleIface, 'applyRotation', [NEXT.address], T0 + WEEK), 'apply');
  const wots = '0x' + '22'.repeat(32);
  mustOk(await box.call(dave, oracle, oracleIface, 'rotateSigner', [2, 0, wots], T0 + WEEK), 'rotate wots');
  mustRevert(await box.call(dave, oracle, oracleIface, 'applyRotation', [alice], T0 + WEEK + WEEK), 'signer');
});

test('a scheme-2 commit is stored and still cannot sign a reading', async () => {
  const box = await boot(421614);
  const oracle = await box.deploy(ORACLE, [KEYS[0].address, KEYS[1].address, KEYS[2].address, dave]);
  const wots = '0x' + '22'.repeat(32);
  mustOk(await box.call(dave, oracle, oracleIface, 'rotateSigner', [2, 0, wots]), 'rotate');
  mustRevert(await box.call(dave, oracle, oracleIface, 'applyRotation', [alice], T0 + WEEK), 'signer');
  mustOk(await box.call(dave, oracle, oracleIface, 'applyRotation', [
    '0x0000000000000000000000000000000000000000',
  ], T0 + WEEK), 'apply');
  const stored = await box.view(oracle, oracleIface, 'wotsCommit', [], T0 + WEEK);
  assert.equal(stored[0], wots);
  const value = reading({ scheme: 2, ts: T0 + WEEK });
  const sigs = await signs(421614, oracle, value, [KEYS[0], KEYS[1]]);
  mustRevert(await box.call(alice, oracle, oracleIface, 'submit', [
    value.asset, value.qesBps, value.twBps, value.flags, value.ts, 2, sigs,
  ], T0 + WEEK), 'scheme unavailable');
});

test('a canary cuts the cap and schedules LTV, and nothing liquidates', async () => {
  const box = await boot(998);
  const oracle = await box.deploy(ORACLE, [KEYS[0].address, KEYS[1].address, KEYS[2].address, dave]);
  const sentinel = await box.deploy(SENTINEL, [oracle, alice, 50, 1000]);
  const names = SENTINEL.abi.map((item) => item.name).filter(Boolean);
  assert.equal(names.includes('liquidate'), false);
  assert.equal(names.some((name) => /increase|raise/i.test(name)), false);
  mustOk(await box.call(alice, sentinel, sentinelIface, 'seed', [MARKET, 100000n, 8600n]), 'seed');
  const quiet = reading({ flags: 0 });
  let sigs = await signs(998, oracle, quiet, [KEYS[0], KEYS[1]]);
  mustOk(await box.call(alice, oracle, oracleIface, 'submit', [
    quiet.asset, quiet.qesBps, quiet.twBps, quiet.flags, quiet.ts, 0, sigs,
  ]), 'quiet');
  mustRevert(await box.call(alice, sentinel, sentinelIface, 'cut', [ASSET, MARKET]), 'no trigger');
  const loud = reading({ flags: 32 });
  sigs = await signs(998, oracle, loud, [KEYS[0], KEYS[1]]);
  mustOk(await box.call(alice, oracle, oracleIface, 'submit', [
    loud.asset, loud.qesBps, loud.twBps, loud.flags, loud.ts, 0, sigs,
  ]), 'canary');
  mustRevert(await box.call(bob, sentinel, sentinelIface, 'cut', [ASSET, MARKET]), 'curator');
  mustOk(await box.call(alice, sentinel, sentinelIface, 'cut', [ASSET, MARKET]), 'cut');
  const market = await box.view(sentinel, sentinelIface, 'markets', [MARKET]);
  assert.equal(market[0], 90000n);
  assert.equal(market[1], 8600n);
  assert.equal(market[2], 7740n);
  mustRevert(await box.call(alice, sentinel, sentinelIface, 'applyLtv', [MARKET], T0 + WEEK - 1n), 'timelock');
  mustOk(await box.call(alice, sentinel, sentinelIface, 'applyLtv', [MARKET], T0 + WEEK), 'ltv');
  const after = await box.view(sentinel, sentinelIface, 'markets', [MARKET], T0 + WEEK);
  assert.equal(after[1], 7740n);
  assert.ok(after[1] < 8600n);
});

test('a jump needs a prior observation, a deadline needs no attestation, and flags 16 and 32 carry the score', async () => {
  const box = await boot(84532);
  const oracle = await box.deploy(ORACLE, [KEYS[0].address, KEYS[1].address, KEYS[2].address, dave]);
  const sentinel = await box.deploy(SENTINEL, [oracle, alice, 50, 1000]);
  mustOk(await box.call(alice, sentinel, sentinelIface, 'seed', [MARKET, 100000n, 5000n]), 'seed');
  const first = reading({ qesBps: 4000, twBps: 4000, flags: 0 });
  let sigs = await signs(84532, oracle, first, [KEYS[0], KEYS[1]]);
  mustOk(await box.call(alice, oracle, oracleIface, 'submit', [
    first.asset, first.qesBps, first.twBps, first.flags, first.ts, 0, sigs,
  ]), 'first');
  mustRevert(await box.call(alice, sentinel, sentinelIface, 'cut', [ASSET, MARKET]), 'no trigger');
  mustOk(await box.call(alice, sentinel, sentinelIface, 'observe', [ASSET, MARKET]), 'observe');
  const jumped = reading({ qesBps: 4051, twBps: 4051, flags: 0 });
  sigs = await signs(84532, oracle, jumped, [KEYS[1], KEYS[2]]);
  mustOk(await box.call(alice, oracle, oracleIface, 'submit', [
    jumped.asset, jumped.qesBps, jumped.twBps, jumped.flags, jumped.ts, 0, sigs,
  ]), 'jump');
  mustOk(await box.call(alice, sentinel, sentinelIface, 'cut', [ASSET, MARKET]), 'jump cut');
  const hair0 = await box.view(sentinel, sentinelIface, 'collateralHaircut', [0, 4051]);
  const hair16 = await box.view(sentinel, sentinelIface, 'collateralHaircut', [16, 4051]);
  const hair32 = await box.view(sentinel, sentinelIface, 'collateralHaircut', [32, 4051]);
  assert.equal(hair0[0], 0n);
  assert.equal(hair16[0], 4051n);
  assert.equal(hair32[0], 4051n);
  mustRevert(await box.call(alice, sentinel, sentinelIface, 'collateralHaircut', [32, 10001]), 'qes');
  const later = T0 + DAY;
  mustOk(await box.call(alice, sentinel, sentinelIface, 'setPolicy', [MARKET, later, false], later), 'policy');
  const same = reading({ qesBps: 4051, twBps: 4051, flags: 0, ts: later });
  sigs = await signs(84532, oracle, same, [KEYS[0], KEYS[2]]);
  mustOk(await box.call(alice, oracle, oracleIface, 'submit', [
    same.asset, same.qesBps, same.twBps, same.flags, same.ts, 0, sigs,
  ], later), 'repost');
  const before = await box.view(sentinel, sentinelIface, 'markets', [MARKET], later);
  mustOk(await box.call(alice, sentinel, sentinelIface, 'cut', [ASSET, MARKET], later), 'deadline');
  const after = await box.view(sentinel, sentinelIface, 'markets', [MARKET], later);
  assert.ok(after[0] < before[0]);
  mustOk(await box.call(alice, sentinel, sentinelIface, 'setPolicy', [MARKET, later, true], later), 'attest');
  mustRevert(await box.call(alice, sentinel, sentinelIface, 'cut', [ASSET, MARKET], later), 'no trigger');
});

test('a reading older than a day is stale and is not returned as zero', async () => {
  const box = await boot(84532);
  const oracle = await box.deploy(ORACLE, [KEYS[0].address, KEYS[1].address, KEYS[2].address, dave]);
  const value = reading();
  const sigs = await signs(84532, oracle, value, [KEYS[0], KEYS[1]]);
  mustOk(await box.call(alice, oracle, oracleIface, 'submit', [
    value.asset, value.qesBps, value.twBps, value.flags, value.ts, 0, sigs,
  ]), 'submit');
  mustRevert(await box.call(alice, oracle, oracleIface, 'qesOf', [ASSET], T0 + DAY + 1n), 'stale');
});
