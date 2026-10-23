/**
 * Kink Cap — compile the market, run it on an in-process EVM. No network.
 * The precompile is etched at 0x812 for the test only; that runtime is not
 * in the market's bytecode.
 *
 * Chain 998 only. Two signers of this market's own three. Collateral of one
 * series cannot pay another. A missing rate pays 0 and is not stored as 0%.
 * Kind 0 is a cap, kind 1 is a cap spread (ceiling 40% when unset), kind 2
 * is a digital. A formula break, a model break, or a gap over two hours
 * freezes the series.
 */
import test from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { VM } from '@ethereumjs/vm';
import { Common, Hardfork } from '@ethereumjs/common';
import { Account, Address, hexToBytes, bytesToHex } from '@ethereumjs/util';
import {
  AbiCoder, Interface, Wallet, ZeroHash, concat, getBytes, keccak256,
  recoverAddress, toUtf8Bytes, zeroPadValue,
} from 'ethers';
import solc from 'solc';

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = join(HERE, '..');
const GAS = 30_000_000n;
const WAD = 10n ** 18n;
const HOUR = 3600n;
const CHAIN = 998;
const PRECOMPILE = '0x0000000000000000000000000000000000000812';
const L1 = '0x0000000000000000000000000000000000000809';

const alice = '0x' + '11'.repeat(20);
const bob = '0x' + '22'.repeat(20);
const carol = '0x' + '33'.repeat(20);
const mallory = '0x' + '44'.repeat(20);
const ZERO = '0x' + '00'.repeat(20);
const signers = [new Wallet('0x' + 'a1'.repeat(32)), new Wallet('0x' + 'b2'.repeat(32)), new Wallet('0x' + 'c3'.repeat(32))];

const SETTINGS = {
  optimizer: { enabled: true, runs: 200 },
  viaIR: true,
  evmVersion: 'paris',
  outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object'] } },
};

function compile() {
  const sources = {
    'KinkCapMarket.sol': { content: readFileSync(join(ROOT, 'KinkCapMarket.sol'), 'utf8') },
    'test/MockUSDC.sol': { content: readFileSync(join(HERE, 'MockUSDC.sol'), 'utf8') },
    'test/FixedPrecompile.sol': { content: readFileSync(join(HERE, 'FixedPrecompile.sol'), 'utf8') },
    'test/FixedL1.sol': { content: readFileSync(join(HERE, 'FixedL1.sol'), 'utf8') },
  };
  const out = JSON.parse(solc.compile(JSON.stringify({ language: 'Solidity', sources, settings: SETTINGS })));
  const fatal = (out.errors || []).filter((e) => e.severity === 'error');
  if (fatal.length) throw new Error(JSON.stringify(fatal, null, 2));
  const pick = (file, name) => {
    const c = out.contracts[file][name];
    return { abi: c.abi, bytecode: '0x' + c.evm.bytecode.object };
  };
  return {
    market: pick('KinkCapMarket.sol', 'KinkCapMarket'),
    token: pick('test/MockUSDC.sol', 'MockUSDC'),
    pre: pick('test/FixedPrecompile.sol', 'FixedPrecompile'),
    l1: pick('test/FixedL1.sol', 'FixedL1'),
  };
}

const BUILT = compile();
const marketIface = new Interface(BUILT.market.abi);
const tokenIface = new Interface(BUILT.token.abi);
const preIface = new Interface(BUILT.pre.abi);
const l1Iface = new Interface(BUILT.l1.abi);
const coder = AbiCoder.defaultAbiCoder();

function header(timestamp) {
  return {
    timestamp, number: timestamp, coinbase: Address.zero(), difficulty: 0n,
    prevRandao: new Uint8Array(32), gasLimit: GAS, baseFeePerGas: 0n,
    getBlobGasPrice: () => 0n,
  };
}

function revertName(execResult) {
  const ret = bytesToHex(execResult.returnValue || new Uint8Array());
  if (ret.startsWith('0x08c379a0')) {
    return new Interface(['function Error(string)']).decodeFunctionData('Error', ret)[0];
  }
  for (const name of ['WrongChain', 'TransfersDisabled']) {
    if (ret.startsWith(marketIface.getError(name).selector)) return name;
  }
  return execResult.exceptionError ? String(execResult.exceptionError.error) : ret;
}

