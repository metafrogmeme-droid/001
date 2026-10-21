/**
 * Memeclaw — compile the real contracts, run the real bytecode on an
 * in-process EVM. No network, no mocks of the thing under test. The
 * promises under test:
 *   testnet only (the constructor refuses every other chain),
 *   a fixed supply held by the curve at launch, no mint after it,
 *   the curve pays every sale from DOGE it received (random trading, then
 *   every holder sells out), slippage, deadline and sold-out refusals,
 *   no re-entry through a seller's payout, no owner and no fee at all,
 *   and the committed artifact is the built one.
 */
import test from 'node:test';
import assert from 'node:assert';
import { readFileSync } from 'node:fs';
import { VM } from '@ethereumjs/vm';
import { Common, Hardfork } from '@ethereumjs/common';
import { Account, Address, hexToBytes, bytesToHex } from '@ethereumjs/util';
import { Interface, getBytes, concat, ZeroHash, id as keccakId } from 'ethers';
import solc from 'solc';
import { build, serialize, ARTIFACT } from '../scripts/build.mjs';

const ARTIFACT_OBJ = build();
const F = ARTIFACT_OBJ.contracts.MemeclawFactory;
const T = ARTIFACT_OBJ.contracts.MemeclawToken;
const factoryIface = new Interface(F.abi);
const tokenIface = new Interface(T.abi);

const DOGEOS_TESTNET = 6281971;
const GAS = 30_000_000n;
const E18 = 10n ** 18n;
const SUPPLY = 1_000_000_000n * E18;
const VIRTUAL_DOGE = 10n * E18;
const VIRTUAL_TOKENS = 250_000_000n * E18;
const FAR = 2n ** 40n; // a deadline far in the future

const alice = '0x' + 'a1'.repeat(20);
const bob = '0x' + 'b0'.repeat(20);
const carol = '0x' + 'c0'.repeat(20);

async function newVm(chainId = DOGEOS_TESTNET) {
  const common = Common.custom({ chainId }, { hardfork: Hardfork.Shanghai });
  const vm = await VM.create({ common });
  for (const who of [alice, bob, carol]) {
    await vm.stateManager.putAccount(new Address(hexToBytes(who)),
      new Account(0n, 1_000_000n * E18));
  }
  return vm;
}

function revertReason(execResult) {
  const ret = bytesToHex(execResult.returnValue || new Uint8Array());
  if (ret.startsWith('0x08c379a0')) {
    return new Interface(['function Error(string)']).decodeFunctionData('Error', ret)[0];
  }
  return execResult.exceptionError ? String(execResult.exceptionError.error) : null;
}

async function deploy(vm, bytecode, from = alice) {
  const r = await vm.evm.runCall({
    caller: new Address(hexToBytes(from)), data: getBytes(bytecode), gasLimit: GAS,
  });
  return r;
}

/** A harness bound to one VM and one factory. */
async function setup() {
  const vm = await newVm();
  const r = await deploy(vm, F.bytecode);
  assert.ok(!r.execResult.exceptionError, 'factory deploy reverted: ' + revertReason(r.execResult));
  const factory = r.createdAddress.toString();
  let clock = 1_800_000_000n;

  async function send(from, to, data, value = 0n) {
    clock += 3n;
    const res = await vm.evm.runCall({
      caller: new Address(hexToBytes(from)),
      to: new Address(hexToBytes(to)),
      data: getBytes(data),
      gasLimit: GAS,
      value,
      block: { header: { timestamp: clock, number: clock, coinbase: Address.zero(),
        difficulty: 0n, prevRandao: new Uint8Array(32), gasLimit: GAS, baseFeePerGas: 0n,
        getBlobGasPrice: () => 0n } },
    });
    return res.execResult;
  }
  async function read(to, iface, fn, ...args) {
    const res = await send(alice, to, iface.encodeFunctionData(fn, args));
    assert.ok(!res.exceptionError, `${fn} reverted: ${revertReason(res)}`);
    return iface.decodeFunctionResult(fn, bytesToHex(res.returnValue));
  }
  async function balance(who) {
    const acct = await vm.stateManager.getAccount(new Address(hexToBytes(who)));
    return acct ? acct.balance : 0n;
  }
  function logsOf(res, iface, name) {
    const topic = iface.getEvent(name).topicHash;
    return (res.logs || [])
      .filter((l) => bytesToHex(l[1][0]) === topic)
      .map((l) => ({ address: bytesToHex(l[0]), ...iface.parseLog({
        topics: l[1].map((t) => bytesToHex(t)), data: bytesToHex(l[2]) }) }));
  }
  async function launch(from, name, symbol, value = 0n, { terms = keccakId('terms:' + name), minOut = 0n, deadline = FAR } = {}) {
    const res = await send(from, factory,
      factoryIface.encodeFunctionData('launch', [name, symbol, terms, minOut, deadline]), value);
    const ev = res.exceptionError ? [] : logsOf(res, factoryIface, 'Launched');
    return { res, token: ev.length ? ev[0].args.token.toLowerCase() : null, ev };
  }
  const buy = (from, token, value, minOut = 0n, deadline = FAR) =>
    send(from, factory, factoryIface.encodeFunctionData('buy', [token, minOut, deadline]), value);
  const approve = (from, token, amount) =>
    send(from, token, tokenIface.encodeFunctionData('approve', [factory, amount]));
  const sell = (from, token, amount, minOut = 0n, deadline = FAR) =>
    send(from, factory, factoryIface.encodeFunctionData('sell', [token, amount, minOut, deadline]));
  const curve = async (token) => (await read(factory, factoryIface, 'curves', token));
  const tokenBalance = async (token, who) => (await read(token, tokenIface, 'balanceOf', who))[0];
  return { vm, factory, send, read, balance, logsOf, launch, buy, approve, sell, curve, tokenBalance };
}

