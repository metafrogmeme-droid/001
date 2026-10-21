'use strict';
/**
 * Memeclaw's practice mode, driven through its routes: practice DOGE and
 * practice coins on the same curve as the contract, no wallet, no chain.
 *
 * The promises: a practice player is a key only its browser knows (the
 * server keeps the hash); every practice DOGE handed out is, at every step,
 * either held by a player or in a coin's curve, never made or lost by a
 * trade; a trade that fails half way leaves nothing changed; the page's
 * rules apply to practice coins too; the refill is for practising, not for
 * farming.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const express = require('express');

const db = require('../db');
const curve = require('../lib/memeclaw_curve');
const paper = require('../lib/memeclaw_paper');

const E18 = curve.E18;
const router = require('../routes/memeclaw');
const app = express();
app.set('trust proxy', true);
app.use(express.json());
app.use('/api/memeclaw', router);
let base; let server; let clock = Date.parse('2026-10-21T12:00:00Z');
router._deps = { env: {}, now: () => new Date(clock) };
test.before(async () => {
  server = app.listen(0, '127.0.0.1');
  await new Promise((r) => server.once('listening', r));
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => server.close());

let caller = 0;
async function call(method, path, body, key) {
  caller += 1;
  const headers = { 'content-type': 'application/json', 'x-forwarded-for': `10.7.${(caller >> 8) & 255}.${caller & 255}` };
  if (key) headers['x-memeclaw-practice'] = key;
  const r = await fetch(`${base}/api/memeclaw${path}`, { method, headers, body: body ? JSON.stringify(body) : undefined });
  return { status: r.status, body: await r.json() };
}

function fresh() {
  for (const k of ['memeclawPaperPlayers', 'memeclawPaperCoins', 'memeclawPaperHoldings', 'memeclawPaperTrades']) db.pool[k] = [];
  clock = Date.parse('2026-10-21T12:00:00Z');
}

/** Every practice DOGE that exists: what players hold plus what curves hold. */
function totalDoge() {
  const held = db.pool.memeclawPaperPlayers.reduce((s, p) => s + BigInt(p.doge), 0n);
  const pooled = db.pool.memeclawPaperCoins.reduce((s, c) => s + BigInt(c.doge_reserve), 0n);
  return held + pooled;
}

async function player() {
  const r = await call('POST', '/paper/players');
  assert.equal(r.status, 200, JSON.stringify(r.body));
  return r.body;
}

test('a practice player is a key only its browser knows, with 100 practice DOGE', async () => {
  fresh();
  const p = await player();
  assert.match(p.key, /^[0-9a-f]{64}$/);
  assert.equal(p.player.practice_doge, (100n * E18).toString());
  const row = db.pool.memeclawPaperPlayers[0];
  assert.equal(row.key_hash, crypto.createHash('sha256').update(p.key).digest('hex'));
  assert.ok(!JSON.stringify(db.pool.memeclawPaperPlayers).includes(p.key), 'the server keeps the hash, not the key');
  const me = await call('GET', '/paper/me', null, p.key);
  assert.equal(me.status, 200);
  assert.equal(me.body.player.practice_doge, (100n * E18).toString());
  assert.equal(me.body.launches_left_today, paper.LAUNCHES_PER_DAY);
  for (const k of ['balance', 'usd', 'pnl']) assert.ok(!(k in me.body.player), `no ${k} field`);
  assert.equal((await call('GET', '/paper/me')).status, 401, 'no key, no player');
  assert.equal((await call('GET', '/paper/me', null, 'f'.repeat(64))).status, 401, 'a key nobody was given');
});

test('a practice coin follows the page\'s rules, and its first buy is the curve\'s', async () => {
  fresh();
  const p = await player();
  let r = await call('POST', '/paper/launch', { name: 'Apple Inu', symbol: 'AAPL' }, p.key);
  assert.equal(r.status, 400);
  assert.equal(r.body.error, 'symbol_reserved', 'a practice stock is still a stock');
  r = await call('POST', '/paper/launch', { name: 'Fine', symbol: 'FINE', description: 'see t.me/x' }, p.key);
  assert.equal(r.body.error, 'description_link');
  r = await call('POST', '/paper/launch', { name: 'Much Claw', symbol: 'CLAW', description: 'wow', dev_buy: String(2n * E18) }, p.key);
  assert.equal(r.status, 200, JSON.stringify(r.body));
  const c = r.body.coin;
  assert.equal(c.mode, 'practice');
  assert.match(c.id, /^p[0-9a-f]{16}$/);
  const out = curve.quoteBuy({ dogeReserve: 0n, tokenReserve: curve.SUPPLY }, 2n * E18);
  assert.equal(c.token_reserve, (curve.SUPPLY - out).toString());
  assert.equal(c.doge_reserve, (2n * E18).toString());
  const me = await call('GET', '/paper/me', null, p.key);
  assert.equal(me.body.player.practice_doge, (98n * E18).toString());
  assert.deepEqual(me.body.holdings, [{ coin: c.id, amount: out.toString() }]);
  assert.equal(totalDoge(), 100n * E18);
});

