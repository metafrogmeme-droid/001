/**
 * CoverEscrow — compile the real contract, run the real bytecode on an
 * in-process EVM. No network. The promises under test:
 *   testnet only (998 and 84532; mainnet chain ids revert),
 *   ADL and stop-gap payouts, including the cap and the deductible,
 *   a zero gap is not a payment, and an unreadable price is not a zero price,
 *   one claim, one settlement,
 *   a stale oracle does not pay,
 *   stop-gap ignores a caller-supplied fill,
 *   a position that was not reduced does not pay, and an unreadable
 *   position is not treated as flat,
 *   majors only (anything off the allowlist reverts),
 *   cooldown and minimum hold,
 *   a re-entering token cannot open a second policy or settle twice.
 */
import test from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import solc from 'solc';
import { VM } from '@ethereumjs/vm';
import { Common, Hardfork } from '@ethereumjs/common';
import { Account, Address, hexToBytes, bytesToHex } from '@ethereumjs/util';
import { Interface, getBytes, concat, id as keccakId } from 'ethers';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const SETTINGS = {
  optimizer: { enabled: true, runs: 200 },
  viaIR: true,
  evmVersion: 'paris',
  outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object'] } },
};

function compile() {
  const sources = {
    'CoverEscrow.sol': { content: readFileSync(join(root, 'CoverEscrow.sol'), 'utf8') },
    'companions.sol': { content: readFileSync(join(here, 'companions.sol'), 'utf8') },
  };
  const out = JSON.parse(solc.compile(JSON.stringify({ language: 'Solidity', sources, settings: SETTINGS })));
  const fatal = (out.errors || []).filter((e) => e.severity === 'error');
  if (fatal.length) throw new Error(JSON.stringify(fatal, null, 2));
  const art = (file, name) => {
    const c = out.contracts[file][name];
    return { abi: c.abi, bytecode: '0x' + c.evm.bytecode.object, iface: new Interface(c.abi) };
  };
  return {
    escrow: art('CoverEscrow.sol', 'CoverEscrow'),
    reader: art('CoverEscrow.sol', 'HyperPrecompileReader'),
    readings: art('companions.sol', 'ScriptedReadings'),
    token: art('companions.sol', 'TestToken'),
    silent: art('companions.sol', 'SilentToken'),
    fee: art('companions.sol', 'FeeToken'),
    reenter: art('companions.sol', 'ReenterToken'),
    settleTok: art('companions.sol', 'SettleToken'),
    px: art('companions.sol', 'PxPrecompile'),
    pos: art('companions.sol', 'PosPrecompile'),
  };
}

const ART = compile();
const GAS = 30_000_000n;
const SCALE = 10n ** 8n;
const E6 = 10n ** 6n;
const alice = '0x' + 'a1'.repeat(20);
const bob = '0x' + 'b0'.repeat(20);
const carol = '0x' + 'c0'.repeat(20);
const REENTERED = keccakId('Reentered()').slice(0, 10);
const HOLD = 'Not offered to customers. Licence review pending. Do not enable in production.';

const source = readFileSync(join(root, 'CoverEscrow.sol'), 'utf8');

function namedRevert(res, iface) {
  const ret = bytesToHex(res.returnValue || new Uint8Array());
  if (ret.startsWith('0x08c379a0')) {
    return new Interface(['function Error(string)']).decodeFunctionData('Error', ret)[0];
  }
  if (ret && ret !== '0x') {
    try {
      const parsed = iface.parseError(ret);
      if (parsed) return parsed.name;
    } catch { /* not this interface */ }
  }
  return res.exceptionError ? String(res.exceptionError.error) : null;
}

async function newVm(chainId) {
  const common = Common.custom({ chainId }, { hardfork: Hardfork.Shanghai });
  const vm = await VM.create({ common });
  for (const who of [alice, bob, carol]) {
    await vm.stateManager.putAccount(new Address(hexToBytes(who)), new Account(0n, 1_000_000n * 10n ** 18n));
  }
  return vm;
}

function harness(vm, escrowIface) {
  let clock = 1_800_000_000n;
  const header = () => ({
    timestamp: clock,
    number: clock,
    coinbase: Address.zero(),
    difficulty: 0n,
    prevRandao: new Uint8Array(32),
    gasLimit: GAS,
    baseFeePerGas: 0n,
    getBlobGasPrice: () => 0n,
  });
  async function send(from, to, data, value = 0n) {
    const tx = {
      caller: new Address(hexToBytes(from)),
      data: getBytes(data),
      gasLimit: GAS,
      value,
      block: { header: header() },
    };
    if (to) tx.to = new Address(hexToBytes(to));
    const r = await vm.evm.runCall(tx);
    return { res: r.execResult, created: r.createdAddress ? r.createdAddress.toString() : null };
  }
  async function deploy(from, bytecode) {
    const out = await send(from, null, bytecode);
    if (out.res.exceptionError) {
      throw new Error('deploy reverted: ' + namedRevert(out.res, escrowIface));
    }
    return out.created;
  }
  function at(t) { clock = t; }
  function now() { return clock; }
  return { send, deploy, at, now, vm };
}