async function newVm(chainId = CHAIN) {
  const common = Common.custom({ chainId }, { hardfork: Hardfork.Shanghai });
  const vm = await VM.create({ common });
  const who = [alice, bob, carol, mallory, ...signers.map((w) => w.address)];
  for (const addr of who) {
    await vm.stateManager.putAccount(new Address(hexToBytes(addr)), new Account(0n, 1_000_000n * WAD));
  }
  return vm;
}

function word(n) {
  return hexToBytes('0x' + BigInt(n).toString(16).padStart(64, '0'));
}

async function mirror(vm, live) {
  const src = new Address(hexToBytes(live));
  const code = await vm.stateManager.getContractCode(src);
  assert.ok(code.length > 0, 'precompile stand-in has no code');
  const dest = new Address(hexToBytes(PRECOMPILE));
  await vm.stateManager.putContractCode(dest, code);
  for (let i = 0; i < 8; i++) {
    const key = word(i);
    const value = await vm.stateManager.getContractStorage(src, key);
    await vm.stateManager.putContractStorage(dest, key, value);
  }
}

async function setup(chainId = CHAIN, transfers = false) {
  const vm = await newVm(chainId);
  async function send(from, to, data, timestamp = 0n) {
    const res = await vm.evm.runCall({
      caller: new Address(hexToBytes(from)),
      to: to ? new Address(hexToBytes(to)) : undefined,
      data: getBytes(data),
      gasLimit: GAS,
      value: 0n,
      block: { header: header(timestamp) },
    });
    return res;
  }
  async function deploy(bytecode, from = alice) {
    const r = await send(from, undefined, bytecode);
    assert.ok(!r.execResult.exceptionError, 'deploy reverted: ' + revertName(r.execResult));
    return r.createdAddress.toString();
  }
  function logsOf(res, iface, name) {
    const topic = iface.getEvent(name).topicHash;
    return (res.execResult.logs || [])
      .filter((l) => bytesToHex(l[1][0]) === topic)
      .map((l) => iface.parseLog({
        topics: l[1].map((t) => bytesToHex(t)),
        data: bytesToHex(l[2]),
      }));
  }
  async function call(from, to, iface, fn, args, timestamp = 0n) {
    const r = await send(from, to, iface.encodeFunctionData(fn, args), timestamp);
    return r;
  }
  async function read(to, iface, fn, args, timestamp = 0n) {
    const r = await call(alice, to, iface, fn, args, timestamp);
    assert.ok(!r.execResult.exceptionError, `${fn} reverted: ${revertName(r.execResult)}`);
    return iface.decodeFunctionResult(fn, bytesToHex(r.execResult.returnValue));
  }
  const token = await deploy(BUILT.token.bytecode);
  const pre = await deploy(BUILT.pre.bytecode);
  const args = coder.encode(
    ['address', 'address[3]', 'bool', 'address[4]', 'address'],
    [token, signers.map((w) => w.address), transfers, [carol, ZERO, bob, alice], carol],
  );
  let market = null;
  const created = await send(alice, undefined, concat([BUILT.market.bytecode, args]));
  if (!created.execResult.exceptionError) market = created.createdAddress.toString();
  return { vm, token, pre, market, created, send, deploy, call, read, logsOf };
}

function digestOf(hour, supplied, borrowed, rateBps, fundingWad) {
  const tag = keccak256(toUtf8Bytes('HRCFeed.v1'));
  return keccak256(coder.encode(
    ['bytes32', 'uint256', 'uint256', 'uint256', 'uint256', 'uint256', 'uint256'],
    [tag, CHAIN, hour, supplied, borrowed, rateBps, fundingWad],
  ));
}

function signDigest(wallet, digest) {
  const sig = wallet.signingKey.sign(getBytes(digest));
  return { v: sig.v, r: sig.r, s: sig.s };
}

function emptyFeed() {
  return [0, 0, 0, 0, false, 0, ZeroHash, ZeroHash, 0, ZeroHash, ZeroHash, 0, ZeroHash, ZeroHash];
}

function feedFrom(hour, supplied, borrowed, rateBps, fundingWad, wallets) {
  const digest = digestOf(hour, supplied, borrowed, rateBps, fundingWad);
  const sigs = [0, 1, 2].map((i) => (wallets[i] ? signDigest(wallets[i], digest) : null));
  const at = (i) => sigs[i] || { v: 0, r: ZeroHash, s: ZeroHash };
  return [
    supplied, borrowed, rateBps, fundingWad, true,
    at(0).v, at(0).r, at(0).s,
    at(1).v, at(1).r, at(1).s,
    at(2).v, at(2).r, at(2).s,
  ];
}