// The curve, written out once more in BigInt so the test can say what the
// contract must answer without asking the contract.
function quoteBuy(dogeReserve, tokenReserve, dogeIn) {
  const x = VIRTUAL_DOGE + dogeReserve;
  const y = VIRTUAL_TOKENS + tokenReserve;
  return dogeIn * y / (x + dogeIn);
}
function quoteSell(dogeReserve, tokenReserve, tokenIn) {
  const x = VIRTUAL_DOGE + dogeReserve;
  const y = VIRTUAL_TOKENS + tokenReserve;
  return tokenIn * x / (y + tokenIn);
}

// ── the artifact ─────────────────────────────────────────────────────────────

test('the committed artifact is the built artifact', () => {
  assert.equal(readFileSync(ARTIFACT, 'utf8'), serialize(ARTIFACT_OBJ),
    'compiled/MemeclawFactory.json is stale: run npm run build in contracts/memeclaw');
  assert.equal(ARTIFACT_OBJ.compiler, solc.version());
  assert.equal(ARTIFACT_OBJ.settings.evmVersion, 'paris');
});

test('the factory carries no immutables, so its code on chain is the artifact byte for byte', () => {
  // An immutable would be written into the runtime code at deploy and make
  // the deployed code differ from deployedBytecode; the website's check of
  // a factory address compares the two exactly.
  const ctor = F.abi.find((x) => x.type === 'constructor');
  assert.ok(!ctor || ctor.inputs.length === 0, 'the factory constructor takes no arguments');
});

// ── testnet only ─────────────────────────────────────────────────────────────

test('testnet only: the factory deploys on the DogeOS testnet and nowhere else', async () => {
  for (const chainId of [1, 8453, 2000, 56]) {
    const vm = await newVm(chainId);
    const r = await deploy(vm, F.bytecode);
    assert.equal(revertReason(r.execResult), 'testnet only', `chain ${chainId} must be refused`);
  }
  const h = await setup();
  assert.equal((await h.read(h.factory, factoryIface, 'CHAIN_ID'))[0], BigInt(DOGEOS_TESTNET));
});

// ── launch ───────────────────────────────────────────────────────────────────

