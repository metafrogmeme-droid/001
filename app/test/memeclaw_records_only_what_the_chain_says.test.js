'use strict';
/**
 * Memeclaw's website, driven through its routes and its indexer against a
 * DogeOS testnet that answers from a ledger this test writes: receipts and
 * logs encoded by real `ethers` from the committed artifact's ABI, so the
 * decoder reads exactly what the factory emits.
 *
 * The promises: off until the factory's code checks out; a plan is refused
 * for a name the page must not take and otherwise is exactly the call the
 * contract expects; a launch is recorded only from a receipt the chain gave,
 * sealed only when the page planned those terms for that creator; a trade
 * moves a coin's reserves only forward; a chain that does not answer is
 * "could not be read", never "no such launch".
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const express = require('express');
const { ethers } = require('ethers');

const db = require('../db');
const mc = require('../lib/memeclaw');
const mchain = require('../lib/memeclaw_chain');
const curve = require('../lib/memeclaw_curve');
const store = require('../lib/memeclaw_store');
const memeclawConfig = require('../lib/memeclaw_config');
const indexer = require('../lib/memeclaw_indexer');
const { lookupLaunch } = require('../lib/memeclaw_receipts');
const { sealsForDay } = require('../lib/seal_roots');

const iface = new ethers.Interface(mc.FACTORY.abi);
const tokenIface = new ethers.Interface(mc.TOKEN.abi);
const FACTORY = '0x' + 'fa'.repeat(20);
const ALICE = '0x' + 'a1'.repeat(20);
const BOB = '0x' + 'b0'.repeat(20);
const E18 = 10n ** 18n;

// ── the fake chain ───────────────────────────────────────────────────────────

const ledger = { code: {}, receipts: {}, txs: {}, blocks: {}, logs: [], calls: {}, head: 1000, down: false, logError: null };
function resetLedger() {
  Object.assign(ledger, { code: {}, receipts: {}, txs: {}, blocks: {}, logs: [], calls: {}, head: 1000, down: false, logError: null });
  ledger.code[FACTORY] = mc.FACTORY.deployedBytecode;
}
const hx = (n) => '0x' + BigInt(n).toString(16);
mchain.seam.rpcs = ['https://rpc.example.test/'];
mchain.seam.fetch = async (url, opts) => {
  if (ledger.down) throw new TypeError('fetch failed');
  const { method, params } = JSON.parse(opts.body);
  let result = null;
  if (method === 'eth_chainId') result = hx(mchain.chain().chain_id);
  else if (method === 'eth_blockNumber') result = hx(ledger.head);
  else if (method === 'eth_getCode') result = ledger.code[String(params[0]).toLowerCase()] || '0x';
  else if (method === 'eth_getTransactionReceipt') result = ledger.receipts[params[0]] || null;
  else if (method === 'eth_getTransactionByHash') result = ledger.txs[params[0]] || null;
  else if (method === 'eth_getBlockByNumber') result = { number: params[0], timestamp: hx(1_792_000_000 + Number(BigInt(params[0])) * 3) };
  else if (method === 'eth_getLogs') {
    if (ledger.logError) {
      const e = ledger.logError(params[0]);
      if (e) return { ok: true, json: async () => ({ jsonrpc: '2.0', id: 1, error: { code: -32602, message: e } }) };
    }
    const from = Number(BigInt(params[0].fromBlock)); const to = Number(BigInt(params[0].toBlock));
    result = ledger.logs.filter((l) => Number(BigInt(l.blockNumber)) >= from && Number(BigInt(l.blockNumber)) <= to);
  } else if (method === 'eth_call') {
    const key = `${String(params[0].to).toLowerCase()}|${params[0].data}`;
    if (!(key in ledger.calls)) throw new Error(`test chain: no answer for eth_call ${key.slice(0, 60)}`);
    result = ledger.calls[key];
  } else throw new Error(`test chain: ${method} not modelled`);
  return { ok: true, json: async () => ({ jsonrpc: '2.0', id: 1, result }) };
};

let logIndex = 0;
function logOf(name, args, { block, tx, address = FACTORY }) {
  const ev = iface.encodeEventLog(name, args);
  logIndex += 1;
  return { address, topics: ev.topics, data: ev.data, blockNumber: hx(block), transactionHash: tx, logIndex: hx(logIndex) };
}
function txHash(n) { return '0x' + crypto.createHash('sha256').update(String(n)).digest('hex'); }

/** A launch in block `block`, with an optional creator buy, as the factory would emit it. */
function chainLaunch({ token, creator, name, symbol, terms, buy = 0n, block = 900, n }) {
  const tx = txHash(`launch:${n || token}`);
  const logs = [logOf('Launched', [token, creator, terms, name, symbol], { block, tx })];
  let state = { dogeReserve: 0n, tokenReserve: curve.SUPPLY };
  if (buy > 0n) {
    const out = curve.quoteBuy(state, buy);
    state = { dogeReserve: buy, tokenReserve: curve.SUPPLY - out };
    logs.push(logOf('Trade', [token, creator, true, buy, out, state.dogeReserve, state.tokenReserve], { block, tx }));
  }
  ledger.receipts[tx] = { status: '0x1', to: FACTORY, blockNumber: hx(block), logs, transactionHash: tx };
  ledger.txs[tx] = { hash: tx, blockHash: '0x' + '11'.repeat(32) };
  ledger.logs.push(...logs);
  return { tx, state };
}