const MAINNET = [500, 349, 12298058140850754n, 7773, 1, 0, 55236235337690993n, 42938185433855173n];
const ABOVE = [1450, 0, 0, 8200, 1, 0, 1_000_000, 820099];

async function setWords(h, words) {
  const r = await h.call(alice, h.pre, preIface, 'set', [words]);
  assert.ok(!r.execResult.exceptionError, revertName(r.execResult));
  await mirror(h.vm, h.pre);
}

const FULL = [10000, 0, 0, 10000, 1, 0, 1000, 1000];
const BIG = 10n ** 24n;

async function at(h, from, fn, args, hour) {
  const r = await h.call(from, h.market, marketIface, fn, args, BigInt(hour) * HOUR + 5n);
  assert.ok(!r.execResult.exceptionError, `${fn} reverted: ${revertName(r.execResult)}`);
  return r;
}

async function create(h, kind, strike, ceil, start, end, maxNotional, addressCap, digital = 0) {
  await at(h, alice, 'createSeries', [kind, strike, ceil, start, end, maxNotional, addressCap, digital], 0);
  return (await h.read(h.market, marketIface, 'seriesCount', []))[0] - 1n;
}

async function fund(h, amount) {
  await h.call(alice, h.token, tokenIface, 'mint', [alice, amount]);
  await h.call(alice, h.token, tokenIface, 'approve', [h.market, amount]);
}

function seriesOf(row) {
  if (row && row.state !== undefined) return row;
  return row[0];
}

test('the market source keeps 0x812 and does not call CoreWriter', () => {
  const src = readFileSync(join(ROOT, 'KinkCapMarket.sol'), 'utf8');
  assert.ok(src.includes('address(uint160(0x812))'));
  assert.ok(src.includes('address(uint160(0x809))'));
  assert.ok(!src.includes('FixedPrecompile'));
  assert.ok(!src.includes('FixedL1'));
  assert.ok(!src.includes('function setReader'));
  assert.ok(!src.includes('sendRawAction'));
  assert.ok(!src.includes('3333333333333333333333333333333333333333'));
  const names = BUILT.market.abi.filter((x) => x.type === 'function').map((x) => x.name);
  assert.ok(names.includes('sample'));
  assert.ok(names.includes('createSeries'));
  assert.ok(!names.some((n) => /withdraw|supply/i.test(n)));
  const events = BUILT.market.abi.filter((x) => x.type === 'event').map((x) => x.name);
  for (const name of ['SeriesCreated', 'Minted', 'Sampled', 'Accrued', 'Frozen', 'Settled']) {
    assert.ok(events.includes(name), name);
  }
});

test('chain 999 and chain 1 are refused, and 998 deploys', async () => {
  for (const id of [999, 1]) {
    const h = await setup(id);
    assert.equal(revertName(h.created.execResult), 'WrongChain');
    assert.equal(h.market, null);
  }
  const h = await setup();
  assert.ok(h.market);
  assert.equal((await h.read(h.market, marketIface, 'CHAIN_ID', []))[0], 998n);
  assert.equal((await h.read(h.market, marketIface, 'PRECOMPILE', []))[0].toLowerCase(), PRECOMPILE);
  assert.equal((await h.read(h.market, marketIface, 'TOKEN_INDEX', []))[0], 0n);
  assert.equal((await h.read(h.market, marketIface, 'transfersEnabled', []))[0], false);
  assert.equal((await h.read(h.market, marketIface, 'guardian', []))[0].toLowerCase(), carol);
  assert.equal((await h.read(h.market, marketIface, 'DEFAULT_CEIL_BPS', []))[0], 4000n);
});

test('a python signature recovers in ethers and matches the contract digest', async () => {
  const fx = JSON.parse(readFileSync(join(HERE, 'sig.json'), 'utf8'));
  const recovered = recoverAddress(fx.digest, {
    r: zeroPadValue(fx.r, 32), s: zeroPadValue(fx.s, 32), v: fx.v,
  });
  assert.equal(recovered.toLowerCase(), fx.address);
  assert.equal(digestOf(fx.hour, fx.supplied, fx.borrowed, fx.rate_bps, fx.funding_wad), fx.digest);
  const h = await setup();
  const got = await h.read(h.market, marketIface, 'feedDigest', [
    fx.hour, fx.supplied, fx.borrowed, fx.rate_bps, fx.funding_wad,
  ]);
  assert.equal(got[0], fx.digest);
});

