'use strict';
/**
 * Provable Calls on the BOARD — the public leaderboard advertises which
 * records are receipt-backed. §4: counts only (never an amount), and a
 * trader with zero sealed closes gets no badge rather than a hollow one.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');

const { pool } = require('../db');
const { setTickerFetcher } = require('../lib/tickers');

let server, base;
test.before(async () => {
  setTickerFetcher(async () => ({ BTCUSDT: { price: 100, change: 0, volume: 1 } }));
  const app = express();
  app.use('/api/arena', require('../routes/arena'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); setTickerFetcher(null); });

const get = (p) => new Promise((resolve, reject) => {
  http.get(`${base}${p}`, (res) => {
    let d = ''; res.on('data', (c) => d += c);
    res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {}, raw: d }));
  }).on('error', reject);
});

test('board rows carry receipt counts — sealed, unsealed and mixed records', async () => {
  // Three traders: fully receipt-backed, partially, and none at all.
  pool.users.push({ id: 8801, leaderboard_handle: 'sealed_all' },
                  { id: 8802, leaderboard_handle: 'sealed_some' },
                  { id: 8803, leaderboard_handle: 'legacy_none' });
  for (const id of [8801, 8802, 8803]) pool.arenaAccounts[id] = { user_id: id, balance: 1000 };
  const close = (user_id, seal) => pool.arenaTrades.push({
    id: pool._nextArenaTradeId++, user_id, symbol: 'BTCUSDT', direction: 'LONG',
    entry: 100, exit_price: 101, margin: 50, leverage: 2, pnl: 1, reason: 'tp',
    trade_key: seal ? 'arena:' + user_id + seal : null, seal: seal || null,
    seal_payload: seal ? '{}' : null, sealed_at: seal ? new Date() : null,
    opened_at: new Date(), closed_at: new Date(),
  });
  close(8801, 'a'); close(8801, 'b');          // 2/2 sealed
  close(8802, 'c'); close(8802, null);         // 1/2 sealed
  close(8803, null); close(8803, null);        // 0/2 sealed

  const r = await get('/api/arena/leaderboard');
  assert.equal(r.status, 200);
  const by = Object.fromEntries(r.data.rows.map((x) => [x.handle, x]));
  assert.deepEqual([by.sealed_all.sealed, by.sealed_all.closes], [2, 2], 'fully backed');
  assert.deepEqual([by.sealed_some.sealed, by.sealed_some.closes], [1, 2], 'partially backed');
  assert.deepEqual([by.legacy_none.sealed, by.legacy_none.closes], [0, 2], 'pre-receipt record');
  // §4 holds on this PUBLIC surface: no balances, no vUSDT amounts.
  assert.ok(!r.raw.includes('"balance"') && !r.raw.includes('"pnl"'), 'counts and percent only');
});

test('the board renders the badge honestly (full vs partial vs none)', () => {
  const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  // A fully-backed record reads differently from a partial one at a glance.
  assert.match(arena, /\.lb-seal \{[^}]*color: var\(--text-3\)/);
  assert.match(arena, /\.lb-seal\.full \{ color: var\(--gold-bright\)/);
  // The explanation is dictionary-backed with its placeholders intact. Only
  // the SET is invariant — word order is the translator's call (zh naturally
  // reads "{m} closes, of which {n} carry a receipt").
  const i18n = require('../public/js/i18n');
  const e = i18n.STRINGS['arena.d_seal_title'];
  assert.ok(e, 'arena.d_seal_title missing from the dictionary');
  const slots = (s) => [...new Set(s.match(/\{\w+\}/g) || [])].sort().join(',');
  for (const c of i18n.LANGS.map((l) => l.code)) {
    assert.equal(slots(e[c]), '{m},{n}', `${c} lost a placeholder`);
  }
});

test('season standings carry the same receipt badges as the all-time board', async () => {
  const seasons = require('../lib/arena_seasons');
  // The pure ranking counts receipt-backed closes per trader.
  const rows = seasons.seasonRanking([
    { user_id: 1, pnl: 10, seal: 'a' }, { user_id: 1, pnl: 5, seal: 'b' },
    { user_id: 2, pnl: 8, seal: 'c' }, { user_id: 2, pnl: 1, seal: null },
    { user_id: 3, pnl: 4, seal: null },
  ], new Map([[1, 'all'], [2, 'some'], [3, 'none']]));
  const by = Object.fromEntries(rows.map((r) => [r.handle, r]));
  assert.deepEqual([by.all.sealed, by.all.closes], [2, 2]);
  assert.deepEqual([by.some.sealed, by.some.closes], [1, 2]);
  assert.deepEqual([by.none.sealed, by.none.closes], [0, 1]);
  // The route must actually SELECT the column, or every badge silently vanishes.
  const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8');
  assert.ok(!/SELECT user_id, pnl FROM arena_trades WHERE closed_at/.test(route),
    'season queries must select seal too');
  assert.equal((route.match(/SELECT user_id, pnl, seal FROM arena_trades WHERE closed_at/g) || []).length, 2,
    'both the live season and the Hall of Champions read seal');
});

test('one badge builder feeds both boards — they can never drift apart', () => {
  const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(arena, /function sealBadge\(x\) \{/);
  // Used by BOTH the all-time board row and the season standings row.
  assert.equal((arena.match(/\+ sealBadge\(x\)|= sealBadge\(x\);/g) || []).length, 2,
    'both board rows call the one helper');
  // The honesty rules live in the one helper: nothing when unsealed, ratio
  // only when partial, solid only when complete.
  assert.match(arena, /if \(sealed <= 0\) return '';/);
  assert.match(arena, /var full = closes > 0 && sealed >= closes;/);
});