// ── the app ──────────────────────────────────────────────────────────────────

const router = require('../routes/memeclaw');
const app = express();
// Each call comes from its own address (X-Forwarded-For), so the limits are
// not what a test measures; the test at the end drives one caller into them.
app.set('trust proxy', true);
app.use(express.json());
app.use((req, _res, next) => { const u = req.get('x-test-user'); if (u) req.user = { user_id: Number(u) }; next(); });
app.use('/api/memeclaw', router);
let base;
let server;
test.before(async () => {
  server = app.listen(0, '127.0.0.1');
  await new Promise((r) => server.once('listening', r));
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { server.close(); });

let env = {};
router._deps = { env: new Proxy({}, { get: (_t, k) => env[k] }), now: () => new Date('2026-10-21T12:00:00Z') };

let caller = 0;
async function call(method, path, body, ip) {
  caller += 1;
  const r = await fetch(base + '/api/memeclaw' + path, { method,
    headers: { 'content-type': 'application/json', 'x-forwarded-for': ip || `10.9.${(caller >> 8) & 255}.${caller & 255}` },
    body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, body: await r.json() };
}

function freshState() {
  resetLedger();
  memeclawConfig._resetChecks();
  for (const k of ['memeclawPlans', 'memeclawCoins', 'memeclawTrades']) db.pool[k] = [];
  env = { MEMECLAW_FACTORY: FACTORY, MEMECLAW_FACTORY_BLOCK: '800' };
}

// ── off until checked ────────────────────────────────────────────────────────

test('off until a factory is set, and off while its code is not the artifact or cannot be read', async () => {
  freshState();
  env = {};
  let r = await call('GET', '/config');
  assert.equal(r.body.enabled, false);
  assert.equal(r.body.reason, 'not_deployed');
  assert.equal(r.body.chain.key, 'dogeos-testnet');
  assert.equal(r.body.chain.testnet, true);
  r = await call('POST', '/launch-plan', { creator: ALICE, name: 'Much Claw', symbol: 'CLAW' });
  assert.equal(r.status, 503);
  assert.equal(r.body.error, 'memeclaw_off');

  freshState();
  ledger.code[FACTORY] = mc.FACTORY.deployedBytecode.replace(/..$/, '00');
  r = await call('GET', '/config');
  assert.equal(r.body.enabled, false);
  assert.equal(r.body.reason, 'factory_mismatch', 'one byte off is not the factory');

  freshState();
  ledger.down = true;
  r = await call('GET', '/config');
  assert.equal(r.body.reason, 'factory_unknown', 'an unreadable chain is not a verdict');
  ledger.down = false;
  r = await call('GET', '/config');
  assert.equal(r.body.enabled, true, 'and "unknown" is not cached: the next ask reads the chain again');
  assert.equal(r.body.factory, FACTORY);
});

// ── the page's rules ─────────────────────────────────────────────────────────

test('a launch plan is refused for a name the page must not take, with the reason', async () => {
  freshState();
  const cases = [
    [{ name: 'Apple Inu', symbol: 'AAPL' }, 'symbol_reserved', 'tokenized'],
    [{ name: 'Tesla Moon', symbol: 'TSLAON' }, 'symbol_reserved', 'tokenized'],
    [{ name: 'Fake Doge', symbol: 'DOGE' }, 'symbol_reserved', 'majors'],
    [{ name: 'Dollar', symbol: 'USDC' }, 'symbol_reserved', 'money'],
    [{ name: 'Rune', symbol: 'RCLAW' }, 'symbol_reserved', 'project'],
    [{ name: 'Official RUNECLAW Inu', symbol: 'RCI' }, 'name_reserved', 'project'],
    [{ name: 'Humanoid Traders Coin', symbol: 'HTC' }, 'name_reserved', 'project'],
    [{ name: 'NVDA moon', symbol: 'MOON' }, 'name_reserved', 'tokenized'],
    [{ name: 'Apple Stock', symbol: 'APPL' }, 'presented_as_security', undefined],
    [{ name: 'Doge Shares', symbol: 'DSH' }, 'presented_as_security', undefined],
    [{ name: 'Fine', symbol: 'FINE', description: 'A tokenized equity of nothing' }, 'presented_as_security', undefined],
    [{ name: 'Fine', symbol: 'FINE', description: 'buy at pump.fun/x' }, 'description_link', undefined],
    [{ name: 'Fine', symbol: 'FINE', description: 'join t.me/fine' }, 'description_link', undefined],
    [{ name: 'claw.xyz', symbol: 'FINE' }, 'name_link', undefined],
    [{ name: 'Dоge', symbol: 'FINE' }, 'name_characters', undefined],
    [{ name: 'Fine', symbol: 'fine' }, 'symbol_characters', undefined],
    [{ name: 'Fine', symbol: 'FINE', description: 'x'.repeat(281) }, 'description_length', undefined],
  ];
  for (const [c, reason, detail] of cases) {
    const r = await call('POST', '/launch-plan', { creator: ALICE, ...c });
    assert.equal(r.status, 400, JSON.stringify(c));
    assert.equal(r.body.error, reason, JSON.stringify(c));
    if (detail) assert.equal(r.body.detail, detail, JSON.stringify(c));
  }
  let r = await call('POST', '/launch-plan', { creator: ALICE, name: 'Fine', symbol: 'FINE', dev_buy: String(41n * E18) });
  assert.equal(r.body.error, 'dev_buy_too_large');
  r = await call('POST', '/launch-plan', { creator: 'not an address', name: 'Fine', symbol: 'FINE' });
  assert.equal(r.body.error, 'creator_address');
  // The other arm: a plain meme name passes, CLAW included.
  r = await call('POST', '/launch-plan', { creator: ALICE, name: 'Much Claw', symbol: 'CLAW' });
  assert.equal(r.status, 200, JSON.stringify(r.body));
});

test('a plan is exactly the call the contract expects, with the terms hash the page sealed', async () => {
  freshState();
  const devBuy = 2n * E18;
  const r = await call('POST', '/launch-plan', { creator: ALICE.toUpperCase().replace('0X', '0x'), name: 'Much Claw',
    symbol: 'CLAW', description: '  very   wow  ', dev_buy: devBuy.toString() });
  assert.equal(r.status, 200, JSON.stringify(r.body));
  const p = r.body;
  assert.equal(p.to, FACTORY);
  assert.equal(p.from, ALICE);
  assert.equal(BigInt(p.value), devBuy);
  const terms = { chain_id: mchain.chain().chain_id, factory: FACTORY, creator: ALICE, name: 'Much Claw', symbol: 'CLAW', description: 'very wow' };
  assert.equal(p.terms_payload, mc.canonicalTerms(terms));
  assert.equal(p.terms_hash, '0x' + crypto.createHash('sha256').update(p.terms_payload, 'utf8').digest('hex'));
  const decoded = iface.decodeFunctionData('launch', p.data);
  assert.equal(decoded[0], 'Much Claw');
  assert.equal(decoded[1], 'CLAW');
  assert.equal(decoded[2], p.terms_hash);
  assert.equal(decoded[3], curve.quoteBuy({ dogeReserve: 0n, tokenReserve: curve.SUPPLY }, devBuy), 'the floor is the exact quote on a fresh curve');
  assert.equal(decoded[4], BigInt(Math.floor(Date.parse('2026-10-21T12:00:00Z') / 1000) + mc.PLAN_TTL_S));
  assert.ok(await store.planFor(p.terms_hash), 'the plan is kept for the launch to be matched against');
});

// ── launches, read from the chain ────────────────────────────────────────────

test('a launch is recorded and sealed from the chain\'s receipt, and the seal is in the day\'s root', async () => {
  freshState();
  const plan = (await call('POST', '/launch-plan', { creator: ALICE, name: 'Much Claw', symbol: 'CLAW',
    description: 'wow', dev_buy: String(E18) })).body;
  const token = '0x' + '70'.repeat(20);
  const { tx, state } = chainLaunch({ token, creator: ALICE, name: 'Much Claw', symbol: 'CLAW', terms: plan.terms_hash, buy: E18 });
  const r = await call('POST', '/coins', { tx_hash: tx });
  assert.equal(r.status, 200, JSON.stringify(r.body));
  assert.equal(r.body.sealed, true);
  const c = r.body.coin;
  assert.equal(c.address, token);
  assert.equal(c.terms, 'recorded');
  assert.equal(c.description, 'wow');
  assert.equal(c.doge_reserve, state.dogeReserve.toString());
  assert.equal(c.token_reserve, state.tokenReserve.toString());
  assert.match(c.receipt, /^launch:[0-9a-f]{64}$/);

  const row = await store.coin(token);
  const payload = JSON.parse(row.seal_payload);
  assert.equal(payload.v, 8);
  assert.equal(payload.terms_hash, plan.terms_hash);
  assert.equal(payload.creator_bought, (curve.SUPPLY - state.tokenReserve).toString());
  assert.equal(payload.launched_at, new Date((1_792_000_000 + 900 * 3) * 1000).toISOString(), 'the block\'s time, not ours');
  assert.equal(row.seal, crypto.createHash('sha256').update(row.seal_payload, 'utf8').digest('hex'));
  assert.ok((await store.planFor(plan.terms_hash)).used_at, 'the plan is spent');

  const receipt = await lookupLaunch(c.receipt);
  assert.equal(receipt.code, 200);
  assert.equal(receipt.body.kind, 'memeclaw_launch');
  assert.equal(receipt.body.seal_payload, row.seal_payload);
  const day = new Date(row.sealed_at).toISOString().slice(0, 10);
  assert.ok((await sealsForDay(day)).includes(row.seal), 'the launch seal is a leaf of its day');

  // Asked twice, the same: no second seal, no second trade.
  const again = await call('POST', '/coins', { tx_hash: tx });
  assert.equal(again.status, 200);
  assert.equal((await store.coin(token)).seal, row.seal);
  assert.equal(db.pool.memeclawTrades.length, 1);

  // The list and the coin page.
  const list = await call('GET', '/coins');
  assert.deepEqual(list.body.coins.map((x) => x.address), [token]);
  const one = await call('GET', `/coins/${token}`);
  assert.equal(one.status, 200);
  assert.equal(one.body.reading.creator_bought_at_launch_bps, Number((curve.SUPPLY - state.tokenReserve) * 10000n / curve.SUPPLY));
  assert.equal(one.body.reading.creator_coins_launched, 1);
  assert.strictEqual(one.body.reading.holders, null, 'holders are not read, and said so; never 0');
  assert.equal(one.body.trades.length, 1);
  for (const k of ['balance', 'pnl', 'usd', 'value_usd']) assert.ok(!(k in one.body.coin), `no ${k} on a public coin`);
});

test('a receipt that is not a launch at the factory is refused, and an unmined one is "not yet"', async () => {
  freshState();
  const token = '0x' + '71'.repeat(20);
  const { tx } = chainLaunch({ token, creator: ALICE, name: 'Elsewhere', symbol: 'ELSE', terms: '0x' + '01'.repeat(32) });
  ledger.receipts[tx].to = '0x' + 'ee'.repeat(20);
  let r = await call('POST', '/coins', { tx_hash: tx });
  assert.equal(r.status, 400);
  assert.match(r.body.reason, /not sent to the Memeclaw factory/);

  ledger.receipts[tx].to = FACTORY;
  ledger.receipts[tx].status = '0x0';
  r = await call('POST', '/coins', { tx_hash: tx });
  assert.equal(r.status, 400);
  assert.match(r.body.reason, /failed on chain/);

  // Logs from another contract that look like a launch are not the factory's.
  ledger.receipts[tx].status = '0x1';
  ledger.receipts[tx].logs = ledger.receipts[tx].logs.map((l) => ({ ...l, address: '0x' + 'ee'.repeat(20) }));
  r = await call('POST', '/coins', { tx_hash: tx });
  assert.equal(r.status, 400);
  assert.match(r.body.reason, /no launch in this transaction/);

  const pending = txHash('pending');
  ledger.txs[pending] = { hash: pending, blockHash: null };
  r = await call('POST', '/coins', { tx_hash: pending });
  assert.equal(r.status, 503);
  assert.equal(r.body.reason, 'not yet mined');

  r = await call('POST', '/coins', { tx_hash: txHash('nowhere') });
  assert.equal(r.status, 400);
  assert.match(r.body.reason, /no such transaction/);

  ledger.down = true;
  memeclawConfig._resetChecks();
  r = await call('POST', '/coins', { tx_hash: tx });
  assert.equal(r.status, 503, 'a chain that does not answer has said nothing');
  assert.equal(db.pool.memeclawCoins.length, 0, 'and nothing was recorded from it');
});

test('terms planned for another wallet, or not planned at all, are recorded but never sealed', async () => {
  freshState();
  const plan = (await call('POST', '/launch-plan', { creator: ALICE, name: 'Mine', symbol: 'MINE' })).body;
  // Bob launches with the terms hash Alice's plan carries: the chain names Bob.
  const t1 = '0x' + '72'.repeat(20);
  const { tx } = chainLaunch({ token: t1, creator: BOB, name: 'Mine', symbol: 'MINE', terms: plan.terms_hash });
  let r = await call('POST', '/coins', { tx_hash: tx });
  assert.equal(r.status, 200);
  assert.equal(r.body.sealed, false);
  assert.equal(r.body.coin.terms, 'not_recorded');
  assert.strictEqual(r.body.coin.description, null);
  assert.ok(!(await store.planFor(plan.terms_hash)).used_at, 'Alice\'s plan is still hers');

  const t2 = '0x' + '73'.repeat(20);
  const direct = chainLaunch({ token: t2, creator: BOB, name: 'Direct', symbol: 'DIR', terms: '0x' + '02'.repeat(32), n: 2 });
  r = await call('POST', '/coins', { tx_hash: direct.tx });
  assert.equal(r.body.sealed, false);
  assert.equal(r.body.coin.terms, 'not_recorded');
});

test('a coin launched straight at the contract under a refused name is recorded hidden, and its name is not repeated', async () => {
  freshState();
  const token = '0x' + '74'.repeat(20);
  const { tx } = chainLaunch({ token, creator: BOB, name: 'Apple Stock', symbol: 'AAPL', terms: '0x' + '03'.repeat(32) });
  const r = await call('POST', '/coins', { tx_hash: tx });
  assert.equal(r.status, 200);
  const row = await store.coin(token);
  assert.ok(row.hidden_at);
  assert.match(row.hidden_reason, /symbol_reserved/);
  assert.equal((await call('GET', '/coins')).body.coins.length, 0, 'not on the list');
  const one = await call('GET', `/coins/${token}`);
  assert.equal(one.status, 404);
  assert.equal(one.body.error, 'hidden');
  assert.ok(!JSON.stringify(one.body).includes('Apple'), 'the page does not repeat the name it hid');
});

// ── trades ───────────────────────────────────────────────────────────────────

test('trades move a coin\'s reserves forward only, and a trade read twice is one trade', async () => {
  freshState();
  const token = '0x' + '75'.repeat(20);
  const { tx: ltx } = chainLaunch({ token, creator: ALICE, name: 'Order', symbol: 'ORD', terms: '0x' + '04'.repeat(32), block: 900 });
  await call('POST', '/coins', { tx_hash: ltx });
  const tA = txHash('trade-a'); const tB = txHash('trade-b');
  const a = logOf('Trade', [token, BOB, true, E18, 1000n, E18, curve.SUPPLY - 1000n], { block: 905, tx: tA });
  const b = logOf('Trade', [token, BOB, false, 5n, 10n, E18 - 5n, curve.SUPPLY - 990n], { block: 910, tx: tB });
  ledger.receipts[tA] = { status: '0x1', to: FACTORY, blockNumber: hx(905), logs: [a] };
  ledger.receipts[tB] = { status: '0x1', to: FACTORY, blockNumber: hx(910), logs: [b] };
  // The newer trade first, then the older one: the reserves stay at the newer.
  let r = await call('POST', '/trades', { tx_hash: tB });
  assert.equal(r.status, 200, JSON.stringify(r.body));
  r = await call('POST', '/trades', { tx_hash: tA });
  assert.equal(r.status, 200);
  let row = await store.coin(token);
  assert.equal(row.doge_reserve, (E18 - 5n).toString(), 'an older trade does not move the reserves back');
  assert.equal(row.trade_count, 2);
  r = await call('POST', '/trades', { tx_hash: tB });
  assert.equal(r.body.recorded, 0, 'a trade read twice is one trade');
  row = await store.coin(token);
  assert.equal(row.trade_count, 2);
  // A transaction with no factory trade is not a trade.
  const tC = txHash('trade-c');
  ledger.receipts[tC] = { status: '0x1', to: FACTORY, blockNumber: hx(911), logs: [] };
  r = await call('POST', '/trades', { tx_hash: tC });
  assert.equal(r.status, 400);
});

test('a trade plan prices against the live curve, asks for an approval only when one is missing, and refuses what the curve cannot fill', async () => {
  freshState();
  const token = '0x' + '76'.repeat(20);
  const live = { dogeReserve: 3n * E18, tokenReserve: curve.SUPPLY - 100_000_000n * E18 };
  const curvesData = mc.lower(new ethers.Interface(mc.FACTORY.abi).encodeFunctionData('curves', [token]));
  ledger.calls[`${FACTORY}|${curvesData}`] = ethers.AbiCoder.defaultAbiCoder()
    .encode(['uint256', 'uint256', 'address', 'uint64'], [live.dogeReserve, live.tokenReserve, ALICE, 1_792_000_000n]);
  // Buy: the quote is the curve's, the floor is 1% under it.
  let r = await call('POST', '/trade-plan', { action: 'buy', coin: token, wallet: BOB, amount: String(E18) });
  assert.equal(r.status, 200, JSON.stringify(r.body));
  const q = curve.quoteBuy(live, E18);
  assert.equal(r.body.quote.gets, q.toString());
  assert.equal(r.body.quote.floor, (q * 9900n / 10000n).toString());
  assert.equal(r.body.plans.length, 1);
  const d = iface.decodeFunctionData('buy', r.body.plans[0].data);
  assert.equal(mc.lower(d[0]), token);
  assert.equal(d[1], q * 9900n / 10000n);
  assert.equal(BigInt(r.body.plans[0].value), E18);
  // More than the supply left: refused with the most it could take.
  r = await call('POST', '/trade-plan', { action: 'buy', coin: token, wallet: BOB, amount: String(1000n * E18) });
  assert.equal(r.status, 400);
  assert.equal(r.body.error, 'sold_out');
  assert.equal(r.body.max_buy, curve.maxBuy(live).toString());

  // Sell: Bob holds 5M, has approved nothing.
  const held = 5_000_000n * E18;
  const bal = mc.lower(tokenIface.encodeFunctionData('balanceOf', [BOB]));
  const alw = mc.lower(tokenIface.encodeFunctionData('allowance', [BOB, FACTORY]));
  const word = (n) => ethers.AbiCoder.defaultAbiCoder().encode(['uint256'], [n]);
  ledger.calls[`${token}|${bal}`] = word(held);
  ledger.calls[`${token}|${alw}`] = word(0n);
  r = await call('POST', '/trade-plan', { action: 'sell', coin: token, wallet: BOB, amount: String(held) });
  assert.equal(r.status, 200, JSON.stringify(r.body));
  assert.deepEqual(r.body.plans.map((p) => p.step), ['approve', 'sell']);
  assert.equal(r.body.plans[0].to, token);
  const ap = tokenIface.decodeFunctionData('approve', r.body.plans[0].data);
  assert.equal(mc.lower(ap[0]), FACTORY);
  assert.equal(ap[1], held);
  // The other arm: approved already, one step.
  ledger.calls[`${token}|${alw}`] = word(held);
  r = await call('POST', '/trade-plan', { action: 'sell', coin: token, wallet: BOB, amount: String(held) });
  assert.deepEqual(r.body.plans.map((p) => p.step), ['sell']);
  // More than held.
  r = await call('POST', '/trade-plan', { action: 'sell', coin: token, wallet: BOB, amount: String(held + 1n) });
  assert.equal(r.body.error, 'not_held');
  assert.equal(r.body.held, held.toString());
  // A coin the factory does not know.
  const other = '0x' + '77'.repeat(20);
  ledger.calls[`${FACTORY}|${mc.lower(iface.encodeFunctionData('curves', [other]))}`] = ethers.AbiCoder.defaultAbiCoder()
    .encode(['uint256', 'uint256', 'address', 'uint64'], [0n, 0n, ethers.ZeroAddress, 0n]);
  r = await call('POST', '/trade-plan', { action: 'buy', coin: other, wallet: BOB, amount: String(E18) });
  assert.equal(r.body.error, 'unknown_coin');
  // A chain that does not answer: 503, not a refusal.
  ledger.down = true;
  r = await call('POST', '/trade-plan', { action: 'buy', coin: token, wallet: BOB, amount: String(E18) });
  assert.equal(r.status, 503);
  ledger.down = false;
});

// ── the indexer ──────────────────────────────────────────────────────────────

test('the indexer reads the factory\'s events in order, remembers where it stopped, and narrows a range the RPC refuses', async () => {
  freshState();
  const token = '0x' + '78'.repeat(20);
  const plan = (await call('POST', '/launch-plan', { creator: ALICE, name: 'Indexed', symbol: 'IDX' })).body;
  chainLaunch({ token, creator: ALICE, name: 'Indexed', symbol: 'IDX', terms: plan.terms_hash, buy: E18, block: 850 });
  const tB = txHash('idx-buy');
  ledger.logs.push(logOf('Trade', [token, BOB, true, E18, 77n, 2n * E18, curve.SUPPLY - 999n], { block: 860, tx: tB }));
  ledger.head = 2900;
  // The RPC refuses any range wider than 100 blocks; the indexer starts at 2000.
  let refused = 0;
  ledger.logError = (f) => {
    if (Number(BigInt(f.toBlock)) - Number(BigInt(f.fromBlock)) < 100) return null;
    refused += 1;
    return 'query exceeds max block range 100';
  };
  const cfg = () => memeclawConfig.config({ env: { MEMECLAW_FACTORY: FACTORY, MEMECLAW_FACTORY_BLOCK: '800' } });
  const run1 = await indexer.runOnce({ config: cfg });
  assert.ok(!run1.failed, JSON.stringify(run1));
  assert.equal(run1.read_from, 800);
  assert.ok(refused >= 1, 'the RPC refused a range, and the indexer asked again smaller');
  assert.ok(run1.read_to > 800 && run1.read_to < 2897, `one run reads a bounded stretch (${run1.read_to})`);
  const row = await store.coin(token);
  assert.ok(row, 'the launch the page never heard about is recorded');
  assert.ok(row.seal, 'and sealed: the page planned these terms for this creator');
  assert.equal(row.trade_count, 2);
  assert.equal(row.doge_reserve, (2n * E18).toString(), 'the reserves are the newest trade\'s');

  // A second run reads on from where the first stopped, and records nothing twice.
  ledger.logError = null;
  const before = db.pool.memeclawTrades.length;
  const run2 = await indexer.runOnce({ config: cfg });
  assert.equal(run2.read_from, run1.read_to + 1);
  assert.equal(db.pool.memeclawTrades.length, before);

  // Off: the indexer skips and reads nothing.
  const off = await indexer.runOnce({ config: () => memeclawConfig.config({ env: {} }) });
  assert.equal(off.skipped, 'not_deployed');

  // A chain that does not answer: the run fails, and the cursor does not move.
  ledger.down = true;
  memeclawConfig._resetChecks();
  const failed = await indexer.runOnce({ config: async () => ({ ...(await (async () => { ledger.down = false; const c = await cfg(); ledger.down = true; return c; })()) }) });
  assert.ok(failed.failed, JSON.stringify(failed));
  ledger.down = false;
  const run3 = await indexer.runOnce({ config: cfg });
  assert.equal(run3.read_from, run2.read_to + 1, 'the failed run moved nothing');
});

// ── the operator's doors ─────────────────────────────────────────────────────

test('only the operator hides a coin or reads the deploy plan', async () => {
  freshState();
  const token = '0x' + '79'.repeat(20);
  const { tx } = chainLaunch({ token, creator: ALICE, name: 'Hideme', symbol: 'HIDE', terms: '0x' + '05'.repeat(32) });
  await call('POST', '/coins', { tx_hash: tx });
  let r = await call('POST', `/coins/${token}/hide`, { reason: 'spam' });
  assert.equal(r.status, 401, 'no session, no door');
  r = await call('GET', '/deploy-plan');
  assert.equal(r.status, 401);
  // The other arm, with an operator session, is driven through isOperator
  // in app/test/memeclaw_page.test.js; here the store's own door:
  assert.equal(await store.setHidden(token, 'spam'), true);
  assert.equal((await call('GET', '/coins')).body.coins.length, 0);
  assert.equal(await store.setHidden(token, null), true);
  assert.equal((await call('GET', '/coins')).body.coins.length, 1);
});

test('one caller asking for plans faster than the limit is told to slow down', async () => {
  freshState();
  const statuses = [];
  for (let i = 0; i < 14; i += 1) {
    statuses.push((await call('POST', '/launch-plan', { creator: ALICE, name: `Spam ${i}`, symbol: `SPAM${i}` }, '10.200.0.1')).status);
  }
  assert.deepEqual(statuses.slice(0, 12), Array(12).fill(200));
  assert.equal(statuses[12], 429, 'the thirteenth plan in a minute is refused');
});