test('the truncated mainnet word is accepted and a quote past the tolerance freezes', async () => {
  const h = await setup();
  await setWords(h, MAINNET);
  const id = await create(h, 0, 0, 0, 10, 13, BIG, BIG);
  const sampled = await at(h, mallory, 'sample', [], 10);
  const ev = h.logsOf(sampled, marketIface, 'Sampled');
  assert.equal(ev.length, 1);
  assert.equal(ev[0].args.fromPrecompile, true);
  assert.equal(ev[0].args.rateBps, 500n);
  assert.equal(ev[0].args.utilBps, 7773n);
  assert.equal((await h.read(h.market, marketIface, 'sampleGood', [10]))[0], true);
  const row = seriesOf(await h.read(h.market, marketIface, 'series', [id]));
  assert.equal(row.state, 0n);
  assert.equal(row.ceilBps, 10000n);

  await setWords(h, ABOVE);
  const above = await at(h, mallory, 'sample', [], 11);
  assert.equal(h.logsOf(above, marketIface, 'Frozen').length, 0);
  assert.equal((await h.read(h.market, marketIface, 'samples', [11])).rateBps, 1454n);

  await setWords(h, [1448, 0, 0, 8200, 1, 0, 1_000_000, 820099]);
  const bad = await at(h, mallory, 'sample', [], 12);
  const frozen = h.logsOf(bad, marketIface, 'Frozen');
  assert.equal(frozen.length, 1);
  assert.equal(frozen[0].args.reason, 0n);
  assert.equal(h.logsOf(bad, marketIface, 'Sampled').length, 0);
  assert.equal((await h.read(h.market, marketIface, 'sampleGood', [12]))[0], false);
  assert.equal((await h.read(h.market, marketIface, 'sampleRecorded', [12]))[0], true);
  const after = seriesOf(await h.read(h.market, marketIface, 'series', [id]));
  assert.equal(after.state, 2n);
});

test('a base rate that is not 5% below the kink is a model change', async () => {
  const h = await setup();
  await setWords(h, [800, 0, 0, 5000, 1, 0, 10000, 5000]);
  const id = await create(h, 1, 500, 0, 4, 8, BIG, BIG);
  const row = seriesOf(await h.read(h.market, marketIface, 'series', [id]));
  assert.equal(row.kind, 1n);
  assert.equal(row.ceilBps, 4000n, 'an unset spread ceiling is 40%');
  const bad = await at(h, alice, 'sample', [], 4);
  const frozen = h.logsOf(bad, marketIface, 'Frozen');
  assert.equal(frozen[0].args.reason, 2n);
  assert.equal((await h.read(h.market, marketIface, 'sampleGood', [4]))[0], false);
});

test('two signers settle a past hour, one signer does not, and the lower rate wins', async () => {
  const h = await setup();
  const id = await create(h, 0, 0, 0, 20, 25, BIG, BIG);
  const one = feedFrom(20, 10000, 9000, 5250, 0, [signers[0]]);
  await at(h, alice, 'sampleWithFeed', [20, one], 30);
  assert.equal((await h.read(h.market, marketIface, 'sampleGood', [20]))[0], false);

  const dup = feedFrom(21, 10000, 9000, 5250, 0, [signers[0], signers[0]]);
  await at(h, alice, 'sampleWithFeed', [21, dup], 30);
  assert.equal((await h.read(h.market, marketIface, 'sampleGood', [21]))[0], false);

  const high = feedFrom(22, 10000, 9000, 5250, 0, [signers[0], signers[1]]);
  const N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141n;
  for (const index of [0, 1]) {
    const base = 5 + index * 3;
    const s = BigInt(high[base + 2]);
    high[base] = high[base] === 27 ? 28 : 27;
    high[base + 2] = zeroPadValue('0x' + (N - s).toString(16), 32);
  }
  await at(h, alice, 'sampleWithFeed', [22, high], 30);
  assert.equal((await h.read(h.market, marketIface, 'sampleGood', [22]))[0], false);

  const ok = feedFrom(23, 10000, 9000, 5250, 0, [signers[0], signers[1]]);
  const okRes = await at(h, alice, 'sampleWithFeed', [23, ok], 30);
  assert.equal(h.logsOf(okRes, marketIface, 'Sampled')[0].args.fromPrecompile, false);
  assert.equal((await h.read(h.market, marketIface, 'samples', [23])).rateBps, 5250n);

  await setWords(h, [500, 0, 0, 5000, 1, 0, 10000, 5000]);
  const both = feedFrom(24, 10000, 9000, 5250, 0, [signers[1], signers[2]]);
  const mixed = await at(h, alice, 'sampleWithFeed', [24, both], 24);
  const sampled = h.logsOf(mixed, marketIface, 'Sampled');
  assert.equal(sampled[0].args.fromPrecompile, true);
  assert.equal(sampled[0].args.rateBps, 500n);
  assert.equal(seriesOf(await h.read(h.market, marketIface, 'series', [id])).state, 0n);
});