async function view(h, iface, to, fn, args = []) {
  const out = await h.send(alice, to, iface.encodeFunctionData(fn, args));
  assert.ok(!out.res.exceptionError, `${fn} reverted: ${namedRevert(out.res, iface)}`);
  return iface.decodeFunctionResult(fn, bytesToHex(out.res.returnValue));
}

function expectRevert(out, iface, name) {
  assert.ok(out.res.exceptionError, `expected ${name}, call returned`);
  assert.equal(namedRevert(out.res, iface), name, `expected ${name}, got ${namedRevert(out.res, iface)}`);
}

async function construct(chainId, bytecode) {
  const vm = await newVm(chainId);
  const h = harness(vm, ART.escrow.iface);
  return h.send(alice, null, bytecode);
}

function escrowDeployData(token, readings, underwriter, majors = [0]) {
  return concat([
    ART.escrow.bytecode,
    ART.escrow.iface.encodeDeploy([token, readings, underwriter, majors]),
  ]);
}

test('the hold is written on the contract, and the precompiles are the ones named for these covers', () => {
  assert.ok(source.includes(HOLD));
  assert.equal((source.match(/0x0000000000000000000000000000000000000800/g) || []).length, 1);
  assert.ok(source.includes('0x0000000000000000000000000000000000000806'));
  assert.ok(source.includes('0x0000000000000000000000000000000000000807'));
  assert.ok(!/guaranteed|risk-free|risk free|\$RCLAW/i.test(source));
  const names = ART.escrow.abi.filter((x) => x.type === 'function').map((x) => x.name);
  for (const banned of ['transferOwnership', 'upgradeTo', 'upgradeToAndCall', 'setAllowed', 'setReadings', 'setUnderwriter']) {
    assert.ok(!names.includes(banned), banned);
  }
});

test('the constructor accepts only the two testnets', async () => {
  const data = escrowDeployData(alice, alice, bob);
  for (const id of [1n, 999n, 8453n, 42161n, 10n]) {
    const out = await construct(id, data);
    expectRevert(out, ART.escrow.iface, 'ChainRefused');
  }
  for (const id of [998n, 84532n]) {
    const out = await construct(id, data);
    assert.ok(!out.res.exceptionError, `chain ${id} reverted: ${namedRevert(out.res, ART.escrow.iface)}`);
    assert.ok(out.created);
  }
  const empty = escrowDeployData(alice, alice, bob, []);
  expectRevert(await construct(998n, empty), ART.escrow.iface, 'NoMajors');
  const zero = concat([
    ART.escrow.bytecode,
    ART.escrow.iface.encodeDeploy(['0x' + '00'.repeat(20), alice, bob, [0]]),
  ]);
  expectRevert(await construct(998n, zero), ART.escrow.iface, 'ZeroAddress');
});

test('the precompile reader refuses mainnet and does not invent a price', async () => {
  const bad = await construct(999n, ART.reader.bytecode);
  expectRevert(bad, ART.reader.iface, 'ChainRefused');

  const vm = await newVm(998n);
  const h = harness(vm, ART.reader.iface);
  h.at(1_800_000_100n);
  const reader = await h.deploy(alice, ART.reader.bytecode);
  for (const [fn, addr] of [
    ['POSITION_PRECOMPILE', '0x0000000000000000000000000000000000000800'],
    ['MARK_PRECOMPILE', '0x0000000000000000000000000000000000000806'],
    ['ORACLE_PRECOMPILE', '0x0000000000000000000000000000000000000807'],
  ]) {
    const got = (await view(h, ART.reader.iface, reader, fn))[0];
    assert.equal(got.toLowerCase(), addr);
  }
  // Nothing is installed: a failed precompile is unreadable, not a zero price
  // with a fresh timestamp and not a flat position.
  let mark = await view(h, ART.reader.iface, reader, 'mark', [0]);
  assert.equal(mark[0], 0n);
  assert.equal(mark[1], 0n);
  let pos = await view(h, ART.reader.iface, reader, 'positionSzi', [alice, 0]);
  assert.equal(pos[0], 0n);
  assert.equal(pos[1], false);

  async function install(precompile, initCode) {
    const deployed = await h.deploy(alice, initCode);
    const code = await vm.stateManager.getContractCode(new Address(hexToBytes(deployed)));
    const dest = new Address(hexToBytes(precompile));
    await vm.stateManager.putAccount(dest, new Account(0n, 0n));
    await vm.stateManager.putContractCode(dest, code);
  }
  const pxCode = concat([ART.px.bytecode, ART.px.iface.encodeDeploy([12345n])]);
  await install('0x0000000000000000000000000000000000000806', pxCode);
  mark = await view(h, ART.reader.iface, reader, 'mark', [0]);
  assert.equal(mark[0], 12345n);
  assert.equal(mark[1], h.now());

  const zeroCode = concat([ART.px.bytecode, ART.px.iface.encodeDeploy([0n])]);
  await install('0x0000000000000000000000000000000000000806', zeroCode);
  mark = await view(h, ART.reader.iface, reader, 'mark', [0]);
  assert.equal(mark[0], 0n, 'a zero precompile price is unreadable');
  assert.equal(mark[1], 0n);

  const posCode = concat([ART.pos.bytecode, ART.pos.iface.encodeDeploy([-25n])]);
  await install('0x0000000000000000000000000000000000000800', posCode);
  pos = await view(h, ART.reader.iface, reader, 'positionSzi', [alice, 0]);
  assert.equal(pos[0], -25n);
  assert.equal(pos[1], true);

  // Base Sepolia has no HyperCore precompiles. The reader still deploys,
  // and a missing precompile stays unreadable.
  const vm2 = await newVm(84532n);
  const h2 = harness(vm2, ART.reader.iface);
  const reader2 = await h2.deploy(alice, ART.reader.bytecode);
  const bare = await view(h2, ART.reader.iface, reader2, 'oraclePx', [1]);
  assert.equal(bare[0], 0n);
  assert.equal(bare[1], 0n);
});

