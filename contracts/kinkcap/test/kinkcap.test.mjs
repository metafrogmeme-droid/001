/**
 * Kink Cap — compile the market, run it on an in-process EVM. No network.
 * The precompile is etched at 0x812 for the test only; that runtime is not
 * in the market's bytecode.
 *
 * Chain 998 only. Two signers of this market's own three. Collateral of one
 * series cannot pay another. A missing rate pays 0 and is not stored as 0%.
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
  };
}

const BUILT = compile();
const marketIface = new Interface(BUILT.market.abi);
const tokenIface = new Interface(BUILT.token.abi);
const preIface = new Interface(BUILT.pre.abi);
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
    ['address', 'uint64', 'address[3]', 'bool', 'address[4]'],
    [token, 0, signers.map((w) => w.address), transfers, [carol, ZERO, bob, alice]],
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

test('the market source keeps 0x812 constant and does not carry the test stand-in', () => {
  const src = readFileSync(join(ROOT, 'KinkCapMarket.sol'), 'utf8');
  assert.ok(src.includes('address(uint160(0x812))'));
  assert.ok(!src.includes('FixedPrecompile'));
  assert.ok(!src.includes('function setReader'));
  const names = BUILT.market.abi.filter((x) => x.type === 'function').map((x) => x.name);
  assert.ok(!names.some((n) => /withdraw/i.test(n)));
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
  assert.equal((await h.read(h.market, marketIface, 'usdcTokenIndex', []))[0], 0n);
  assert.equal((await h.read(h.market, marketIface, 'transfersEnabled', []))[0], false);
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

test('exp of the 0.1095 funding rate stays inside a wad of the series', async () => {
  const h = await setup();
  const x = 109500000000000000n;
  const got = (await h.read(h.market, marketIface, 'expWad', [x]))[0];
  let term = WAD;
  let sum = WAD;
  for (let i = 1n; i <= 32n; i++) {
    term = term * x / WAD / i;
    sum += term;
  }
  const diff = got > sum ? got - sum : sum - got;
  assert.ok(diff < 10n ** 10n, `exp gap ${diff}`);
  const strike = (await h.read(h.market, marketIface, 'previewCarryStrike', [x, 0]))[0];
  const matched = got - WAD;
  assert.ok(strike > matched - 10n ** 10n && strike < matched + 10n ** 10n);
});

test('the truncated mainnet word is accepted and a six-bp gap is not', async () => {
  const h = await setup();
  await setWords(h, MAINNET);
  const id = await open(h, 0, 0, 0, 10, 3, 10n ** 24n, 10n ** 24n);
  await fund(h, 10n ** 24n);
  await h.call(alice, h.market, marketIface, 'mint', [id, 8760, bob], 10n * HOUR + 10n);
  const r = await h.call(alice, h.market, marketIface, 'settleHour', [id, emptyFeed()], 10n * HOUR + 10n);
  assert.ok(!r.execResult.exceptionError, revertName(r.execResult));
  const st = await settled(h, id, 10);
  assert.equal(st.hadRate, true);
  assert.equal(st.source, 1n);
  assert.equal(st.rateWad, 5n * 10n ** 16n);

  await setWords(h, ABOVE);
  const r2 = await h.call(alice, h.market, marketIface, 'settleHour', [id, emptyFeed()], 11n * HOUR + 10n);
  assert.ok(!r2.execResult.exceptionError, revertName(r2.execResult));
  const st2 = await settled(h, id, 11);
  assert.equal(st2.hadRate, true);
  assert.equal(st2.rateWad, 145470250000000000n);

  await setWords(h, [1448, 0, 0, 8200, 1, 0, 1_000_000, 820099]);
  const r3 = await h.call(alice, h.market, marketIface, 'settleHour', [id, emptyFeed()], 12n * HOUR + 10n);
  assert.ok(!r3.execResult.exceptionError, revertName(r3.execResult));
  const st3 = await settled(h, id, 12);
  assert.equal(st3.hadRate, false);
  assert.equal(st3.rateWad, 0n);
  assert.equal(st3.source, 0n);
  const ev = h.logsOf(r3, marketIface, 'HourSettled');
  assert.equal(ev[0].args.hadRate, false);
});

test('two signers settle a past hour, one signer does not, and the lower rate wins', async () => {
  const h = await setup();
  const id = await open(h, 0, 0, 0, 20, 5, 10n ** 24n, 10n ** 24n);
  await fund(h, 10n ** 24n);
  await h.call(alice, h.market, marketIface, 'mint', [id, 8760, bob]);
  const one = feedFrom(20, 10000, 9000, 5250, 0, [signers[0]]);
  const past = await h.call(alice, h.market, marketIface, 'settleHour', [id, one], 30n * HOUR);
  assert.ok(!past.execResult.exceptionError, revertName(past.execResult));
  let st = await settled(h, id, 20);
  assert.equal(st.hadRate, false, 'one signer is not a feed');

  const dup = feedFrom(21, 10000, 9000, 5250, 0, [signers[0], signers[0]]);
  const dupRes = await h.call(alice, h.market, marketIface, 'settleHour', [id, dup], 30n * HOUR);
  assert.ok(!dupRes.execResult.exceptionError, revertName(dupRes.execResult));
  st = await settled(h, id, 21);
  assert.equal(st.hadRate, false, 'a repeated signer counts once');

  const high = feedFrom(22, 10000, 9000, 5250, 0, [signers[0], signers[1]]);
  const N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141n;
  for (const index of [0, 1]) {
    const base = 5 + index * 3;
    const s = BigInt(high[base + 2]);
    high[base] = high[base] === 27 ? 28 : 27;
    high[base + 2] = zeroPadValue('0x' + (N - s).toString(16), 32);
  }
  const highRes = await h.call(alice, h.market, marketIface, 'settleHour', [id, high], 30n * HOUR);
  assert.ok(!highRes.execResult.exceptionError, revertName(highRes.execResult));
  st = await settled(h, id, 22);
  assert.equal(st.hadRate, false, 'high s is not a signature');

  const ok = feedFrom(23, 10000, 9000, 5250, 0, [signers[0], signers[1]]);
  const okRes = await h.call(alice, h.market, marketIface, 'settleHour', [id, ok], 30n * HOUR);
  assert.ok(!okRes.execResult.exceptionError, revertName(okRes.execResult));
  st = await settled(h, id, 23);
  assert.equal(st.hadRate, true);
  assert.equal(st.source, 2n);
  assert.equal(st.rateWad, 525n * WAD / 1000n);

  await setWords(h, [500, 0, 0, 5000, 1, 0, 10000, 5000]);
  const both = feedFrom(24, 10000, 9000, 5250, 0, [signers[1], signers[2]]);
  const mixed = await h.call(alice, h.market, marketIface, 'settleHour', [id, both], 24n * HOUR + 5n);
  assert.ok(!mixed.execResult.exceptionError, revertName(mixed.execResult));
  st = await settled(h, id, 24);
  assert.equal(st.source, 3n);
  assert.equal(st.rateWad, 5n * 10n ** 16n, 'the lower rate');
});

test('neither source pays 0, and a precompile reading is only the current hour', async () => {
  const h = await setup();
  await setWords(h, MAINNET);
  const id = await open(h, 0, 0, 0, 4, 2, 10n ** 24n, 10n ** 24n);
  await fund(h, 10n ** 24n);
  await h.call(alice, h.market, marketIface, 'mint', [id, 8760, bob]);
  await h.call(alice, h.market, marketIface, 'settleHour', [id, emptyFeed()], 9n * HOUR);
  const past = await settled(h, id, 4);
  assert.equal(past.hadRate, false, 'a past hour does not read the current precompile');
  await h.call(alice, h.market, marketIface, 'settleHour', [id, emptyFeed()], 5n * HOUR + 1n);
  const now = await settled(h, id, 5);
  assert.equal(now.hadRate, true);
  assert.equal(now.source, 1n);
  const next = (await h.read(h.market, marketIface, 'series', [id])).nextHour;
  assert.equal(next, 6n);
});

test('transfers stay off unless the deployment said so, and a mint is not a transfer', async () => {
  const off = await setup();
  const id = await open(off, 0, WAD, 0, 1, 1, 10n ** 24n, 10n ** 24n);
  await fund(off, 1000n);
  const minted = await off.call(alice, off.market, marketIface, 'mint', [id, 100, bob]);
  assert.ok(!minted.execResult.exceptionError, revertName(minted.execResult));
  const moved = await off.call(bob, off.market, marketIface, 'transferPosition', [0, carol]);
  assert.equal(revertName(moved.execResult), 'TransfersDisabled');
  const safe = await off.call(bob, off.market, marketIface, 'safeTransferFrom', [bob, carol, 0]);
  assert.equal(revertName(safe.execResult), 'TransfersDisabled');
  const holder = (await off.read(off.market, marketIface, 'positions', [0])).holder;
  assert.equal(holder.toLowerCase(), bob);

  const on = await setup(CHAIN, true);
  const id2 = await open(on, 0, WAD, 0, 1, 1, 10n ** 24n, 10n ** 24n);
  await fund(on, 1000n);
  await on.call(alice, on.market, marketIface, 'mint', [id2, 100, bob]);
  const ok = await on.call(bob, on.market, marketIface, 'transferPosition', [0, carol]);
  assert.ok(!ok.execResult.exceptionError, revertName(ok.execResult));
  const now = (await on.read(on.market, marketIface, 'positions', [0])).holder;
  assert.equal(now.toLowerCase(), carol);
});

test('each series keeps its own collateral, and a donation stays put', async () => {
  const h = await setup();
  const cap = 500_000_000n * 10n ** 8n;
  await setWords(h, [10000, 0, 0, 10000, 1, 0, cap, cap]);
  const a = await open(h, 2, 0, 10, 70, 2, 10n ** 24n, 10n ** 24n);
  const b = await open(h, 0, WAD / 2n, 0, 70, 2, 10n ** 24n, 10n ** 24n);
  await fund(h, 10n ** 24n);
  await h.call(alice, h.market, marketIface, 'mint', [a, 1, bob], 70n * HOUR);
  await h.call(alice, h.market, marketIface, 'mint', [b, 8760, carol], 70n * HOUR);
  assert.equal((await h.read(h.market, marketIface, 'seriesCollateral', [a]))[0], 20n);
  assert.equal((await h.read(h.market, marketIface, 'seriesCollateral', [b]))[0], 1n);
  await h.call(alice, h.token, tokenIface, 'mint', [h.market, 7n]);
  for (let i = 0; i < 2; i++) {
    const ts = (70n + BigInt(i)) * HOUR + 10n;
    await h.call(alice, h.market, marketIface, 'settleHour', [a, emptyFeed()], ts);
    await h.call(alice, h.market, marketIface, 'settleHour', [b, emptyFeed()], ts);
  }
  const longA = 0n;
  const longB = 2n;
  const beforeB = (await h.read(h.market, marketIface, 'seriesCollateral', [b]))[0];
  const redeemA = await h.call(bob, h.market, marketIface, 'redeem', [longA]);
  assert.ok(!redeemA.execResult.exceptionError, revertName(redeemA.execResult));
  assert.equal((await h.read(h.market, marketIface, 'seriesCollateral', [b]))[0], beforeB);
  assert.equal((await h.read(h.token, tokenIface, 'balanceOf', [bob]))[0], 20n);
  const redeemB = await h.call(carol, h.market, marketIface, 'redeem', [longB]);
  assert.ok(!redeemB.execResult.exceptionError, revertName(redeemB.execResult));
  assert.equal((await h.read(h.token, tokenIface, 'balanceOf', [carol]))[0], 0n);
  assert.equal((await h.read(h.token, tokenIface, 'balanceOf', [alice]))[0], 10n ** 24n - 20n);
  assert.equal((await h.read(h.token, tokenIface, 'balanceOf', [h.market]))[0], 7n);
});

test('cap-hit pays in the first hour, and the lower borrowed amount is the one that counts', async () => {
  const h = await setup();
  const cap = 500_000_000n * 10n ** 8n;
  await setWords(h, [500, 0, 0, 5000, 1, 0, cap * 2n, cap]);
  const id = await open(h, 2, 0, 4, 80, 1, 10n ** 24n, 10n ** 24n);
  await fund(h, 10n ** 24n);
  await h.call(alice, h.market, marketIface, 'mint', [id, 3, bob]);
  await h.call(alice, h.market, marketIface, 'settleHour', [id, emptyFeed()], 80n * HOUR + 1n);
  const st = await settled(h, id, 80);
  assert.equal(st.hadRate, true);
  await h.call(bob, h.market, marketIface, 'redeem', [0]);
  assert.equal((await h.read(h.token, tokenIface, 'balanceOf', [bob]))[0], 12n);

  const quiet = cap * 98n / 100n - 1n;
  await setWords(h, [500, 0, 0, 5000, 1, 0, quiet * 2n, quiet]);
  const id2 = await open(h, 2, 0, 4, 81, 1, 10n ** 24n, 10n ** 24n);
  await h.call(alice, h.market, marketIface, 'mint', [id2, 3, bob]);
  await h.call(alice, h.market, marketIface, 'settleHour', [id2, emptyFeed()], 81n * HOUR + 1n);
  await h.call(bob, h.market, marketIface, 'redeem', [2]);
  assert.equal((await h.read(h.token, tokenIface, 'balanceOf', [alice]))[0], 10n ** 24n - 12n);

  await setWords(h, [500, 0, 0, 5000, 1, 0, cap * 2n, cap]);
  const id3 = await open(h, 2, 0, 4, 82, 1, 10n ** 24n, 10n ** 24n);
  await h.call(alice, h.market, marketIface, 'mint', [id3, 3, bob]);
  const low = feedFrom(82, 10n ** 17n, 10n ** 15n, 500, 0, [signers[0], signers[1]]);
  await h.call(alice, h.market, marketIface, 'settleHour', [id3, low], 82n * HOUR + 1n);
  const st3 = await settled(h, id3, 82);
  assert.equal(st3.borrowed, 10n ** 15n);
  await h.call(bob, h.market, marketIface, 'redeem', [4]);
  assert.equal((await h.read(h.token, tokenIface, 'balanceOf', [bob]))[0], 12n);
});

test('only the registered source can lower a cap, and a lower cap does not seize collateral', async () => {
  const h = await setup();
  const id = await open(h, 0, WAD, 0, 3, 1, 50, 50);
  await fund(h, 1000n);
  await h.call(alice, h.market, marketIface, 'mint', [id, 10, bob]);
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
  assert.equal(ev[0].args.proposed, 0n);
  assert.equal(ev[0].args.effectiveAfter, 0n);
  const again = await h.call(alice, h.market, marketIface, 'mint', [id, 1, carol]);
  assert.ok(again.execResult.exceptionError, 'new mints stop');
  await h.call(alice, h.market, marketIface, 'settleHour', [id, emptyFeed()], 9n * HOUR);
  const back = await h.call(bob, h.market, marketIface, 'redeem', [0]);
  assert.ok(!back.execResult.exceptionError, revertName(back.execResult));
  const eff = await h.read(h.market, marketIface, 'effectiveCap', [id]);
  assert.equal(eff[0], true);
  assert.equal(eff[1], 0n);
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

test('a rate-cap payoff stays inside the deposit', async () => {
  const h = await setup();
  await setWords(h, [10000, 0, 0, 10000, 1, 0, 1000, 1000]);
  const id = await open(h, 0, 0, 0, 90, 6, 10n ** 24n, 10n ** 24n);
  await fund(h, 10n ** 24n);
  const minted = await h.call(alice, h.market, marketIface, 'mint', [id, 8760, bob]);
  assert.ok(!minted.execResult.exceptionError, revertName(minted.execResult));
  const deposit = (await h.read(h.market, marketIface, 'seriesCollateral', [id]))[0];
  assert.equal(deposit, 6n);
  for (let i = 0; i < 6; i++) {
    const r = await h.call(alice, h.market, marketIface, 'settleHour', [id, emptyFeed()], (90n + BigInt(i)) * HOUR + 3n);
    assert.ok(!r.execResult.exceptionError, revertName(r.execResult));
  }
  const claim = (await h.read(h.market, marketIface, 'positions', [0])).claim;
  assert.ok(claim <= deposit);
  assert.equal(claim, 1n);
  await h.call(bob, h.market, marketIface, 'redeem', [0]);
  assert.equal((await h.read(h.token, tokenIface, 'balanceOf', [bob]))[0], claim);
  assert.equal((await h.read(h.token, tokenIface, 'balanceOf', [alice]))[0], 10n ** 24n - claim);
});

async function settled(h, id, hour) {
  const row = await h.read(h.market, marketIface, 'settledHour', [id, hour]);
  const s = row[0];
  return {
    done: s[0], hadRate: s[1], fundingKnown: s[2], source: s[3], rateWad: s[4], borrowed: s[5],
  };
}

async function open(h, kind, strike, digital, start, duration, maxNotional, perAddress) {
  const r = await h.call(alice, h.market, marketIface, 'openSeries', [
    kind, strike, digital, start, duration, maxNotional, perAddress,
  ]);
  assert.ok(!r.execResult.exceptionError, revertName(r.execResult));
  return (await h.read(h.market, marketIface, 'seriesCount', []))[0] - 1n;
}

async function fund(h, amount) {
  await h.call(alice, h.token, tokenIface, 'mint', [alice, amount]);
  await h.call(alice, h.token, tokenIface, 'approve', [h.market, amount]);
}