test('a launch creates a fixed-supply coin held whole by the curve, and says which terms', async () => {
  const h = await setup();
  const terms = keccakId('the sealed terms');
  const { res, token, ev } = await h.launch(alice, 'Much Claw', 'CLAW', 0n, { terms });
  assert.ok(!res.exceptionError, 'launch reverted: ' + revertReason(res));
  assert.equal(ev.length, 1);
  assert.equal(ev[0].address.toLowerCase(), h.factory.toLowerCase(), 'the factory emits Launched');
  assert.equal(ev[0].args.creator.toLowerCase(), alice);
  assert.equal(ev[0].args.termsHash, terms);
  assert.equal(ev[0].args.name, 'Much Claw');
  assert.equal(ev[0].args.symbol, 'CLAW');

  assert.equal((await h.read(token, tokenIface, 'totalSupply'))[0], SUPPLY);
  assert.equal((await h.read(token, tokenIface, 'decimals'))[0], 18n);
  assert.equal((await h.read(token, tokenIface, 'name'))[0], 'Much Claw');
  assert.equal((await h.read(token, tokenIface, 'factory'))[0].toLowerCase(), h.factory.toLowerCase());
  assert.equal(await h.tokenBalance(token, h.factory), SUPPLY, 'the curve holds the whole supply');
  assert.equal(await h.tokenBalance(token, alice), 0n, 'a launch with no value gives the creator nothing');
  const c = await h.curve(token);
  assert.equal(c.dogeReserve, 0n);
  assert.equal(c.tokenReserve, SUPPLY);
  assert.equal(c.creator.toLowerCase(), alice);
  assert.equal((await h.read(h.factory, factoryIface, 'launchCount'))[0], 1n);
  assert.equal((await h.read(h.factory, factoryIface, 'launches', 0))[0].toLowerCase(), token);
});

test('a launch with value buys for the creator in the same transaction, before anyone', async () => {
  const h = await setup();
  const { res, token } = await h.launch(bob, 'First Buyer', 'FIRST', 1n * E18);
  assert.ok(!res.exceptionError, revertReason(res));
  const expect = quoteBuy(0n, SUPPLY, 1n * E18);
  assert.equal(await h.tokenBalance(token, bob), expect);
  const trades = h.logsOf(res, factoryIface, 'Trade');
  assert.equal(trades.length, 1);
  assert.equal(trades[0].args.trader.toLowerCase(), bob);
  assert.equal(trades[0].args.isBuy, true);
  assert.equal(trades[0].args.tokenAmount, expect);
});

test('a launch refuses empty terms, bad names and bad symbols', async () => {
  const h = await setup();
  const cases = [
    [{ name: 'Fine', symbol: 'FINE', terms: ZeroHash }, 'terms required'],
    [{ name: '', symbol: 'X' }, 'name length'],
    [{ name: 'x'.repeat(33), symbol: 'X' }, 'name length'],
    [{ name: 'Tab\there', symbol: 'X' }, 'name characters'],
    [{ name: 'Dоge', symbol: 'X' }, 'name characters'],          // a Cyrillic о
    [{ name: 'zero​width', symbol: 'X' }, 'name characters'],
    [{ name: ' lead', symbol: 'X' }, 'name spaces'],
    [{ name: 'trail ', symbol: 'X' }, 'name spaces'],
    [{ name: 'Fine', symbol: '' }, 'symbol length'],
    [{ name: 'Fine', symbol: 'ABCDEFGHIJK' }, 'symbol length'],
    [{ name: 'Fine', symbol: 'claw' }, 'symbol characters'],
    [{ name: 'Fine', symbol: 'CL-AW' }, 'symbol characters'],
  ];
  for (const [c, reason] of cases) {
    const { res } = await h.launch(alice, c.name, c.symbol, 0n, c.terms ? { terms: c.terms } : {});
    assert.equal(revertReason(res), reason, JSON.stringify(c));
  }
  // The other arm: the edges that are allowed.
  for (const [name, symbol] of [['x'.repeat(32), 'ABCDEFGHIJ'], ['A b-c_d!~', 'Z9']]) {
    const { res } = await h.launch(alice, name, symbol);
    assert.ok(!res.exceptionError, `${name}/${symbol}: ${revertReason(res)}`);
  }
});

// ── trading ──────────────────────────────────────────────────────────────────

