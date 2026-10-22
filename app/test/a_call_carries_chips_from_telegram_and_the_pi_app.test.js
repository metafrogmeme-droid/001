'use strict';
/**
 * Chips beyond the website (lib/duel_chips.js): the bot's channel and the Pi
 * app's block. Both are thin. The bot reads the card's chips block and posts a
 * call's chips through /api/bot/sync/duel, to the same placePick the website
 * uses; the Pi block draws with the website's own renderer and posts what its
 * field held. Neither computes a stack or checks a limit of its own.
 *
 *   the channel   the card carries the block and the odds; a call's chips
 *                 ride to placePick and the stack moves; every refusal is the
 *                 service's; with the layer off the block is absent
 *   the Pi block  odds and a chips field while the card offers them, the
 *                 field's value on the POST (empty is none, never 0), the
 *                 stack in the record with what a chip is, and nothing at all
 *                 when the card carries no block
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;
delete process.env.FLOVIA_KEY;
delete process.env.FOOTBALL_DATA_TOKEN;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const { setTickerFetcher } = require('../lib/tickers');
const { setCandleFetcher } = require('../lib/candles');
const { pool } = require('../db');

const PRICES = {
  BTCUSDT: { price: 60000, volume: 900 }, ETHUSDT: { price: 3000, volume: 800 }, SOLUSDT: { price: 150, volume: 700 },
  BNBUSDT: { price: 600, volume: 600 }, XRPUSDT: { price: 0.5, volume: 500 }, ARBUSDT: { price: 1.2, volume: 50 },
};
setTickerFetcher(async () => PRICES);
setCandleFetcher(async () => []);

let server, base;
function req(method, path, { body, token, secret } = {}) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : JSON.stringify(body);
    const r = http.request(`${base}${path}`, { method, headers: {
      ...(secret ? { 'X-Bot-Secret': secret } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {}) } }, (res) => {
      let raw = ''; res.on('data', (c) => { raw += c; });
      res.on('end', () => { let json = null; try { json = JSON.parse(raw); } catch (e) { /* none */ } resolve({ status: res.statusCode, body: json }); });
    });
    r.on('error', reject); if (data) r.write(data); r.end();
  });
}
const SECRET = process.env.BOT_SYNC_SECRET;
const bot = (method, path, body) => req(method, `/api/bot/sync${path}`, { body, secret: SECRET });

async function linked(email, tg) {
  const r = await req('POST', '/api/auth/register', { body: { email, password: 'longenough1' } });
  assert.ok(r.body && r.body.token, JSON.stringify(r.body));
  const u = pool.users.find((x) => x.email === email);
  // The statement the link route itself writes (auth.js).
  await pool.execute(
    'UPDATE users SET link_token = NULL, link_token_expires = NULL, telegram_linked = TRUE, telegram_id = ? WHERE id = ?',
    [tg, u.id]);
  return u.id;
}

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', require('../auth').router);
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((resolve) => { server = app.listen(0, '127.0.0.1', () => { base = `http://127.0.0.1:${server.address().port}`; resolve(); }); });
});
test.after(() => { setTickerFetcher(null); setCandleFetcher(null); delete process.env.DUEL_CHIPS_ENABLED; if (server) server.close(); });

test('the bot channel: the card carries the chips block, a call carries chips, and the service decides', async () => {
  process.env.DUEL_CHIPS_ENABLED = '1';
  await linked('tg-chips@example.com', '880001');
  let card = await bot('GET', '/duel?telegram_id=880001');
  assert.strictEqual(card.status, 200, JSON.stringify(card.body));
  assert.strictEqual(card.body.chips.enabled, true);
  assert.deepStrictEqual([card.body.chips.min, card.body.chips.max, card.body.chips.stack.available], [10, 200, 1000]);
  const dir = card.body.rounds.filter((r) => r.kind === 'direction');
  assert.deepStrictEqual(dir[0].odds, { long: 1.9, short: 1.9 }, 'the odds ride on the round');

  const put = await bot('POST', '/duel/pick', { telegram_id: '880001', round_id: dir[0].id, pick: 'long', chips: 50 });
  assert.strictEqual(put.status, 200, JSON.stringify(put.body));
  assert.deepStrictEqual([put.body.chips, put.body.odds], [50, 1.9]);
  card = await bot('GET', '/duel?telegram_id=880001');
  assert.deepStrictEqual([card.body.chips.stack.available, card.body.chips.stack.on_table], [950, 50]);
  const mine = card.body.rounds.find((r) => r.id === dir[0].id).my_call;
  assert.deepStrictEqual(mine.chips, { chips: 50, odds: 1.9, state: 'open', payout: null });

  // Refusals are the service's, and record nothing.
  const zero = await bot('POST', '/duel/pick', { telegram_id: '880001', round_id: dir[1].id, pick: 'long', chips: 0 });
  assert.deepStrictEqual([zero.status, zero.body.reason], [400, 'chips_below_min']);
  const onPass = await bot('POST', '/duel/pick', { telegram_id: '880001', round_id: dir[1].id, pick: 'pass', chips: 25 });
  assert.deepStrictEqual([onPass.status, onPass.body.reason], [400, 'chips_on_pass']);
  const over = await bot('POST', '/duel/pick', { telegram_id: '880001', round_id: dir[1].id, pick: 'short', chips: 250 });
  assert.deepStrictEqual([over.status, over.body.reason], [400, 'chips_over_max']);
  card = await bot('GET', '/duel?telegram_id=880001');
  assert.ok(!card.body.rounds.find((r) => r.id === dir[1].id).my_call, 'no refusal recorded a call');
  // A call without chips is still a call, and carries none.
  const plain = await bot('POST', '/duel/pick', { telegram_id: '880001', round_id: dir[1].id, pick: 'short' });
  assert.strictEqual(plain.status, 200);
  assert.ok(!('chips' in plain.body));
});

