'use strict';
/**
 * At settlement the duel also reads the same instant's price from CoinGecko
 * through Flovia (lib/second_price.js) and seals what it found beside the
 * venue's close: `second_source` and `disputed` ride inside the v7 receipt's
 * hashed bytes. The venue's close settles the round whatever the second
 * source says; a disagreement past the tolerance is said, on the receipt and
 * on the surfaces, and never re-decides the verdict.
 *
 * Three values on `disputed`: true (a read leg disagreed), false (every leg
 * read and agreed), null (not checked: no key, a refusal, no point near the
 * horizon). Both price sources are stubs; the settlement runs for real.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);
delete process.env.DATABASE_URL;
delete process.env.DUEL_SETTLE_TOLERANCE_PCT;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const flovia = require('../lib/flovia');
const secondPrice = require('../lib/second_price');
const svc = require('../lib/duel_service');
const duel = require('../lib/duel');
const receipts = require('../lib/duel_receipts');
const { setCandleFetcher } = require('../lib/candles');
const { pool } = require('../db');

const sha256 = (s) => crypto.createHash('sha256').update(s, 'utf8').digest('hex');

// A stub Flovia: CoinGecko search/coins/market_chart from fixtures.
let series = {};        // id -> [[ts, price], ...]
let refuse = false;
let lookups = [];
function reply(msg, data, isError) {
  return { status: 200, ok: true, headers: { get: (k) => (k === 'content-type' ? 'application/json' : k === 'mcp-session-id' ? 's' : null) },
    text: async () => JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { isError: !!isError, content: [{ type: 'text', text: JSON.stringify(data) }] } }) };
}
flovia.setFetch(async (url, init) => {
  const msg = JSON.parse(init.body);
  if (msg.method === 'initialize') return { ...reply(msg, {}), text: async () => JSON.stringify({ jsonrpc: '2.0', id: msg.id, result: { protocolVersion: '2025-06-18' } }) };
  if (msg.method === 'notifications/initialized') return { status: 202, ok: true, headers: { get: () => null }, text: async () => '' };
  const a = msg.params.arguments; lookups.push(a);
  if (refuse) return reply(msg, { explanation: { meaning: 'window spent' } }, true);
  if (a.path === '/search') {
    const q = a.query.query;
    return reply(msg, { coins: q === 'ZZZ' ? [] : [{ id: q.toLowerCase() + '-coin', symbol: q, market_cap_rank: 10 }] });
  }
  if (/^\/coins\/[^/]+$/.test(a.path)) return reply(msg, { id: a.path.slice(7), platforms: { ethereum: '0xabc' } });
  if (/market_chart$/.test(a.path)) {
    const id = decodeURIComponent(a.path.split('/')[2]);
    return reply(msg, { prices: series[id] || [] });
  }
  return reply(msg, { error: 'unexpected' }, true);
});

// A past instant on the hour: two hours ago, floored, so the call is due
// whenever this runs and the venue's bucket is the horizon's own hour.
const HORIZON = Math.floor((Date.now() - 2 * 3600000) / 3600000) * 3600000;
const bucket = String(Math.floor(HORIZON / 3600000) * 3600000);
let nextId = 1;
function round(symbol, kind = 'direction', extra = {}) {
  const r = { id: nextId++, day: '2026-10-20', idx: nextId, kind, symbol, symbol_b: null, threshold_pct: null,
    agent_direction: 'long', seal: 'a'.repeat(64), created_at: new Date(HORIZON - 3600000).toISOString(), ...extra };
  pool.duelRounds.push(r);
  return r;
}
function pick(r, entry, pickWord = 'long', extra = {}) {
  const p = { id: nextId++, user_id: 1, round_id: r.id, pick: pickWord, entry_price: entry, entry_price_b: null,
    resolves_at: new Date(HORIZON).toISOString(), settle_price: null, settle_price_b: null, settle_state: null,
    settled_at: null, seal: 'b'.repeat(64), seal_payload: '{}', created_at: new Date(HORIZON - 3600000).toISOString(), ...extra };
  pool.duelPicks.push(p);
  return p;
}
function venue(closes) {  // symbol -> close
  setCandleFetcher(async (sym) => (closes[sym] != null ? [[bucket, '1', '2', '0', String(closes[sym]), '1', '1']] : []));
}
test.afterEach(() => { setCandleFetcher(null); lookups = []; refuse = false; });

test('the second source agrees: sealed beside the close, disputed false, verdict from the venue', async () => {
  process.env.FLOVIA_KEY = 'flv_test';
  series['btc-coin'] = [[HORIZON - 10 * 60000, 104.9], [HORIZON - 5 * 60000, 105.05], [HORIZON + 5 * 60000, 200]];
  const r = round('BTCUSDT');
  const p = pick(r, 100);
  venue({ BTCUSDT: 105 });
  assert.equal(await svc.settleDue([r], [p]), 1);
  assert.equal(p.settle_state, 'settled');
  assert.equal(p.settle_price, 105);
  assert.equal(p.settle_second_price, 105.05, 'the last point at or before the horizon');
  assert.strictEqual(p.settle_disputed, 0);
  const sp = JSON.parse(p.settle_payload);
  assert.equal(sha256(p.settle_payload), p.settle_seal);
  assert.deepStrictEqual(sp.second_source, { provider: 'CoinGecko', price: 105.05, source_at: new Date(HORIZON - 5 * 60000).toISOString() });
  assert.strictEqual(sp.second_source_b, null);
  assert.strictEqual(sp.disputed, false);
  assert.equal(sp.result, 'correct');
  const pub = duel.publicPick(p, { round: r });
  assert.strictEqual(pub.disputed, false);
  const rec = await receipts.settleReceipt(p, r);
  assert.deepStrictEqual([rec.current.second_price, rec.current.disputed], [105.05, false]);
});

test('the second source disagrees past the tolerance: disputed true, the verdict unchanged', async () => {
  process.env.FLOVIA_KEY = 'flv_test';
  series['eth-coin'] = [[HORIZON - 60000, 96]];
  const r = round('ETHUSDT');
  const p = pick(r, 100);
  venue({ ETHUSDT: 105 });
  await svc.settleDue([r], [p]);
  assert.equal(p.settle_price, 105, 'the venue still settles');
  assert.strictEqual(p.settle_disputed, 1);
  const sp = JSON.parse(p.settle_payload);
  assert.strictEqual(sp.disputed, true);
  assert.equal(sp.result, 'correct', 'a dispute changes no verdict');
  assert.equal(sp.second_source.price, 96);
  assert.strictEqual(duel.publicPick(p, { round: r }).disputed, true);
  // The tolerance is the operator's: at 10% the same pair agrees.
  process.env.DUEL_SETTLE_TOLERANCE_PCT = '10';
  assert.strictEqual(secondPrice.disputed(105, { read: true, price: 96 }, secondPrice.tolerancePct()), false);
  delete process.env.DUEL_SETTLE_TOLERANCE_PCT;
  assert.strictEqual(secondPrice.disputed(105, { read: true, price: 96 }, secondPrice.tolerancePct()), true);
  assert.strictEqual(secondPrice.disputed(105, { read: false, reason: 'refused' }, 0.5), null);
  assert.strictEqual(secondPrice.disputed(0, { read: true, price: 96 }, 0.5), null, 'a zero close is not a price to compare');
});

test('no second reading (no key, a refusal, no point near the horizon, an unknown coin): not checked, still settled', async () => {
  delete process.env.FLOVIA_KEY;
  let r = round('SOLUSDT'); let p = pick(r, 100); venue({ SOLUSDT: 105 });
  await svc.settleDue([r], [p]);
  assert.deepStrictEqual([p.settle_state, p.settle_price, p.settle_second_price, p.settle_disputed], ['settled', 105, null, null]);
  let sp = JSON.parse(p.settle_payload);
  assert.deepStrictEqual([sp.second_source, sp.disputed], [null, null]);
  assert.strictEqual(duel.publicPick(p, { round: r }).disputed, null);
  assert.strictEqual((await receipts.settleReceipt(p, r)).current.disputed, null);
  assert.equal(lookups.length, 0, 'no key, no lookup');

  process.env.FLOVIA_KEY = 'flv_test';
  refuse = true;
  r = round('XRPUSDT'); p = pick(r, 100); venue({ XRPUSDT: 105 });
  await svc.settleDue([r], [p]);
  assert.deepStrictEqual([p.settle_state, p.settle_disputed], ['settled', null]);
  refuse = false;

  series['ada-coin'] = [[HORIZON - 3 * 3600000, 105]];   // three hours stale: no point
  r = round('ADAUSDT'); p = pick(r, 100); venue({ ADAUSDT: 105 });
  await svc.settleDue([r], [p]);
  assert.deepStrictEqual([p.settle_state, p.settle_disputed], ['settled', null]);

  r = round('ZZZUSDT'); p = pick(r, 100); venue({ ZZZUSDT: 105 });
  await svc.settleDue([r], [p]);
  assert.deepStrictEqual([p.settle_state, p.settle_disputed], ['settled', null]);
});

test('a mover checks both legs: one disagreeing leg disputes, an unread leg beside an agreeing one is not fully checked', async () => {
  process.env.FLOVIA_KEY = 'flv_test';
  series['arb-coin'] = [[HORIZON - 60000, 110]];
  series['op-coin'] = [[HORIZON - 60000, 150]];     // far from the venue's 120
  let r = round('ARBUSDT', 'mover', { symbol_b: 'OPUSDT' });
  let p = pick(r, 100, 'first', { entry_price_b: 100 });
  venue({ ARBUSDT: 110, OPUSDT: 120 });
  await svc.settleDue([r], [p]);
  assert.equal(p.settle_state, 'settled');
  assert.deepStrictEqual([p.settle_second_price, p.settle_second_price_b, p.settle_disputed], [110, 150, 1]);
  const sp = JSON.parse(p.settle_payload);
  assert.equal(sp.second_source_b.price, 150);
  assert.strictEqual(sp.disputed, true);

  series['link-coin'] = [[HORIZON - 60000, 110]];
  series['dot-coin'] = [];                             // nothing for the second leg
  r = round('LINKUSDT', 'mover', { symbol_b: 'DOTUSDT' });
  p = pick(r, 100, 'first', { entry_price_b: 100 });
  venue({ LINKUSDT: 110, DOTUSDT: 120 });
  await svc.settleDue([r], [p]);
  assert.deepStrictEqual([p.settle_second_price, p.settle_second_price_b, p.settle_disputed], [110, null, null]);
});

test('the surfaces say disputed only for true', () => {
  const D = require('../public/js/duel.js');
  const tr = (k, fb) => fb;
  const yes = D.callState({ state: 'correct', disputed: true }, tr);
  assert.equal(yes.text, 'Correct · disputed');
  assert.match(yes.note, /second price source disagreed/);
  assert.equal(yes.tone, 'up', 'the verdict keeps its colour');
  assert.equal(D.callState({ state: 'correct', disputed: false }, tr).text, 'Correct');
  assert.equal(D.callState({ state: 'wrong', disputed: null }, tr).text, 'Wrong');
  assert.equal(D.callState({ state: 'wrong' }, tr).text, 'Wrong');
  const push = D.callState({ state: 'push', disputed: true }, tr);
  assert.equal(push.text, 'No result · disputed');
  assert.match(push.note, /Counts for neither side\. A second price source/);
  assert.equal(D.callState({ pending: true, disputed: true }, tr).text, 'Running', 'a running call cannot be disputed');
});