test('buy and sell pay exactly the curve, and the reserves move with them', async () => {
  const h = await setup();
  const { token } = await h.launch(alice, 'Curve', 'CURVE');
  let dr = 0n; let tr = SUPPLY;

  const b1 = 2n * E18;
  const out1 = quoteBuy(dr, tr, b1);
  let res = await h.buy(bob, token, b1);
  assert.ok(!res.exceptionError, revertReason(res));
  dr += b1; tr -= out1;
  assert.equal(await h.tokenBalance(token, bob), out1);
  // Opening price check: 2 DOGE buys a little under 2/10 of 1.25B coins' worth.
  assert.equal(out1, 2n * E18 * (VIRTUAL_TOKENS + SUPPLY) / (VIRTUAL_DOGE + 2n * E18));

  const sellAmt = out1 / 2n;
  const back = quoteSell(dr, tr, sellAmt);
  res = await h.approve(bob, token, sellAmt);
  assert.ok(!res.exceptionError, revertReason(res));
  const before = await h.balance(bob);
  res = await h.sell(bob, token, sellAmt);
  assert.ok(!res.exceptionError, revertReason(res));
  dr -= back; tr += sellAmt;
  assert.equal(await h.balance(bob) - before, back, 'the seller receives the quoted DOGE (gas price is zero here)');
  const c = await h.curve(token);
  assert.equal(c.dogeReserve, dr);
  assert.equal(c.tokenReserve, tr);
  assert.equal(await h.balance(h.factory), dr, 'the factory holds exactly the reserve it records');
  assert.equal(await h.tokenBalance(token, h.factory), tr);

  // The views quote what the trades pay.
  assert.equal((await h.read(h.factory, factoryIface, 'quoteBuy', token, E18))[0], quoteBuy(dr, tr, E18));
  assert.equal((await h.read(h.factory, factoryIface, 'quoteSell', token, E18))[0], quoteSell(dr, tr, E18));
});

test('the constants the page will read are the constants the curve uses', async () => {
  const h = await setup();
  for (const [fn, want] of [['SUPPLY', SUPPLY], ['VIRTUAL_DOGE', VIRTUAL_DOGE],
    ['VIRTUAL_TOKENS', VIRTUAL_TOKENS], ['MAX_NAME_BYTES', 32n], ['MAX_SYMBOL_BYTES', 10n]]) {
    assert.equal((await h.read(h.factory, factoryIface, fn))[0], want, fn);
  }
});

test('slippage, deadline, sold out, unknown coin and empty trades are refused', async () => {
  const h = await setup();
  const { token } = await h.launch(alice, 'Edges', 'EDGE');
  const want = quoteBuy(0n, SUPPLY, E18);
  assert.equal(revertReason(await h.buy(bob, token, E18, want + 1n)), 'slippage');
  assert.equal(revertReason(await h.buy(bob, token, E18, 0n, 1n)), 'expired');
  assert.equal(revertReason(await h.buy(bob, token, 0n)), 'nothing sent');
  assert.equal(revertReason(await h.buy(bob, carol, E18)), 'unknown token');
  // The other arm: the exact quote as the floor passes.
  assert.ok(!(await h.buy(bob, token, E18, want)).exceptionError);

  // More DOGE than the supply left can absorb: refused, nothing moves.
  const c = await h.curve(token);
  const tooMuch = 1000n * E18;
  assert.ok(quoteBuy(c.dogeReserve, c.tokenReserve, tooMuch) > c.tokenReserve);
  assert.equal(revertReason(await h.buy(carol, token, tooMuch)), 'sold out');
  assert.equal((await h.read(h.factory, factoryIface, 'quoteBuy', token, tooMuch))[0], 0n);

  // Sells: no allowance, slippage, deadline, nothing.
  const held = await h.tokenBalance(token, bob);
  assert.equal(revertReason(await h.sell(bob, token, held)), 'allowance');
  await h.approve(bob, token, held);
  const back = quoteSell((await h.curve(token)).dogeReserve, (await h.curve(token)).tokenReserve, held);
  assert.equal(revertReason(await h.sell(bob, token, held, back + 1n)), 'slippage');
  assert.equal(revertReason(await h.sell(bob, token, held, 0n, 1n)), 'expired');
  assert.equal(revertReason(await h.sell(bob, token, 0n)), 'nothing sold');
  assert.equal(revertReason(await h.sell(bob, carol, 1n)), 'unknown token');
  assert.ok(!(await h.sell(bob, token, held, back)).exceptionError);
});