function expected(kind, side, size, basis, observed, deductible, cap) {
  let hi;
  let lo;
  if (kind === 1) {
    if (side === 1) { hi = observed; lo = basis; } else { hi = basis; lo = observed; }
  } else if (side === 1) { hi = basis; lo = observed; } else { hi = observed; lo = basis; }
  if (hi <= lo || hi - lo <= deductible) return 0n;
  const raw = size * (hi - lo - deductible) / SCALE;
  return raw > cap ? cap : raw;
}

test('quotePayout is the spec formula, capped, and a zero gap is zero', async () => {
  const vm = await newVm(998n);
  const h = harness(vm, ART.escrow.iface);
  const escrow = await h.deploy(alice, escrowDeployData(alice, alice, bob));
  const cases = [
    [1, 1, SCALE, 100n * E6, 110n * E6, 0n, 1000n * E6],
    [1, 1, SCALE, 100n * E6, 110n * E6, 4n * E6, 1000n * E6],
    [1, 2, SCALE, 110n * E6, 100n * E6, 1n * E6, 1000n * E6],
    [2, 1, 2n * SCALE, 100n * E6, 96n * E6, 1n * E6, 1000n * E6],
    [2, 2, SCALE, 100n * E6, 104n * E6, 0n, 1000n * E6],
    [1, 1, 1000n * SCALE, 100n * E6, 150n * E6, 0n, 10n * E6],
    [1, 1, SCALE, 100n * E6, 100n * E6, 0n, 1000n * E6],
    [2, 1, SCALE, 100n * E6, 100n * E6, 0n, 1000n * E6],
  ];
  for (const row of cases) {
    const got = (await view(h, ART.escrow.iface, escrow, 'quotePayout', row))[0];
    assert.equal(got, expected(...row), `quote ${row.join(',')}`);
  }
  const bad = await h.send(alice, escrow, ART.escrow.iface.encodeFunctionData(
    'quotePayout', [9, 1, SCALE, 1n, 2n, 0n, 10n],
  ));
  expectRevert(bad, ART.escrow.iface, 'BadTerms');
});

async function world(tokenArt = ART.token, chainId = 998n) {
  const vm = await newVm(chainId);
  const h = harness(vm, ART.escrow.iface);
  const token = await h.deploy(alice, tokenArt.bytecode);
  const readings = await h.deploy(alice, ART.readings.bytecode);
  const escrow = await h.deploy(alice, escrowDeployData(token, readings, bob, [0, 1]));
  const mint = (to, n) => h.send(alice, token, tokenArt.iface.encodeFunctionData('mint', [to, n]));
  const approve = (from, n) => h.send(from, token, tokenArt.iface.encodeFunctionData('approve', [escrow, n]));
  await mint(alice, 1_000_000n * E6);
  await mint(bob, 1_000_000n * E6);
  await approve(alice, (1n << 256n) - 1n);
  await approve(bob, (1n << 256n) - 1n);
  const C = {};
  for (const name of ['ACTIVATION_COOLDOWN', 'MIN_HOLD', 'CHALLENGE_WINDOW', 'MAX_STALENESS',
    'ST_PENDING', 'ST_ACTIVE', 'ST_CLAIMED', 'ST_PAID', 'ST_VOID', 'ST_SETTLED', 'SIZE_SCALE']) {
    C[name] = (await view(h, ART.escrow.iface, escrow, name))[0];
  }
  assert.equal(C.SIZE_SCALE, SCALE);
  async function bal(who) {
    return (await view(h, tokenArt.iface, token, 'balanceOf', [who]))[0];
  }
  async function policy(id) {
    return view(h, ART.escrow.iface, escrow, 'policies', [id]);
  }
  function setMark(px, at) {
    return h.send(alice, readings, ART.readings.iface.encodeFunctionData('setMark', [px, at]));
  }
  function setOracle(px, at) {
    return h.send(alice, readings, ART.readings.iface.encodeFunctionData('setOracle', [px, at]));
  }
  function setPos(szi, readable) {
    return h.send(alice, readings, ART.readings.iface.encodeFunctionData('setPos', [szi, readable]));
  }
  return { h, vm, token, readings, escrow, bal, policy, setMark, setOracle, setPos, C, tokenArt };
}

