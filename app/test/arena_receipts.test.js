'use strict';
/**
 * Provable Calls v2 — Arena trader receipts. Every paper trade is sealed at
 * OPEN time (before anyone knows how it ends); the seal rides the position
 * onto the closed-trade row untouched, and /call/:key lets anyone re-derive
 * the SHA-256 in their own browser. §4: the sealed payload and the public
 * verify feed carry prices / leverage / percent / times ONLY — margin and
 * pnl (virtual dollars) never appear on a public surface.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');

const authModule = require('../auth');
const { setTickerFetcher } = require('../lib/tickers');
const { pool } = require('../db');
const { sealArenaTrade, canonicalArenaPayload, newTradeKey, sealOf } = require('../lib/callseal');

let server, base;
let PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 } };

test.before(async () => {
  setTickerFetcher(async () => PRICES);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/arena', require('../routes/arena'));
  app.use('/api/call', require('../routes/call'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); setTickerFetcher(null); });

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, { method, headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(payload ? { 'Content-Type': 'application/json' } : {}) } }, (res) => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {}, raw: d }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

test('the arena seal is §4-clean: prices/leverage/times only, no margin, no amounts', () => {
  const t = { trade_key: 'arena:abc123def456ghi789', handle: 'runelord', symbol: 'BTCUSDT',
    direction: 'LONG', entry: 100, leverage: 5, tp: 110, sl: 95,
    opened_at: '2026-07-25T10:00:00.000Z', margin: 250, pnl: 999 };
  const a = sealArenaTrade(t);
  assert.equal(a.seal.length, 64);
  assert.equal(sealOf(a.seal_payload), a.seal, 'self-consistent');
  assert.ok(!a.seal_payload.includes('margin'), 'margin (vUSDT) never enters the payload');
  assert.ok(!a.seal_payload.includes('pnl'), 'outcome never enters the payload');
  assert.ok(!a.seal_payload.includes('250'), 'the margin VALUE is absent too');
  // Outcome-ish fields cannot move the seal; decision facts do.
  assert.equal(sealArenaTrade({ ...t, margin: 999, pnl: -1 }).seal, a.seal);
  assert.notEqual(sealArenaTrade({ ...t, entry: 100.1 }).seal, a.seal);
  assert.notEqual(sealArenaTrade({ ...t, handle: null }).seal, a.seal, 'the handle is bound in');
  assert.match(canonicalArenaPayload(t), /^\{"v":2,"kind":"arena_trade","trade_key":/);
  // Keys are unguessable and well-formed.
  const k1 = newTradeKey(), k2 = newTradeKey();
  assert.match(k1, /^arena:[0-9a-f]{18}$/);
  assert.notEqual(k1, k2);
});

let token, tradeKey;

test('opening a paper trade seals it at fill time', async () => {
  const reg = await req('POST', '/api/auth/register', { body: { email: 'receipts1@example.com', password: 'longenough1' } });
  token = reg.data.token;
  // Opt into a handle FIRST so the receipt binds it at open time.
  const me = pool.users.find((u) => u.email === 'receipts1@example.com');
  me.leaderboard_handle = 'sealbearer';
  PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 } };
  const open = await req('POST', '/api/arena/open', { token,
    body: { symbol: 'BTCUSDT', direction: 'LONG', margin: 100, leverage: 5, tp: 110, sl: 95 } });
  assert.equal(open.status, 200);
  tradeKey = open.data.filled.key;
  assert.match(tradeKey, /^arena:[0-9a-f]{18}$/, 'the fill hands back the receipt address');
  const row = pool.arenaPositions.find((p) => p.trade_key === tradeKey);
  assert.ok(row.seal && row.seal_payload && row.sealed_at, 'sealed on insert');
  assert.equal(sealOf(row.seal_payload), row.seal);
  const p = JSON.parse(row.seal_payload);
  assert.equal(p.handle, 'sealbearer');
  assert.equal(p.entry, 100);
  assert.equal(p.leverage, 5);
  assert.ok(!row.seal_payload.includes('margin'));
  // The private account view carries the key so the owner can share it.
  const acct = await req('GET', '/api/arena/account', { token });
  assert.equal(acct.data.positions[0].key, tradeKey);
});

test('/api/call verifies an OPEN paper trade — public, §4-clean, drift-free', async () => {
  const r = await req('GET', '/api/call/' + encodeURIComponent(tradeKey));
  assert.equal(r.status, 200);
  const d = r.data;
  assert.equal(d.kind, 'arena_trade');
  // The browser-side WebCrypto math, mirrored with node crypto:
  const hex = crypto.createHash('sha256').update(d.seal_payload, 'utf8').digest('hex');
  assert.equal(hex, d.seal, 'sha256(seal_payload) === seal');
  const p = JSON.parse(d.seal_payload);
  for (const f of ['symbol', 'direction', 'entry', 'leverage', 'tp', 'sl']) {
    assert.deepEqual(p[f], d.current[f], `no drift on ${f}`);
  }
  assert.equal(d.outcome.status, 'OPEN', 'sealed before the outcome is known');
  // §4: no virtual dollars anywhere in the public verify feed.
  assert.ok(!r.raw.includes('"margin"'), 'no margin on the public feed');
  assert.ok(!r.raw.includes('"pnl"'), 'no dollar pnl on the public feed');
});

test('closing attaches a percent outcome to the SAME receipt — the seal survives', async () => {
  const before = pool.arenaPositions.find((p) => p.trade_key === tradeKey);
  const sealBefore = before.seal;
  PRICES = { BTCUSDT: { price: 104, change: 0, volume: 1 } };
  const acct = await req('GET', '/api/arena/account', { token });
  const posId = acct.data.positions[0].id;
  const close = await req('POST', '/api/arena/close', { token, body: { position_id: posId } });
  assert.equal(close.status, 200);
  const trade = pool.arenaTrades.find((t) => t.trade_key === tradeKey);
  assert.equal(trade.seal, sealBefore, 'the seal rides onto the closed trade untouched');
  assert.equal(trade.seal_payload, before.seal_payload);
  const r = await req('GET', '/api/call/' + encodeURIComponent(tradeKey));
  assert.equal(r.data.kind, 'arena_trade');
  assert.equal(r.data.outcome.status, 'CLOSED');
  // +4% price × 5× leverage = +20% on margin — percent only, no dollars.
  assert.equal(r.data.outcome.pct, 20);
  assert.equal(r.data.outcome.exit_price, 104);
  assert.ok(!r.raw.includes('"margin"') && !r.raw.includes('"pnl"'), '§4 holds after close');
  // Closed rows hold no tp/sl columns — current omits them, so the verify
  // page never flags a FALSE rewrite on a field the row no longer carries.
  assert.ok(!('tp' in r.data.current) && !('sl' in r.data.current));
  const hex = crypto.createHash('sha256').update(r.data.seal_payload, 'utf8').digest('hex');
  assert.equal(hex, r.data.seal, 'still verifies after resolution');
});

test('receipts surface publicly: tape 🔏 keys and the trader card counts', async () => {
  const tape = await req('GET', '/api/arena/tape');
  const print = tape.data.rows.find((x) => x.handle === 'sealbearer');
  assert.ok(print, 'the close printed on the tape');
  assert.equal(print.key, tradeKey, 'the tape print links its receipt');
  const card = await req('GET', '/api/arena/trader/sealbearer');
  assert.deepEqual(card.data.receipts, { sealed: 1, total: 1 });
  assert.equal(card.data.recent[0].key, tradeKey);
  assert.ok(!JSON.stringify(card.data).includes('"margin"'), 'trader card stays §4-clean');
});

test('wiring: verify page renders arena receipts; 🔏 links on arena, trader, tape', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'call.html'), 'utf8');
  assert.match(page, /arena_trade/);
  assert.match(page, /% on margin/);
  assert.match(page, /Sealed at/);
  const arenaHtml = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.ok(arenaHtml.split('/call/').length >= 4, 'position + history + tape rows all link receipts');
  const traderHtml = fs.readFileSync(path.join(__dirname, '..', 'public', 'trader.html'), 'utf8');
  assert.match(traderHtml, /receipt-backed/);
  assert.match(traderHtml, /\/call\//);
});