test('the whole supply can be bought out for VIRTUAL_DOGE*SUPPLY/VIRTUAL_TOKENS, then buys stop and sells work', async () => {
  const h = await setup();
  const { token } = await h.launch(alice, 'Sellout', 'OUT');
  // The most DOGE a buy can spend and stay within the supply left.
  const max = (VIRTUAL_DOGE * SUPPLY) / VIRTUAL_TOKENS;
  assert.equal(max, 40n * E18, 'the header says 40 testnet DOGE');
  let res = await h.buy(bob, token, max);
  assert.ok(!res.exceptionError, revertReason(res));
  assert.equal((await h.curve(token)).tokenReserve, 0n);
  assert.equal(await h.tokenBalance(token, bob), SUPPLY);
  assert.equal(revertReason(await h.buy(carol, token, 1n * E18)), 'sold out');
  await h.approve(bob, token, SUPPLY);
  res = await h.sell(bob, token, SUPPLY);
  assert.ok(!res.exceptionError, revertReason(res));
  const c = await h.curve(token);
  assert.equal(c.tokenReserve, SUPPLY);
  assert.ok(c.dogeReserve >= 0n);
  assert.equal(await h.balance(h.factory), c.dogeReserve);
});

test('random trading by three wallets, then everyone sells out: every sale is paid, no coin borrows from another', async () => {
  const h = await setup();
  const a = (await h.launch(alice, 'Alpha', 'ALPHA')).token;
  const b = (await h.launch(bob, 'Beta', 'BETA')).token;
  let seed = 7n;
  const rnd = (n) => { seed = (seed * 6364136223846793005n + 1442695040888963407n) % (2n ** 64n); return seed % n; };
  const wallets = [alice, bob, carol];
  for (let i = 0; i < 120; i += 1) {
    const who = wallets[Number(rnd(3n))];
    const token = rnd(2n) === 0n ? a : b;
    if (rnd(3n) !== 0n) {
      const amount = (rnd(3000n) + 1n) * E18 / 1000n; // 0.001 to 3 DOGE
      const c = await h.curve(token);
      if (quoteBuy(c.dogeReserve, c.tokenReserve, amount) > c.tokenReserve) continue;
      const res = await h.buy(who, token, amount);
      assert.ok(!res.exceptionError, `buy ${i}: ${revertReason(res)}`);
    } else {
      const held = await h.tokenBalance(token, who);
      if (held === 0n) continue;
      const amount = held * (rnd(100n) + 1n) / 100n;
      if (amount === 0n) continue;
      await h.approve(who, token, amount);
      const res = await h.sell(who, token, amount);
      assert.ok(!res.exceptionError || revertReason(res) === 'too small', `sell ${i}: ${revertReason(res)}`);
    }
    const ca = await h.curve(a); const cb = await h.curve(b);
    assert.equal(await h.balance(h.factory), ca.dogeReserve + cb.dogeReserve, `step ${i}: the factory holds exactly both reserves`);
  }
  for (const token of [a, b]) {
    for (const who of wallets) {
      const held = await h.tokenBalance(token, who);
      if (held === 0n) continue;
      await h.approve(who, token, held);
      const res = await h.sell(who, token, held);
      assert.ok(!res.exceptionError || revertReason(res) === 'too small', `final sell: ${revertReason(res)}`);
    }
    const c = await h.curve(token);
    assert.ok(c.dogeReserve >= 0n);
    // Whatever rounding left behind stays with the coin it came from.
    assert.ok(c.dogeReserve < E18 / 1000n, `rounding dust only, got ${c.dogeReserve}`);
  }
});