const ADL = 1;
const STOP = 2;
const LONG = 1;
const SHORT = 2;

function openData(over = {}) {
  const o = {
    kind: ADL, side: LONG, asset: 0, size: SCALE, covered: 100n, trigger: 0n,
    deductible: 0n, cap: 1000n * E6, delta: 60n, expiry: 1_900_000_000n, premium: 50n * E6,
    ...over,
  };
  return [o.kind, o.side, o.asset, o.size, o.covered, o.trigger, o.deductible, o.cap, o.delta, o.expiry, o.premium];
}

async function openFund(w, over = {}) {
  const args = openData(over);
  const opened = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('open', args));
  assert.ok(!opened.res.exceptionError, namedRevert(opened.res, ART.escrow.iface));
  const id = ART.escrow.iface.decodeFunctionResult('open', bytesToHex(opened.res.returnValue))[0];
  const cap = args[7];
  const funded = await w.h.send(bob, w.escrow, ART.escrow.iface.encodeFunctionData('postCollateral', [id, cap]));
  assert.ok(!funded.res.exceptionError, namedRevert(funded.res, ART.escrow.iface));
  return { id, args };
}

test('an asset off the allowlist cannot be opened, and an unfunded policy cannot be claimed', async () => {
  const w = await world();
  const hip3 = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('open', openData({ asset: 50 })));
  expectRevert(hip3, ART.escrow.iface, 'AssetNotAllowed');
  const opened = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('open', openData()));
  assert.ok(!opened.res.exceptionError, namedRevert(opened.res, ART.escrow.iface));
  const id = ART.escrow.iface.decodeFunctionResult('open', bytesToHex(opened.res.returnValue))[0];
  assert.equal((await w.policy(id)).state, w.C.ST_PENDING);
  const early = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [id, 1n, w.h.now()]));
  expectRevert(early, ART.escrow.iface, 'BadState');
  const short = await w.h.send(bob, w.escrow, ART.escrow.iface.encodeFunctionData('postCollateral', [id, 1n]));
  expectRevert(short, ART.escrow.iface, 'BelowCap');
  const stranger = await w.h.send(carol, w.escrow, ART.escrow.iface.encodeFunctionData('postCollateral', [id, 1000n * E6]));
  expectRevert(stranger, ART.escrow.iface, 'NotUnderwriter');
  const funded = await w.h.send(bob, w.escrow, ART.escrow.iface.encodeFunctionData('postCollateral', [id, 1000n * E6]));
  assert.ok(!funded.res.exceptionError, namedRevert(funded.res, ART.escrow.iface));
  const twice = await w.h.send(bob, w.escrow, ART.escrow.iface.encodeFunctionData('postCollateral', [id, 1000n * E6]));
  expectRevert(twice, ART.escrow.iface, 'BadState');
});

test('cooldown and minimum hold both refuse a claim, including one filed for a time before the cover was active', async () => {
  const w = await world();
  const t0 = w.h.now();
  const { id } = await openFund(w);
  const activeAt = t0 + w.C.ACTIVATION_COOLDOWN;
  w.h.at(activeAt - 1n);
  let claim = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [id, 100n * E6, activeAt]));
  expectRevert(claim, ART.escrow.iface, 'Cooldown');

  const first = activeAt + w.C.MIN_HOLD;
  w.h.at(first - 1n);
  const trigger = (first - 1n) - 60n;
  claim = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [id, 100n * E6, trigger]));
  expectRevert(claim, ART.escrow.iface, 'MinHold');

  w.h.at(first);
  claim = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [id, 100n * E6, activeAt - 1n]));
  expectRevert(claim, ART.escrow.iface, 'Window');
  assert.equal((await w.policy(id)).state, w.C.ST_ACTIVE);
});