test('every practice DOGE is held or in a curve, trade after trade, and a sell-out leaves only dust', async () => {
  fresh();
  const players = [await player(), await player(), await player()];
  const coin = (await call('POST', '/paper/launch', { name: 'Ledger', symbol: 'LEDG' }, players[0].key)).body.coin.id;
  let seed = 5n;
  const rnd = (n) => { seed = (seed * 6364136223846793005n + 1442695040888963407n) % (2n ** 64n); return seed % n; };
  for (let i = 0; i < 60; i += 1) {
    const p = players[Number(rnd(3n))];
    if (rnd(3n) !== 0n) {
      const amount = (rnd(4000n) + 1n) * E18 / 1000n;
      const q = await call('POST', '/paper/quote', { coin, action: 'buy', amount: amount.toString() });
      if (q.status !== 200) continue;
      const r = await call('POST', '/paper/trade', { coin, action: 'buy', amount: amount.toString(), min_out: q.body.gets }, p.key);
      assert.ok(r.status === 200 || r.body.error === 'not_enough_doge', JSON.stringify(r.body));
      if (r.status === 200) assert.equal(r.body.gets, q.body.gets, 'the trade pays the quote');
    } else {
      const me = (await call('GET', '/paper/me', null, p.key)).body;
      const h = me.holdings.find((x) => x.coin === coin);
      if (!h) continue;
      const amount = BigInt(h.amount) * (rnd(100n) + 1n) / 100n;
      if (amount === 0n) continue;
      const r = await call('POST', '/paper/trade', { coin, action: 'sell', amount: amount.toString() }, p.key);
      assert.ok(r.status === 200 || r.body.error === 'too_small', JSON.stringify(r.body));
    }
    assert.equal(totalDoge(), 300n * E18, `step ${i}: practice DOGE was made or lost`);
  }
  for (const p of players) {
    const h = (await call('GET', '/paper/me', null, p.key)).body.holdings.find((x) => x.coin === coin);
    if (h) await call('POST', '/paper/trade', { coin, action: 'sell', amount: h.amount }, p.key);
  }
  assert.equal(totalDoge(), 300n * E18);
  const c = db.pool.memeclawPaperCoins[0];
  assert.ok(BigInt(c.doge_reserve) < E18 / 1000n, `only rounding dust stays in the curve, got ${c.doge_reserve}`);
});

test('a trade is refused rather than filled at a price the player did not see, or with DOGE they do not have', async () => {
  fresh();
  const a = await player(); const b = await player();
  const coin = (await call('POST', '/paper/launch', { name: 'Race', symbol: 'RACE' }, a.key)).body.coin.id;
  const q = await call('POST', '/paper/quote', { coin, action: 'buy', amount: String(E18) });
  // B buys first, so A's quote is stale.
  await call('POST', '/paper/trade', { coin, action: 'buy', amount: String(5n * E18) }, b.key);
  let r = await call('POST', '/paper/trade', { coin, action: 'buy', amount: String(E18), min_out: q.body.gets }, a.key);
  assert.equal(r.status, 400);
  assert.equal(r.body.error, 'slippage');
  assert.ok(BigInt(r.body.gets) < BigInt(q.body.gets));
  r = await call('POST', '/paper/trade', { coin, action: 'buy', amount: String(101n * E18) }, a.key);
  assert.equal(r.body.error, 'not_enough_doge');
  r = await call('POST', '/paper/trade', { coin, action: 'sell', amount: '1' }, a.key);
  assert.equal(r.body.error, 'not_held');
  r = await call('POST', '/paper/trade', { coin: 'p' + '0'.repeat(16), action: 'buy', amount: String(E18) }, a.key);
  assert.equal(r.body.error, 'unknown_coin');
  // Past the supply left.
  r = await call('POST', '/paper/quote', { coin, action: 'buy', amount: String(100n * E18) });
  assert.equal(r.body.error, 'sold_out');
  assert.ok(BigInt(r.body.max_buy) > 0n);
});