test('no re-entry: a seller whose payout calls back into the factory is refused, and the sale does not happen', async () => {
  const h = await setup();
  const { token } = await h.launch(alice, 'Guard', 'GUARD');
  // A contract that buys, then sells, and on receiving the payout tries to
  // sell again from inside it.
  const src = `// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.24;
interface IF { function buy(address,uint256,uint256) external payable returns (uint256);
  function sell(address,uint256,uint256,uint256) external returns (uint256); }
interface IT { function approve(address,uint256) external returns (bool); }
contract Attacker {
  IF f; address t; uint256 n; bool armed;
  constructor(address f_, address t_) payable { f = IF(f_); t = t_; }
  function go(bool reenter) external {
    n = f.buy{value: 0.5 ether}(t, 0, type(uint256).max);
    IT(t).approve(address(f), type(uint256).max);
    armed = reenter;
    f.sell(t, n / 2, 0, type(uint256).max);
  }
  receive() external payable {
    if (armed) { armed = false; f.sell(t, n / 4, 0, type(uint256).max); }
  }
}`;
  const out = JSON.parse(solc.compile(JSON.stringify({ language: 'Solidity',
    sources: { 'A.sol': { content: src } },
    settings: { evmVersion: 'paris', outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object'] } } } })));
  const A = out.contracts['A.sol'].Attacker;
  const aIface = new Interface(A.abi);
  const r = await h.vm.evm.runCall({
    caller: new Address(hexToBytes(carol)),
    data: getBytes(concat(['0x' + A.evm.bytecode.object, aIface.encodeDeploy([h.factory, token])])),
    gasLimit: GAS, value: 2n * E18,
  });
  assert.ok(!r.execResult.exceptionError, revertReason(r.execResult));
  const attacker = r.createdAddress.toString();
  const before = await h.curve(token);
  // The re-entering sell is refused inside the payout, so the payout fails
  // and the whole sale with it.
  const res = await h.send(carol, attacker, aIface.encodeFunctionData('go', [true]));
  assert.equal(revertReason(res), 'payout');
  const after = await h.curve(token);
  assert.equal(after.dogeReserve, before.dogeReserve, 'nothing moved');
  assert.equal(after.tokenReserve, before.tokenReserve);
  // The other arm: the same contract, not re-entering, buys and sells.
  const plain = await h.send(carol, attacker, aIface.encodeFunctionData('go', [false]));
  assert.ok(!plain.exceptionError, revertReason(plain));
  assert.ok((await h.curve(token)).dogeReserve > before.dogeReserve);
});

// ── no admin surface ─────────────────────────────────────────────────────────

test('no owner, no fee, no mint, no withdraw: nothing to rug', () => {
  for (const [name, abi] of [['MemeclawFactory', F.abi], ['MemeclawToken', T.abi]]) {
    for (const f of abi.filter((x) => x.type === 'function')) {
      assert.doesNotMatch(f.name, /owner|admin|pause|withdraw|fee|mint|burn|set[A-Z]|upgrade|rescue|skim/i,
        `${name}.${f.name} looks like a control surface`);
    }
  }
  const payable = F.abi.filter((x) => x.type === 'function' && x.stateMutability === 'payable').map((f) => f.name).sort();
  assert.deepEqual(payable, ['buy', 'launch'], 'only buying takes DOGE in');
  assert.ok(!F.abi.some((x) => x.type === 'receive' || x.type === 'fallback'),
    'a plain DOGE send to the factory is refused, not kept');
});

test('a plain DOGE send to the factory is refused', async () => {
  const h = await setup();
  const res = await h.send(bob, h.factory, '0x', E18);
  assert.ok(res.exceptionError);
  assert.equal(await h.balance(h.factory), 0n);
});

test('coin transfers and allowances behave like ERC-20', async () => {
  const h = await setup();
  const { token } = await h.launch(alice, 'Plain', 'PLAIN', E18);
  const held = await h.tokenBalance(token, alice);
  let res = await h.send(alice, token, tokenIface.encodeFunctionData('transfer', [bob, held / 2n]));
  assert.ok(!res.exceptionError, revertReason(res));
  assert.equal(await h.tokenBalance(token, bob), held / 2n);
  res = await h.send(alice, token, tokenIface.encodeFunctionData('transfer', [bob, held]));
  assert.equal(revertReason(res), 'balance');
  res = await h.send(alice, token, tokenIface.encodeFunctionData('transfer', ['0x' + '00'.repeat(20), 1n]));
  assert.equal(revertReason(res), 'zero address');
  res = await h.send(carol, token, tokenIface.encodeFunctionData('transferFrom', [bob, carol, 1n]));
  assert.equal(revertReason(res), 'allowance');
  await h.send(bob, token, tokenIface.encodeFunctionData('approve', [carol, 5n]));
  res = await h.send(carol, token, tokenIface.encodeFunctionData('transferFrom', [bob, carol, 5n]));
  assert.ok(!res.exceptionError, revertReason(res));
  assert.equal((await h.read(token, tokenIface, 'allowance', bob, carol))[0], 0n);
});

// ── the website's quotes ─────────────────────────────────────────────────────

test('the website quotes what the contract pays (app/lib/memeclaw_curve.js), trade after trade', async () => {
  const { createRequire } = await import('node:module');
  const require = createRequire(import.meta.url);
  const site = require('../../../app/lib/memeclaw_curve.js');
  const h = await setup();
  for (const [fn, want] of [['SUPPLY', site.SUPPLY], ['VIRTUAL_DOGE', site.VIRTUAL_DOGE],
    ['VIRTUAL_TOKENS', site.VIRTUAL_TOKENS]]) {
    assert.equal((await h.read(h.factory, factoryIface, fn))[0], want, `${fn}: the site and the contract disagree`);
  }
  const { token } = await h.launch(alice, 'Quotes', 'QUOTE');
  let seed = 11n;
  const rnd = (n) => { seed = (seed * 6364136223846793005n + 1442695040888963407n) % (2n ** 64n); return seed % n; };
  for (let i = 0; i < 40; i += 1) {
    const c = await h.curve(token);
    const curve = { dogeReserve: c.dogeReserve, tokenReserve: c.tokenReserve };
    const amount = (rnd(5000n) + 1n) * E18 / 1000n;
    if (rnd(2n) === 0n) {
      const quoted = site.quoteBuy(curve, amount);
      const before = await h.tokenBalance(token, bob);
      const res = await h.buy(bob, token, amount);
      if (quoted === null) { assert.equal(revertReason(res), 'sold out'); continue; }
      assert.ok(!res.exceptionError, revertReason(res));
      assert.equal(await h.tokenBalance(token, bob) - before, quoted, `buy ${i}`);
    } else {
      const held = await h.tokenBalance(token, bob);
      if (held === 0n) continue;
      const amt = held * (rnd(90n) + 1n) / 100n;
      const quoted = site.quoteSell(curve, amt);
      await h.approve(bob, token, amt);
      const before = await h.balance(bob);
      const res = await h.sell(bob, token, amt);
      if (quoted === 0n) { assert.equal(revertReason(res), 'too small'); continue; }
      assert.ok(!res.exceptionError, revertReason(res));
      assert.equal(await h.balance(bob) - before, quoted, `sell ${i}`);
    }
  }
  // The largest buy the site offers is one the contract fills.
  const c = await h.curve(token);
  const max = site.maxBuy({ dogeReserve: c.dogeReserve, tokenReserve: c.tokenReserve });
  const res = await h.buy(carol, token, max);
  assert.ok(!res.exceptionError, 'maxBuy must fill: ' + revertReason(res));
  assert.ok((await h.curve(token)).tokenReserve < E18, 'and it empties the curve, to rounding');
});

test('the contract\'s chain is the registry\'s DogeOS testnet, and the registry calls it a testnet', async () => {
  const { createRequire } = await import('node:module');
  const require = createRequire(import.meta.url);
  const { chains } = require('../../../app/lib/root_anchor.js');
  const doge = chains({})['dogeos-testnet'];
  assert.equal(doge.testnet, true);
  const h = await setup();
  assert.equal((await h.read(h.factory, factoryIface, 'CHAIN_ID'))[0], BigInt(doge.chain_id),
    'the contract refuses every chain but the one the website reads');
  // And the website's own length limits are the contract's.
  const mc = require('../../../app/lib/memeclaw.js');
  assert.equal((await h.read(h.factory, factoryIface, 'MAX_NAME_BYTES'))[0], BigInt(mc.MAX_NAME_BYTES));
  assert.equal((await h.read(h.factory, factoryIface, 'MAX_SYMBOL_BYTES'))[0], BigInt(mc.MAX_SYMBOL_BYTES));
});

test('the website\'s name and symbol rules agree with the contract\'s, case by case', async () => {
  const { createRequire } = await import('node:module');
  const require = createRequire(import.meta.url);
  const mc = require('../../../app/lib/memeclaw.js');
  const h = await setup();
  const names = ['Much Claw', '', 'x'.repeat(32), 'x'.repeat(33), 'Tab\there', 'Dоge', ' lead', 'trail ', 'A b-c_d!~', 'zero​width'];
  const symbols = ['CLAW', '', 'ABCDEFGHIJ', 'ABCDEFGHIJK', 'claw', 'CL-AW', 'Z9'];
  for (const name of names) {
    const { res } = await h.launch(alice, name, 'OK');
    const site = mc.chainNameProblem(name);
    assert.equal(site, res.exceptionError ? revertReason(res).replace(' ', '_') : null, `name ${JSON.stringify(name)}`);
  }
  for (const symbol of symbols) {
    const { res } = await h.launch(alice, 'Fine', symbol);
    const site = mc.chainSymbolProblem(symbol);
    assert.equal(site, res.exceptionError ? revertReason(res).replace(' ', '_') : null, `symbol ${JSON.stringify(symbol)}`);
  }
});