async function readyClaim(w, over) {
  const t0 = w.h.now();
  const { id, args } = await openFund(w, over);
  const activeAt = t0 + w.C.ACTIVATION_COOLDOWN;
  const when = activeAt + w.C.MIN_HOLD;
  const delta = args[8];
  const trigger = when - delta;
  w.h.at(when);
  return { id, args, when, trigger, activeAt };
}

test('ADL long and short pay the formula, once, and a second claim or settlement reverts', async () => {
  const w = await world();
  const beforeAlice = await w.bal(alice);
  const beforeBob = await w.bal(bob);
  const { id, args, when, trigger } = await readyClaim(w, {
    side: LONG, size: SCALE, deductible: 1n * E6, cap: 1000n * E6, premium: 50n * E6,
  });
  const adlPx = 100n * E6;
  const mark = 110n * E6;
  await w.setMark(mark, when);
  await w.setPos(40n, true);
  const pay = expected(ADL, LONG, args[3], adlPx, mark, args[6], args[7]);
  assert.ok(pay > 0n && pay < args[7]);
  const filed = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [id, adlPx, trigger]));
  assert.ok(!filed.res.exceptionError, namedRevert(filed.res, ART.escrow.iface));
  assert.equal((await w.policy(id)).payout, pay);
  assert.equal((await w.policy(id)).state, w.C.ST_CLAIMED);
  const again = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [id, adlPx, trigger]));
  expectRevert(again, ART.escrow.iface, 'BadState');
  const tooSoon = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('settle', [id]));
  expectRevert(tooSoon, ART.escrow.iface, 'Window');
  w.h.at(when + w.C.CHALLENGE_WINDOW);
  const settled = await w.h.send(carol, w.escrow, ART.escrow.iface.encodeFunctionData('settle', [id]));
  assert.ok(!settled.res.exceptionError, namedRevert(settled.res, ART.escrow.iface));
  const twice = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('settle', [id]));
  expectRevert(twice, ART.escrow.iface, 'BadState');
  const rest = args[10] + args[7] - pay;
  assert.equal(await w.bal(alice), beforeAlice - args[10] + pay);
  assert.equal(await w.bal(bob), beforeBob - args[7] + rest);
  assert.equal(await w.bal(w.escrow), 0n);
  assert.equal((await w.policy(id)).state, w.C.ST_PAID);

  // Short, same escrow, fresh clock far enough that expiry still holds.
  w.h.at(1_800_000_000n);
  const short = await readyClaim(w, { side: SHORT, size: SCALE, deductible: 1n * E6 });
  const sAdl = 110n * E6;
  const sMark = 100n * E6;
  await w.setMark(sMark, short.when);
  await w.setPos(-10n, true);
  const sPay = expected(ADL, SHORT, SCALE, sAdl, sMark, 1n * E6, 1000n * E6);
  const sFiled = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [short.id, sAdl, short.trigger]));
  assert.ok(!sFiled.res.exceptionError, namedRevert(sFiled.res, ART.escrow.iface));
  assert.equal((await w.policy(short.id)).payout, sPay);
});

test('a zero gap does not pay, a stale or zero oracle does not pay, and neither consumes the policy', async () => {
  const w = await world();
  const flat = await readyClaim(w);
  await w.setMark(100n * E6, flat.when);
  await w.setPos(1n, true);
  const nothing = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [flat.id, 100n * E6, flat.trigger]));
  expectRevert(nothing, ART.escrow.iface, 'NothingToPay');
  assert.equal((await w.policy(flat.id)).state, w.C.ST_ACTIVE);

  const stale = await readyClaim(w);
  await w.setMark(110n * E6, stale.when - w.C.MAX_STALENESS - 1n);
  await w.setPos(1n, true);
  const old = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [stale.id, 100n * E6, stale.trigger]));
  expectRevert(old, ART.escrow.iface, 'Stale');
  assert.equal((await w.policy(stale.id)).state, w.C.ST_ACTIVE);
  await w.setMark(110n * E6, stale.when);
  const filed = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [stale.id, 100n * E6, stale.trigger]));
  assert.ok(!filed.res.exceptionError, namedRevert(filed.res, ART.escrow.iface));

  const future = await readyClaim(w);
  await w.setMark(110n * E6, future.when + 1n);
  await w.setPos(1n, true);
  const ahead = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [future.id, 100n * E6, future.trigger]));
  expectRevert(ahead, ART.escrow.iface, 'Unreadable');
  assert.equal((await w.policy(future.id)).state, w.C.ST_ACTIVE);

  const zero = await readyClaim(w, { side: SHORT });
  await w.setMark(0n, zero.when);
  await w.setPos(-1n, true);
  const unread = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [zero.id, 100n * E6, zero.trigger]));
  expectRevert(unread, ART.escrow.iface, 'Unreadable');
  assert.equal((await w.policy(zero.id)).state, w.C.ST_ACTIVE);
  assert.equal((await w.policy(zero.id)).payout, 0n);
});