test('neither source pays 0, and a precompile reading is only the current hour', async () => {
  const h = await setup();
  await setWords(h, MAINNET);
  const id = await create(h, 0, 0, 0, 4, 6, BIG, BIG);
  await fund(h, BIG);
  await at(h, alice, 'mint', [id, 8760n * 10000n, bob], 4);
  await at(h, alice, 'sampleWithFeed', [4, emptyFeed()], 9);
  assert.equal((await h.read(h.market, marketIface, 'sampleGood', [4]))[0], false);
  const accrued = await at(h, alice, 'accrue', [id], 9);
  assert.equal(h.logsOf(accrued, marketIface, 'Accrued')[0].args.pay, 0n);
  await at(h, mallory, 'sample', [], 5);
  assert.equal((await h.read(h.market, marketIface, 'sampleGood', [5]))[0], true);
  assert.equal((await h.read(h.market, marketIface, 'sampleFromPrecompile', [5]))[0], true);
  const life = await h.read(h.market, marketIface, 'life', [id]);
  assert.equal(life.nextHour, 5n);
});

test('transfers stay off unless the deployment said so, and a mint is not a transfer', async () => {
  const off = await setup();
  const id = await create(off, 0, 10000, 0, 1, 2, BIG, BIG);
  await fund(off, 1000n);
  const minted = await at(off, alice, 'mint', [id, 100, bob], 1);
  assert.equal(off.logsOf(minted, marketIface, 'Minted').length, 1);
  const moved = await off.call(bob, off.market, marketIface, 'transferPosition', [0, carol]);
  assert.equal(revertName(moved.execResult), 'TransfersDisabled');
  const safe = await off.call(bob, off.market, marketIface, 'safeTransferFrom', [bob, carol, 0]);
  assert.equal(revertName(safe.execResult), 'TransfersDisabled');
  const holder = (await off.read(off.market, marketIface, 'positions', [0])).holder;
  assert.equal(holder.toLowerCase(), bob);

  const on = await setup(CHAIN, true);
  const id2 = await create(on, 0, 10000, 0, 1, 2, BIG, BIG);
  await fund(on, 1000n);
  await at(on, alice, 'mint', [id2, 100, bob], 1);
  const ok = await at(on, bob, 'transferPosition', [0, carol], 1);
  assert.ok(ok);
  const now = (await on.read(on.market, marketIface, 'positions', [0])).holder;
  assert.equal(now.toLowerCase(), carol);
});

