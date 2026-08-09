'use strict';
/**
 * Daily Duel — the PUBLIC surface, driven end to end.
 *
 * public_no_dollars.test.js already scans this route's source for money-shaped
 * keys. This file does the other half: it drives real players through real
 * calls and inspects the bytes that actually go out, because a §4 breach that
 * arrives through a spread (`...row`) or a stray join is invisible to a source
 * scan and perfectly visible to anyone with curl.
 */

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 'b'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const authModule = require('../auth');
const { setTickerFetcher } = require('../lib/tickers');
const { setCandleFetcher } = require('../lib/candles');
const publicDuel = require('../routes/public_duel');
const squads = require('../lib/duel_squads');
const { pool } = require('../db');

const PRICES = {
  BTCUSDT: { price: 60000, volume: 900 },
  ETHUSDT: { price: 3000, volume: 800 },
  SOLUSDT: { price: 150, volume: 700 },
  BNBUSDT: { price: 600, volume: 600 },
  XRPUSDT: { price: 0.5, volume: 500 },
};

let server, base;

function req(method, path, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : JSON.stringify(body);
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {}),
      },
    }, (res) => {
      let raw = '';
      res.on('data', (c) => { raw += c; });
      res.on('end', () => {
        let json = null;
        try { json = raw ? JSON.parse(raw) : null; } catch (e) { /* non-JSON */ }
        resolve({ status: res.statusCode, body: json, raw });
      });
    });
    r.on('error', reject);
    if (data) r.write(data);
    r.end();
  });
}

const DAY = new Date().toISOString().slice(0, 10);
const SEASON = DAY.slice(0, 7);

/** Plant a settled call directly: the board reads records, and building them
 *  through the API would need a day of real time to pass. */
async function plantCall(userId, roundId, { pick, entry, settle }) {
  await pool.execute(
    'INSERT INTO duel_picks (user_id, round_id, pick, entry_price, resolves_at,'
    + ' seal, seal_payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
    [userId, roundId, pick, entry, new Date(Date.now() - 3600000).toISOString(),
      'a'.repeat(64), '{}', new Date().toISOString()]);
  const [rows] = await pool.execute(
    'SELECT id, user_id, round_id, pick, entry_price, resolves_at, settle_price,'
    + ' settle_state, created_at FROM duel_picks');
  const row = rows[rows.length - 1];
  await pool.execute(
    'UPDATE duel_picks SET settle_price = ?, settle_state = ?, settled_at = ? WHERE id = ?',
    [settle, 'settled', new Date().toISOString(), row.id]);
}

let ROUND_IDS = [];
let CAPTAIN, CREW;