test('an unreadable position is not a flat book, an intact position is not a claim, and int64 minimum is unreadable', async () => {
  const w = await world();
  const unread = await readyClaim(w);
  await w.setMark(110n * E6, unread.when);
  await w.setPos(0n, false);
  const a = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [unread.id, 100n * E6, unread.trigger]));
  expectRevert(a, ART.escrow.iface, 'Unreadable');
  assert.equal((await w.policy(unread.id)).state, w.C.ST_ACTIVE);

  const intact = await readyClaim(w, { covered: 100n });
  await w.setMark(110n * E6, intact.when);
  await w.setPos(100n, true);
  const b = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [intact.id, 100n * E6, intact.trigger]));
  expectRevert(b, ART.escrow.iface, 'PositionIntact');

  const min = await readyClaim(w);
  await w.setMark(110n * E6, min.when);
  await w.setPos(-(2n ** 63n), true);
  const c = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [min.id, 100n * E6, min.trigger]));
  expectRevert(c, ART.escrow.iface, 'Unreadable');
  assert.equal((await w.policy(min.id)).state, w.C.ST_ACTIVE);
});

test('a challenge voids an ADL claim whose position was not reduced, and does not void one that still is', async () => {
  const w = await world();
  const bad = await readyClaim(w, { premium: 40n * E6, cap: 80n * E6 });
  const beforeBob = await w.bal(bob);
  const beforeAlice = await w.bal(alice);
  await w.setMark(110n * E6, bad.when);
  await w.setPos(10n, true);
  const filed = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [bad.id, 100n * E6, bad.trigger]));
  assert.ok(!filed.res.exceptionError, namedRevert(filed.res, ART.escrow.iface));
  await w.setPos(100n, true);
  const challenged = await w.h.send(carol, w.escrow, ART.escrow.iface.encodeFunctionData('challenge', [bad.id]));
  assert.ok(!challenged.res.exceptionError, namedRevert(challenged.res, ART.escrow.iface));
  assert.equal((await w.policy(bad.id)).state, w.C.ST_VOID);
  w.h.at(bad.when + w.C.CHALLENGE_WINDOW);
  const noSettle = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('settle', [bad.id]));
  expectRevert(noSettle, ART.escrow.iface, 'BadState');
  const back = await w.h.send(bob, w.escrow, ART.escrow.iface.encodeFunctionData('reclaim', [bad.id]));
  assert.ok(!back.res.exceptionError, namedRevert(back.res, ART.escrow.iface));
  assert.equal(await w.bal(alice), beforeAlice);
  assert.equal(await w.bal(bob), beforeBob + bad.args[7] + bad.args[10]);

  const good = await readyClaim(w);
  await w.setMark(110n * E6, good.when);
  await w.setPos(10n, true);
  const filed2 = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [good.id, 100n * E6, good.trigger]));
  assert.ok(!filed2.res.exceptionError, namedRevert(filed2.res, ART.escrow.iface));
  const held = await w.h.send(bob, w.escrow, ART.escrow.iface.encodeFunctionData('challenge', [good.id]));
  expectRevert(held, ART.escrow.iface, 'ChallengeFailed');
  assert.equal((await w.policy(good.id)).state, w.C.ST_CLAIMED);
  await w.setPos(0n, false);
  const unread = await w.h.send(bob, w.escrow, ART.escrow.iface.encodeFunctionData('challenge', [good.id]));
  expectRevert(unread, ART.escrow.iface, 'ChallengeFailed');
  assert.equal((await w.policy(good.id)).state, w.C.ST_CLAIMED);
  w.h.at(good.when + w.C.CHALLENGE_WINDOW);
  const settled = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('settle', [good.id]));
  assert.ok(!settled.res.exceptionError, namedRevert(settled.res, ART.escrow.iface));
});

test('stop-gap pays from the oracle and the stored trigger, and a fill figure is refused', async () => {
  const w = await world();
  const row = await readyClaim(w, {
    kind: STOP, side: LONG, covered: 0n, trigger: 100n * E6, deductible: 1n * E6, size: 2n * SCALE,
  });
  const oracle = 96n * E6;
  await w.setOracle(oracle, row.when);
  const sneaky = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [row.id, 1n, row.trigger]));
  expectRevert(sneaky, ART.escrow.iface, 'FillNotUsed');
  assert.equal((await w.policy(row.id)).state, w.C.ST_ACTIVE);
  const filed = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [row.id, 0n, row.trigger]));
  assert.ok(!filed.res.exceptionError, namedRevert(filed.res, ART.escrow.iface));
  const pay = expected(STOP, LONG, 2n * SCALE, 100n * E6, oracle, 1n * E6, 1000n * E6);
  assert.equal((await w.policy(row.id)).payout, pay);
  const challenged = await w.h.send(bob, w.escrow, ART.escrow.iface.encodeFunctionData('challenge', [row.id]));
  expectRevert(challenged, ART.escrow.iface, 'ChallengeFailed');
  assert.equal((await w.policy(row.id)).state, w.C.ST_CLAIMED);

  const cap = await readyClaim(w, {
    kind: STOP, side: SHORT, covered: 0n, trigger: 100n * E6, size: 1000n * SCALE, cap: 10n * E6,
  });
  await w.setOracle(150n * E6, cap.when);
  const capped = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [cap.id, 0n, cap.trigger]));
  assert.ok(!capped.res.exceptionError, namedRevert(capped.res, ART.escrow.iface));
  assert.equal((await w.policy(cap.id)).payout, 10n * E6);
});