test('the bot channel with the layer off: no block for a player who never put chips, and chips refused', async () => {
  delete process.env.DUEL_CHIPS_ENABLED;
  await linked('tg-off@example.com', '880002');
  const card = await bot('GET', '/duel?telegram_id=880002');
  assert.strictEqual(card.status, 200);
  assert.strictEqual(card.body.chips, null, 'no block, never a stack of 1,000 for a game not offered');
  assert.ok(card.body.rounds.every((r) => !('odds' in r)), 'no odds while the layer is off');
  const dir = card.body.rounds.find((r) => r.kind === 'direction');
  const put = await bot('POST', '/duel/pick', { telegram_id: '880002', round_id: dir.id, pick: 'long', chips: 50 });
  assert.deepStrictEqual([put.status, put.body.reason], [400, 'chips_off']);
});

// ── the Pi block ────────────────────────────────────────────────────────────

global.window = global.window || {};
global.window.RCDuel = require('../public/js/duel.js');
global.window.RCMiniView = require('../public/js/miniapp-view.js');
global.document = global.document || { hidden: true, addEventListener() {} };
const { create, recordedText } = require('../public/js/pi-duel.js');

const STACK = { season: '2026-10', float: 1000, available: 850, on_table: 150, held: 50, net: 0, put: 150,
  calls_with_chips: 2, paid: 0, open: 2, unread: 0, return_pct: null };
const BLOCK = { enabled: true, float: 1000, min: 10, max: 200, hold_hours: 2, odds_source: 'table-2026-10', stack: STACK };
const piCard = (chips) => ({ day: '2026-10-22', horizon_hours: 24, open: 1, chips, rounds: [
  { id: 1, kind: 'direction', symbol: 'BTCUSDT', options: ['long', 'short', 'pass'], callable: true, odds: chips ? { long: 1.9, short: 1.9 } : undefined },
  { id: 2, kind: 'direction', symbol: 'ETHUSDT', options: ['long', 'short', 'pass'], callable: true, agent_direction: 'short',
    my_call: { pick: 'long', pending: true, chips: { chips: 50, odds: 1.9, state: 'held', payout: null, held_until: null } } },
] });
const piMe = (chips) => ({ accuracy: { pct: null, scored: 0, unresolved: 0 }, marks: { marks: 0, pending: 1 }, streak: { current: 1 }, chips });

function piSession(chips) {
  const calls = { posts: [], toasts: [] };
  const ctx = {
    api(path, o) {
      if (path === '/api/duel/today') return Promise.resolve({ ok: true, status: 200, body: piCard(chips) });
      if (path === '/api/duel/me') return Promise.resolve({ ok: true, status: 200, body: piMe(chips) });
      if (path === '/api/duel/pick') { calls.posts.push(o.body); return Promise.resolve({ ok: true, status: 200, body: { ok: true } }); }
      return Promise.resolve({ ok: false, status: 404, body: null });
    },
    render() {}, refresh() { return Promise.resolve(); }, toast(kind, text) { calls.toasts.push([kind, text]); },
  };
  return { ctx, calls };
}
const settle = () => new Promise((r) => setTimeout(r, 30));
const tap = (block, ctx, roundId, pick, value) => {
  const input = value === undefined ? null : { value };
  const wrap = { getAttribute: () => String(roundId), querySelector: (sel) => (sel === '.d-chips-in' ? input : null) };
  const btn = { closest: () => wrap, getAttribute: () => pick };
  return block.click({ closest: (sel) => (sel.indexOf('d-btn') >= 0 ? btn : null) }, ctx);
};

test('the Pi block offers chips where the card does, and sends what the field held', async () => {
  const block = create();
  const { ctx, calls } = piSession(BLOCK);
  block.signedIn(ctx);
  await settle();
  const html = block.html();
  assert.match(html, /×1\.9/, 'each pick shows its odds');
  assert.match(html, /class="d-chips-in"[^>]*max="200"/, 'a chips field, capped at the limit');
  assert.match(html, /50 chips held until the check window closes/, 'a held call reads as held, by dispute or flag alike');
  assert.doesNotMatch(html, /disputed, paid/, 'a hold is not called a dispute');
  assert.match(html, /<span class="k">Chips<\/span><span class="v tone-muted">850<\/span><span class="d">150 on the table · 50 held<\/span>/);
  assert.match(html, /1000 a season, play money with no cash value/);
  tap(block, ctx, 1, 'long', '50');
  await settle();
  tap(block, ctx, 1, 'short', '');
  await settle();
  tap(block, ctx, 1, 'short', '  ');
  await settle();
  assert.deepStrictEqual(calls.posts, [
    { round_id: 1, pick: 'long', chips: '50' },
    { round_id: 1, pick: 'short' },
    { round_id: 1, pick: 'short' },
  ], 'empty is none, never chips: 0');
});

test('the Pi block with no chips block: no odds, no field, no stack', async () => {
  const block = create();
  const { ctx } = piSession(null);
  block.signedIn(ctx);
  await settle();
  const html = block.html();
  assert.doesNotMatch(html, /×1\.9|d-chips-in|play money/);
  assert.doesNotMatch(html, /<span class="k">Chips<\/span>/);
});

test('the Pi sentence after a call names its chips when it carried some', () => {
  const base = { ok: true, kind: 'direction', symbol: 'BTCUSDT', pick: 'long', entry_price: 100, agent_direction: 'short' };
  assert.match(recordedText({ ...base, chips: 50, odds: 1.9 }), /The Claw called SHORT\. 50 chips on the table at ×1\.9$/);
  assert.doesNotMatch(recordedText(base), /chips/);
});