test.before(async () => {
  setTickerFetcher(async () => PRICES);
  setCandleFetcher(async () => []);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/public/duel', publicDuel);
  await new Promise((resolve) => {
    server = app.listen(0, '127.0.0.1', () => {
      base = `http://127.0.0.1:${server.address().port}`;
      resolve();
    });
  });

  // Two players, one of whom recruited the other, both opted in to the board.
  const a = await req('POST', '/api/auth/register',
    { body: { email: 'cap@example.com', password: 'longenough1' } });
  const b = await req('POST', '/api/auth/register',
    { body: { email: 'crew@example.com', password: 'longenough1' } });
  assert.ok(a.body.token && b.body.token);
  const [users] = await pool.execute('SELECT id, email FROM users WHERE email = ?', ['cap@example.com']);
  CAPTAIN = users[0].id;
  const [users2] = await pool.execute('SELECT id, email FROM users WHERE email = ?', ['crew@example.com']);
  CREW = users2[0].id;
  await pool.execute('UPDATE users SET leaderboard_handle = ? WHERE id = ?', ['captain', CAPTAIN]);
  await pool.execute('UPDATE users SET leaderboard_handle = ? WHERE id = ?', ['crewmate', CREW]);
  await pool.execute('UPDATE users SET referred_by = ? WHERE id = ?', [CAPTAIN, CREW]);

  // A card, and enough settled calls to make the captain readable.
  for (let i = 0; i < 3; i++) {
    await pool.execute(
      'INSERT IGNORE INTO duel_rounds (day, idx, symbol, agent_direction, signal_key)'
      + ' VALUES (?, ?, ?, ?, ?)', [DAY, i, ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'][i], 'short', null]);
  }
  const [rounds] = await pool.execute(
    'SELECT id, day, idx, symbol, agent_direction, signal_key FROM duel_rounds WHERE day = ? ORDER BY idx', [DAY]);
  ROUND_IDS = rounds.map((r) => r.id);

  // The captain calls all three correctly (the agent said short, it went up),
  // then two more on planted rounds to clear the readability floor.
  for (let i = 3; i < squads.MIN_SCORED + 1; i++) {
    await pool.execute(
      'INSERT IGNORE INTO duel_rounds (day, idx, symbol, agent_direction, signal_key)'
      + ' VALUES (?, ?, ?, ?, ?)', [DAY, i, `X${i}USDT`, 'short', null]);
  }
  const [allRounds] = await pool.execute(
    'SELECT id, day, idx, symbol, agent_direction, signal_key FROM duel_rounds WHERE day = ? ORDER BY idx', [DAY]);
  for (const r of allRounds) {
    await plantCall(CAPTAIN, r.id, { pick: 'long', entry: 100, settle: 110 });
  }
  // The crewmate has called once — not enough to be readable.
  await plantCall(CREW, allRounds[0].id, { pick: 'short', entry: 100, settle: 110 });
  publicDuel._resetCache();
});

test.after(() => {
  setTickerFetcher(null);
  setCandleFetcher(null);
  if (server) server.close();
});

// ── the board ───────────────────────────────────────────────────────────────

test('the board is readable without a session', async () => {
  const res = await req('GET', '/api/public/duel/board');
  assert.equal(res.status, 200);
  assert.equal(res.body.season, SEASON);
  assert.ok(res.body.rows.length >= 1);
  assert.equal(res.body.rows[0].handle, 'captain');
  assert.equal(res.body.rows[0].accuracy_pct, 100);
  assert.equal(res.body.rows[0].rank, 1);
});

test('a player under the floor is counted, never ranked at zero', async () => {
  const res = await req('GET', '/api/public/duel/board');
  assert.ok(!res.body.rows.some((r) => r.handle === 'crewmate'),
    'one call is not a record');
  assert.ok(res.body.warming_up >= 1, 'but they are still counted as playing');
  assert.equal(res.body.min_calls, squads.MIN_SCORED);
});

test('beating the agent is reported as a count', async () => {
  const res = await req('GET', '/api/public/duel/board');
  const cap = res.body.rows.find((r) => r.handle === 'captain');
  // The agent called short on every round and every one went up.
  assert.equal(cap.beat_agent, cap.correct);
  assert.equal(cap.marks, cap.correct * 2);
});

// ── squads ──────────────────────────────────────────────────────────────────

test('squads are built from the referral graph', async () => {
  const res = await req('GET', '/api/public/duel/squads');
  assert.equal(res.status, 200);
  const cap = res.body.rows.find((r) => r.captain === 'captain');
  assert.ok(cap, 'the captain leads a squad');
  assert.equal(cap.size, 2, 'the captain plus the player who joined on their code');
  assert.equal(cap.readable_members, 1);
  assert.equal(cap.accuracy_pct, 100);
  assert.ok(!res.body.rows.some((r) => r.captain === 'crewmate'),
    'a recruit with no recruits of their own is not also a squad');
});

// ── refusals and §4 ─────────────────────────────────────────────────────────

test('a malformed season is refused, not silently swapped for this one', async () => {
  for (const bad of ['2026', 'august', '2026-8-1', "2026-08'; DROP"]) {
    const res = await req('GET', `/api/public/duel/board?season=${encodeURIComponent(bad)}`);
    assert.equal(res.status, 400, `"${bad}" must be refused`);
  }
  const good = await req('GET', '/api/public/duel/board?season=2020-01');
  assert.equal(good.status, 200);
  assert.deepEqual(good.body.rows, [], 'a season nobody played is empty, not an error');
});

test('no identifier and no amount of any currency rides the public board', async () => {
  const board = await req('GET', '/api/public/duel/board');
  const squadRes = await req('GET', '/api/public/duel/squads');
  for (const raw of [board.raw, squadRes.raw]) {
    for (const banned of ['user_id', 'email', '@example.com', 'referred_by',
      'balance', 'equity', 'margin', 'pnl', 'notional', 'usd_value', 'vusdt',
      'stake', 'wager']) {
      assert.equal(raw.toLowerCase().includes(banned.toLowerCase()), false,
        `"${banned}" must not appear on a public duel payload`);
    }
  }
  // Only the anonymous handles the players chose.
  assert.ok(board.raw.includes('captain'));
});

test('the public board never carries the agent\'s pending stance', async () => {
  // A player could otherwise read tomorrow's answer off a public page.
  const board = await req('GET', '/api/public/duel/board');
  const squadRes = await req('GET', '/api/public/duel/squads');
  for (const raw of [board.raw, squadRes.raw]) {
    assert.equal(/agent_direction/.test(raw), false);
    assert.equal(/signal_key/.test(raw), false);
  }
});