test('one policy cannot be paid out of another policy, and a stranger cannot file', async () => {
  const w = await world();
  w.h.at(1_800_000_000n);
  const a0 = await w.bal(alice);
  const b0 = await w.bal(bob);
  const first = await readyClaim(w, { premium: 50n * E6, cap: 100n * E6, size: SCALE });
  // readyClaim moved the clock. Open the second at a time that still fits expiry.
  w.h.at(first.when);
  const second = await openFund(w, { premium: 70n * E6, cap: 200n * E6, asset: 1 });
  await w.setMark(110n * E6, first.when);
  await w.setPos(1n, true);
  const filed = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [first.id, 100n * E6, first.trigger]));
  assert.ok(!filed.res.exceptionError, namedRevert(filed.res, ART.escrow.iface));
  const stranger = await w.h.send(carol, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [second.id, 100n * E6, first.trigger]));
  expectRevert(stranger, ART.escrow.iface, 'NotBuyer');
  w.h.at(first.when + w.C.CHALLENGE_WINDOW);
  const settled = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('settle', [first.id]));
  assert.ok(!settled.res.exceptionError, namedRevert(settled.res, ART.escrow.iface));
  const pay = (await w.policy(first.id)).payout;
  assert.equal(pay, 10n * E6);
  assert.equal((await w.policy(second.id)).state, w.C.ST_ACTIVE);
  assert.equal((await w.policy(second.id)).collateral, 200n * E6);
  assert.equal(await w.bal(w.escrow), 70n * E6 + 200n * E6);
  assert.equal(await w.bal(alice), a0 - 50n * E6 - 70n * E6 + pay);
  assert.equal(await w.bal(bob), b0 - 100n * E6 - 200n * E6 + (50n * E6 + 100n * E6 - pay));
});

test('cancel returns the premium, expiry returns the funded cover to the underwriter, and native coin is refused', async () => {
  const w = await world();
  const before = await w.bal(alice);
  const opened = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('open', openData({ premium: 25n * E6 })));
  const id = ART.escrow.iface.decodeFunctionResult('open', bytesToHex(opened.res.returnValue))[0];
  assert.equal(await w.bal(alice), before - 25n * E6);
  const cancelled = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('cancel', [id]));
  assert.ok(!cancelled.res.exceptionError, namedRevert(cancelled.res, ART.escrow.iface));
  assert.equal(await w.bal(alice), before);
  const again = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('cancel', [id]));
  expectRevert(again, ART.escrow.iface, 'BadState');

  const t0 = w.h.now();
  const bob0 = await w.bal(bob);
  const alice0 = await w.bal(alice);
  const { id: live, args } = await openFund(w, { premium: 30n * E6, cap: 40n * E6 });
  const tooEarly = await w.h.send(bob, w.escrow, ART.escrow.iface.encodeFunctionData('reclaim', [live]));
  expectRevert(tooEarly, ART.escrow.iface, 'BadState');
  w.h.at(args[9]);
  const back = await w.h.send(bob, w.escrow, ART.escrow.iface.encodeFunctionData('reclaim', [live]));
  assert.ok(!back.res.exceptionError, namedRevert(back.res, ART.escrow.iface));
  assert.equal(await w.bal(bob), bob0 + args[10]);
  assert.equal(await w.bal(alice), alice0 - args[10]);
  assert.equal((await w.policy(live)).state, w.C.ST_SETTLED);
  assert.ok(t0 < args[9]);

  const native = await w.h.send(alice, w.escrow, '0x', 1n);
  expectRevert(native, ART.escrow.iface, 'NativeRejected');
});