test('each series keeps its own collateral, and a donation stays put', async () => {
  const h = await setup();
  await setWords(h, FULL);
  const a = await create(h, 2, 0, 0, 7, 9, BIG, BIG, 100);
  const b = await create(h, 0, 5000, 0, 7, 9, BIG, BIG);
  await fund(h, BIG);
  await at(h, alice, 'mint', [a, 10000, bob], 1);
  await at(h, alice, 'mint', [b, 8760n * 10000n, carol], 1);
  assert.equal((await h.read(h.market, marketIface, 'seriesCollateral', [a]))[0], 200n);
  assert.equal((await h.read(h.market, marketIface, 'seriesCollateral', [b]))[0], 10000n);
  await h.call(alice, h.token, tokenIface, 'mint', [h.market, 7n]);
  for (let hour = 1n; hour <= 8n; hour++) {
    await at(h, mallory, 'sample', [], hour);
    if (hour >= 7n) {
      await at(h, alice, 'accrue', [a], hour);
      await at(h, alice, 'accrue', [b], hour);
    }
  }
  const beforeB = (await h.read(h.market, marketIface, 'seriesCollateral', [b]))[0];
  const redeemA = await at(h, bob, 'redeem', [0], 8);
  assert.equal(h.logsOf(redeemA, marketIface, 'Settled').length, 0);
  assert.equal((await h.read(h.market, marketIface, 'seriesCollateral', [b]))[0], beforeB);
  const bobBal = (await h.read(h.token, tokenIface, 'balanceOf', [bob]))[0];
  assert.equal(bobBal, 200n);
  const redeemB = await at(h, carol, 'redeem', [2], 8);
  assert.ok(redeemB);
  assert.equal((await h.read(h.token, tokenIface, 'balanceOf', [h.market]))[0], 7n);
  const aliceBal = (await h.read(h.token, tokenIface, 'balanceOf', [alice]))[0];
  assert.ok(aliceBal < BIG);
});

test('a gap over two hours freezes, two good hours unfreeze, and the window is 24h', async () => {
  const h = await setup();
  await setWords(h, MAINNET);
  const id = await create(h, 1, 1000, 4000, 10, 40, BIG, BIG);
  await at(h, mallory, 'sample', [], 10);
  assert.equal(seriesOf(await h.read(h.market, marketIface, 'series', [id])).state, 0n);
  await at(h, mallory, 'sample', [], 12);
  assert.equal(seriesOf(await h.read(h.market, marketIface, 'series', [id])).state, 0n, 'a two-hour step is inside the gap');
  const jumped = await at(h, mallory, 'sample', [], 15);
  assert.equal(h.logsOf(jumped, marketIface, 'Frozen')[0].args.reason, 1n);
  assert.equal((await h.read(h.market, marketIface, 'sampleGood', [15]))[0], true);

  const early = await h.call(carol, h.market, marketIface, 'unfreeze', [id], 16n * HOUR);
  assert.ok(early.execResult.exceptionError, 'one good hour is not two');
  await at(h, mallory, 'sample', [], 16);
  await at(h, mallory, 'sample', [], 17);
  const stranger = await h.call(mallory, h.market, marketIface, 'unfreeze', [id], 17n * HOUR);
  assert.equal(revertName(stranger.execResult), 'guardian');
  await at(h, carol, 'unfreeze', [id], 17);
  assert.equal(seriesOf(await h.read(h.market, marketIface, 'series', [id])).state, 0n);

  await setWords(h, [800, 0, 0, 5000, 1, 0, 10000, 5000]);
  await at(h, mallory, 'sample', [], 18);
  assert.equal(seriesOf(await h.read(h.market, marketIface, 'series', [id])).state, 2n);
  const late = await h.call(carol, h.market, marketIface, 'unfreeze', [id], (18n + 25n) * HOUR);
  assert.equal(revertName(late.execResult), 'window');
  const settled = await at(h, mallory, 'settle', [id], 18n + 25n);
  const ev = h.logsOf(settled, marketIface, 'Settled');
  assert.equal(ev.length, 1);
  assert.equal(ev[0].args.hadGoodHour, false);
  assert.equal(seriesOf(await h.read(h.market, marketIface, 'series', [id])).state, 3n);
});

test('only the registered source can lower a cap, and a lower cap does not seize collateral', async () => {
  const h = await setup();
  const id = await create(h, 0, 10000, 0, 3, 4, 50, 50);
  await fund(h, 1000n);
  await at(h, alice, 'mint', [id, 10, bob], 3);
  const attack = await h.call(mallory, h.market, marketIface, 'lowerCap', [id, keccak256(toUtf8Bytes('HRC')), 0]);
  assert.ok(attack.execResult.exceptionError);
  const dead = await h.call(alice, h.market, marketIface, 'lowerCap', [id, keccak256(toUtf8Bytes('RXB')), 0]);
  assert.ok(dead.execResult.exceptionError, 'address(0) cannot lower');
  const same = await h.call(alice, h.market, marketIface, 'lowerCap', [id, keccak256(toUtf8Bytes('HRC')), 50]);
  assert.ok(same.execResult.exceptionError, 'not strictly lower');
  const cut = await h.call(carol, h.market, marketIface, 'lowerCap', [id, keccak256(toUtf8Bytes('CGC')), 0]);
  assert.ok(!cut.execResult.exceptionError, revertName(cut.execResult));
  const ev = h.logsOf(cut, marketIface, 'CapLowered');
  assert.equal(ev.length, 1);
  assert.equal(ev[0].args.hadPrevious, false);
  assert.equal(ev[0].args.effectiveAfter, 0n);
  const again = await h.call(alice, h.market, marketIface, 'mint', [id, 1, carol]);
  assert.ok(again.execResult.exceptionError, 'new mints stop');
  await at(h, alice, 'sampleWithFeed', [3, emptyFeed()], 9);
  await at(h, alice, 'accrue', [id], 9);
  const back = await at(h, bob, 'redeem', [0], 9);
  assert.equal(h.logsOf(back, marketIface, 'Redeemed')[0].args.paid, 0n);
});