test('a trade that fails half way leaves nothing changed', async () => {
  fresh();
  const p = await player();
  const coin = (await call('POST', '/paper/launch', { name: 'Atomic', symbol: 'ATOM' }, p.key)).body.coin.id;
  const before = JSON.stringify([db.pool.memeclawPaperPlayers, db.pool.memeclawPaperCoins, db.pool.memeclawPaperHoldings]);
  const real = db.pool.execute.bind(db.pool);
  db.pool.execute = async (sql, params) => {
    if (/^INSERT INTO memeclaw_paper_trades/.test(sql)) throw Object.assign(new Error('Lock wait timeout'), { code: 'ER_LOCK_WAIT_TIMEOUT' });
    return real(sql, params);
  };
  try {
    const r = await call('POST', '/paper/trade', { coin, action: 'buy', amount: String(E18) }, p.key);
    assert.equal(r.status, 503);
    assert.equal(r.body.exception, 'Error', 'the class, never the message');
  } finally { db.pool.execute = real; }
  assert.equal(JSON.stringify([db.pool.memeclawPaperPlayers, db.pool.memeclawPaperCoins, db.pool.memeclawPaperHoldings]), before,
    'the balance, the reserves and the holding are as they were');
});

test('five practice launches a day, and the refill is once a day and only when nearly empty', async () => {
  fresh();
  const p = await player();
  for (let i = 0; i < paper.LAUNCHES_PER_DAY; i += 1) {
    assert.equal((await call('POST', '/paper/launch', { name: `Pup ${i}`, symbol: `PUP${i}` }, p.key)).status, 200);
  }
  let r = await call('POST', '/paper/launch', { name: 'One more', symbol: 'MORE' }, p.key);
  assert.equal(r.body.error, 'launches_today');
  clock += 24 * 3600 * 1000;
  assert.equal((await call('POST', '/paper/launch', { name: 'Next day', symbol: 'NEXT' }, p.key)).status, 200, 'a new day, a new five');

  r = await call('POST', '/paper/refill', {}, p.key);
  assert.equal(r.body.error, 'refill_not_needed');
  // Spend it nearly all: 33.2 DOGE on each of three coins (one coin's whole supply costs 40).
  const spend = async (coins) => {
    for (const c of coins) {
      const t = await call('POST', '/paper/trade', { coin: c.id, action: 'buy', amount: String(332n * E18 / 10n) }, p.key);
      assert.equal(t.status, 200, JSON.stringify(t.body));
    }
  };
  await spend(db.pool.memeclawPaperCoins.slice(0, 3));
  r = await call('POST', '/paper/refill', {}, p.key);
  assert.equal(r.status, 200, JSON.stringify(r.body));
  assert.equal(r.body.practice_doge, (100n * E18).toString());
  await spend(db.pool.memeclawPaperCoins.slice(3, 6));
  r = await call('POST', '/paper/refill', {}, p.key);
  assert.equal(r.body.error, 'refill_too_soon');
  assert.ok(r.body.next_at);
});

test('forgetting a player removes its key, balance and holdings; its coins and trades stay, anonymous', async () => {
  fresh();
  const p = await player();
  const coin = (await call('POST', '/paper/launch', { name: 'Forget', symbol: 'FGT', dev_buy: String(E18) }, p.key)).body.coin.id;
  assert.equal((await call('DELETE', '/paper/me', null, p.key)).status, 200);
  assert.equal((await call('GET', '/paper/me', null, p.key)).status, 401);
  assert.equal(db.pool.memeclawPaperPlayers.length, 0);
  assert.equal(db.pool.memeclawPaperHoldings.length, 0);
  const one = await call('GET', `/paper/coins/${coin}`);
  assert.equal(one.status, 200);
  assert.equal(one.body.trades.length, 1);
  assert.match(one.body.coin.creator, /^[0-9a-f]{16}$/, 'a random id, never a person');
});

test('a hidden practice coin leaves the list and cannot be traded', async () => {
  fresh();
  const p = await player();
  const coin = (await call('POST', '/paper/launch', { name: 'Hidden', symbol: 'HID' }, p.key)).body.coin.id;
  assert.equal((await call('GET', '/paper/coins')).body.coins.length, 1);
  assert.equal(await paper.setHidden(coin, 'spam'), true);
  assert.equal((await call('GET', '/paper/coins')).body.coins.length, 0);
  assert.equal((await call('GET', `/paper/coins/${coin}`)).status, 404);
  const r = await call('POST', '/paper/trade', { coin, action: 'buy', amount: String(E18) }, p.key);
  assert.equal(r.body.error, 'unknown_coin');
  assert.equal((await call('POST', `/paper/coins/${coin}/hide`, { reason: '' })).status, 401, 'the operator\'s door needs a session');
});