test('a fee-on-transfer token is refused, and a token that returns no bool still pays once', async () => {
  const fee = await world(ART.fee);
  const before = await fee.bal(alice);
  const opened = await fee.h.send(alice, fee.escrow, ART.escrow.iface.encodeFunctionData('open', openData()));
  expectRevert(opened, ART.escrow.iface, 'TransferFailed');
  assert.equal(await fee.bal(alice), before);

  const w = await world(ART.silent);
  const { id, when, trigger, args } = await readyClaim(w);
  await w.setMark(110n * E6, when);
  await w.setPos(1n, true);
  const alice0 = await w.bal(alice);
  const filed = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [id, 100n * E6, trigger]));
  assert.ok(!filed.res.exceptionError, namedRevert(filed.res, ART.escrow.iface));
  w.h.at(when + w.C.CHALLENGE_WINDOW);
  const settled = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('settle', [id]));
  assert.ok(!settled.res.exceptionError, namedRevert(settled.res, ART.escrow.iface));
  const pay = (await w.policy(id)).payout;
  assert.equal(await w.bal(alice), alice0 + pay);
  assert.equal(pay, expected(ADL, LONG, args[3], 100n * E6, 110n * E6, 0n, args[7]));
});

test('a token that calls back cannot open a second policy or settle a second time', async () => {
  const w = await world(ART.reenter);
  await w.h.send(alice, w.token, ART.reenter.iface.encodeFunctionData('setEscrow', [w.escrow]));
  await w.h.send(alice, w.token, ART.reenter.iface.encodeFunctionData('mint', [w.token, 1000n]));
  const t0 = w.h.now();
  const opened = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('open', openData()));
  assert.ok(!opened.res.exceptionError, namedRevert(opened.res, ART.escrow.iface));
  const id = ART.escrow.iface.decodeFunctionResult('open', bytesToHex(opened.res.returnValue))[0];
  const bob0 = await w.bal(bob);
  await w.h.send(alice, w.token, ART.reenter.iface.encodeFunctionData('arm', [true]));
  const funded = await w.h.send(bob, w.escrow, ART.escrow.iface.encodeFunctionData('postCollateral', [id, 1000n * E6]));
  assert.ok(!funded.res.exceptionError, namedRevert(funded.res, ART.escrow.iface));
  assert.equal((await view(w.h, ART.reenter.iface, w.token, 'innerOk'))[0], false);
  const ret = String((await view(w.h, ART.reenter.iface, w.token, 'innerRet'))[0]).toLowerCase();
  assert.ok(ret.startsWith(REENTERED), ret);
  assert.equal((await view(w.h, ART.escrow.iface, w.escrow, 'nextId'))[0], 2n);
  assert.equal(await w.bal(bob), bob0 - 1000n * E6);
  assert.equal(await w.bal(w.token), 1000n);
  assert.equal((await w.policy(id)).state, w.C.ST_ACTIVE);
  assert.ok(t0 > 0n);

  const s = await world(ART.settleTok);
  await s.h.send(alice, s.token, ART.settleTok.iface.encodeFunctionData('setEscrow', [s.escrow]));
  const row = await readyClaim(s);
  await s.setMark(110n * E6, row.when);
  await s.setPos(1n, true);
  const filed = await s.h.send(alice, s.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [row.id, 100n * E6, row.trigger]));
  assert.ok(!filed.res.exceptionError, namedRevert(filed.res, ART.escrow.iface));
  const pay = (await s.policy(row.id)).payout;
  const alice0 = await s.bal(alice);
  await s.h.send(alice, s.token, ART.settleTok.iface.encodeFunctionData('arm', [row.id]));
  s.h.at(row.when + s.C.CHALLENGE_WINDOW);
  const settled = await s.h.send(alice, s.escrow, ART.escrow.iface.encodeFunctionData('settle', [row.id]));
  assert.ok(!settled.res.exceptionError, namedRevert(settled.res, ART.escrow.iface));
  assert.equal((await view(s.h, ART.settleTok.iface, s.token, 'innerOk'))[0], false);
  const sret = String((await view(s.h, ART.settleTok.iface, s.token, 'innerRet'))[0]).toLowerCase();
  assert.ok(sret.startsWith(REENTERED), sret);
  assert.equal(await s.bal(alice), alice0 + pay);
  assert.equal((await s.policy(row.id)).state, s.C.ST_PAID);
});

test('the observation window refuses a claim that is early or late even when the oracle timestamp is fresh', async () => {
  const w = await world();
  const row = await readyClaim(w);
  const earlyTrigger = row.when - 60n + 10n;
  await w.setMark(110n * E6, row.when);
  await w.setPos(1n, true);
  const early = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [row.id, 100n * E6, earlyTrigger]));
  expectRevert(early, ART.escrow.iface, 'Window');

  const lateTrigger = row.activeAt + w.C.MIN_HOLD;
  const late = lateTrigger + 60n + w.C.MAX_STALENESS + 1n;
  w.h.at(late);
  await w.setMark(110n * E6, late);
  const missed = await w.h.send(alice, w.escrow, ART.escrow.iface.encodeFunctionData('fileClaim', [row.id, 100n * E6, lateTrigger]));
  expectRevert(missed, ART.escrow.iface, 'Window');
  assert.equal((await w.policy(row.id)).state, w.C.ST_ACTIVE);
});