test('a signer registers only their own post-quantum slot, and the slot is stored', async () => {
  const h = await setup();
  const material = keccak256(toUtf8Bytes('ml-dsa-test-key'));
  const denied = await h.call(signers[1].address, h.market, marketIface, 'registerPqSlot', [0, material]);
  assert.ok(denied.execResult.exceptionError);
  const ok = await h.call(signers[0].address, h.market, marketIface, 'registerPqSlot', [0, material]);
  assert.ok(!ok.execResult.exceptionError, revertName(ok.execResult));
  assert.equal((await h.read(h.market, marketIface, 'pqSlots', [0]))[0], material);
});

test('a rate-cap payoff stays inside the deposit, and the spread uses the ceiling', async () => {
  const h = await setup();
  await setWords(h, FULL);
  const cap = await create(h, 0, 0, 0, 1, 7, BIG, BIG);
  await fund(h, BIG);
  const notional = 8760n;
  await at(h, alice, 'mint', [cap, notional, bob], 1);
  assert.equal((await h.read(h.market, marketIface, 'seriesCollateral', [cap]))[0], 6n);
  for (let hour = 1n; hour <= 6n; hour++) {
    await at(h, mallory, 'sample', [], hour);
    await at(h, alice, 'accrue', [cap], hour);
  }
  const claim = (await h.read(h.market, marketIface, 'positions', [0])).claim;
  assert.equal(claim, 1n);
  await at(h, bob, 'redeem', [0], 6);
  assert.equal((await h.read(h.token, tokenIface, 'balanceOf', [bob]))[0], claim);

  const spread = await create(h, 1, 500, 0, 7, 13, BIG, BIG);
  const size = 8760n * 10000n;
  await at(h, alice, 'mint', [spread, size, carol], 7);
  const deposit = (await h.read(h.market, marketIface, 'seriesCollateral', [spread]))[0];
  assert.equal(deposit, 21000n);
  let spreadClaim = 0n;
  let hours = 0;
  for (let hour = 7n; hour <= 12n; hour++) {
    await at(h, mallory, 'sample', [], hour);
    await at(h, alice, 'accrue', [spread], hour);
    spreadClaim = (await h.read(h.market, marketIface, 'positions', [2])).claim;
    hours += 1;
    assert.ok(spreadClaim <= deposit);
    assert.equal(spreadClaim, 3500n * BigInt(hours));
  }
  assert.equal(spreadClaim, deposit);
});

test('the L1 block is the 0x809 word when that precompile answers', async () => {
  const h = await setup();
  const l1 = await h.deploy(BUILT.l1.bytecode);
  const set = await h.call(alice, l1, l1Iface, 'set', [424242n]);
  assert.ok(!set.execResult.exceptionError, revertName(set.execResult));
  const src = new Address(hexToBytes(l1));
  const code = await h.vm.stateManager.getContractCode(src);
  const dest = new Address(hexToBytes(L1));
  await h.vm.stateManager.putContractCode(dest, code);
  const value = await h.vm.stateManager.getContractStorage(src, word(0));
  await h.vm.stateManager.putContractStorage(dest, word(0), value);
  await setWords(h, MAINNET);
  await create(h, 0, 0, 0, 3, 4, BIG, BIG);
  const sampled = await at(h, mallory, 'sample', [], 3);
  const ev = h.logsOf(sampled, marketIface, 'Sampled')[0];
  assert.equal(ev.args.l1Block, 424242n);
  assert.equal((await h.read(h.market, marketIface, 'sampleL1Known', [3]))[0], true);
});

