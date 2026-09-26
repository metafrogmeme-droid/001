'use strict';
/**
 * Practice-follow never opened a position and the Daily Duel never used the
 * agent's calls, because a signal row and the ticker map spell a symbol two
 * ways.
 *
 * The scanner writes a signal as 'SOL/USDT' (sometimes 'SOL/USDT:USDT'); the
 * ticker map, the arena's positions and the duel's rounds are keyed 'SOLUSDT'.
 * Only one door normalised -- POST /api/arena/open-signal, whose comment names
 * the dialects -- and three readers looked the raw row up in the marks:
 *
 *   * the follow sweep (GET /api/arena/account) found no mark for any engine
 *     signal, skipped each as `no_mark` and ADVANCED THE CURSOR past it, so a
 *     signal it could not price was skipped for good;
 *   * the duel's round builder priced none of the agent's calls, so every card
 *     was topped up with majors the Claw "passed" on, on days it had called
 *     PENDLE and ARB;
 *   * the signal picker printed mark null, drift null and `tradeable: true`
 *     beside a SOL position already open.
 *
 * Driven on 2026-09-26 through the real routers and the in-memory database.
 * One normalisation now (`agent_match.exchangeSymbol`), and every reader asks
 * it; the tests plant the scanner's spelling because every earlier fixture
 * planted the exchange one, which is the one case where the raw read worked.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 'b'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const { pool } = require('../db');
const { setTickerFetcher } = require('../lib/tickers');
const { setCandleFetcher } = require('../lib/candles');
const { exchangeSymbol } = require('../lib/agent_match');
const { planFollows } = require('../lib/arena_follow');
const { decorateForPicker } = require('../lib/arena_signal_trade');
const duel = require('../lib/duel');

const PRICES = {
  BTCUSDT: { price: 60000, change: 1, volume: 9e9 },
  ETHUSDT: { price: 3000, change: 1, volume: 5e9 },
  SOLUSDT: { price: 150, change: 1, volume: 3e9 },
  BNBUSDT: { price: 600, change: 1, volume: 1e9 },
  XRPUSDT: { price: 0.6, change: 1, volume: 1e9 },
  PENDLEUSDT: { price: 3.5, change: 4, volume: 5e7 },
  ARBUSDT: { price: 1.1, change: 4, volume: 5e7 },
};

let server, base;

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

let n = 0;
async function register() {
  const r = await req('POST', '/api/auth/register',
    { body: { email: `spell${++n}_${Date.now()}@test.io`, password: 'x'.repeat(12) } });
  assert.ok(r.data.token, JSON.stringify(r.data));
  return { token: r.data.token, id: r.data.user_id };
}

async function signal(key, symbol, direction, conf = 0.8) {
  await pool.execute(
    `INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern,
       regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl, created_at, resolved_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [key, symbol, direction, conf, conf, 'breakout', 'TREND', 149, 140, 170, 2.2, null,
     'NEW', null, new Date().toISOString(), '']);
}

test.before(async () => {
  global.fetch = async (url) => { throw new Error(`network disabled in test: ${url}`); };
  setTickerFetcher(async () => PRICES);
  setCandleFetcher(async () => []);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/arena', require('../routes/arena'));
  app.use('/api/duel', require('../routes/duel'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('the one normalisation reads every dialect the scanner writes', () => {
  for (const s of ['SOL/USDT', 'SOL/USDT:USDT', 'SOLUSDT', 'sol/usdt', ' SOL/USDT ', 'SOL']) {
    assert.equal(exchangeSymbol(s), 'SOLUSDT', s);
  }
  for (const s of ['', null, undefined, '/USDT']) {
    assert.equal(exchangeSymbol(s), '', `no base, no key: ${JSON.stringify(s)}`);
  }
});

test('practice-follow opens a scanner-spelled signal at the live mark', async () => {
  const u = await register();
  assert.equal((await req('GET', '/api/arena/account', { token: u.token })).status, 200);
  const on = await req('POST', '/api/arena/follow',
    { token: u.token, body: { enabled: true, margin: 100, leverage: 3 } });
  assert.equal(on.status, 200, JSON.stringify(on.data));
  await signal('fol-sol', 'SOL/USDT', 'LONG');

  const r = await req('GET', '/api/arena/account', { token: u.token });
  assert.equal(r.status, 200);
  const syms = (r.data.positions || []).map((p) => p.symbol);
  assert.deepEqual(syms, ['SOLUSDT'],
    'the follow sweep skipped a SOL/USDT signal as no_mark and moved its cursor past it');
  const pos = r.data.positions[0];
  assert.equal(Number(pos.entry), 150, 'the fill is the live mark, not the signal price');

  // The picker, for the same reader, now finds the mark and the open position.
  const pick = await req('GET', '/api/arena/signals', { token: u.token });
  const row = pick.data.signals.find((s) => s.id === pick.data.signals[0].id);
  assert.equal(row.symbol, 'SOLUSDT');
  assert.equal(row.mark, 150, 'the picker printed no mark for an engine signal');
  assert.equal(row.drift_pct, 0.67);
  assert.equal(row.tradeable, false);
  assert.equal(row.blocked_reason, 'already_open',
    'the picker called SOL tradeable beside a SOL position already open');
});

test('opening one named signal reads the colon dialect at its door', async () => {
  // The door that already normalised had no drive of its own: the mutation
  // round put the raw spelling back there and every suite stayed green. It is
  // one normalisation now, and this is the drive of it.
  const u = await register();
  assert.equal((await req('GET', '/api/arena/account', { token: u.token })).status, 200);
  await signal('open-eth', 'ETH/USDT:USDT', 'LONG');
  const [rows] = await pool.execute(
    'SELECT id, signal_key FROM signals WHERE signal_key = ?', ['open-eth']);
  const r = await req('POST', '/api/arena/open-signal',
    { token: u.token, body: { signal_id: rows[0].id, margin: 50, leverage: 2 } });
  assert.equal(r.status, 200, JSON.stringify(r.data));
  assert.equal(r.data.filled.symbol, 'ETHUSDT');
  assert.equal(r.data.filled.entry, 3000, 'the fill is the live ETH mark');
});

test('the follow planner and the picker read the colon dialect too', () => {
  const plan = planFollows({
    signals: [{ id: 7, symbol: 'ARB/USDT:USDT', direction: 'SHORT' }],
    positions: [], balance: 1000, prefs: { margin: 50, leverage: 2 }, marks: PRICES,
  });
  assert.deepEqual(plan.skips, []);
  assert.equal(plan.opens.length, 1);
  assert.equal(plan.opens[0].symbol, 'ARBUSDT');
  assert.equal(plan.opens[0].price, 1.1);

  const [row] = decorateForPicker(
    [{ id: 1, symbol: 'PENDLE/USDT:USDT', direction: 'LONG', entry_price: 3.5,
       created_at: new Date().toISOString() }],
    { positions: [{ symbol: 'PENDLEUSDT' }], marks: PRICES });
  assert.equal(row.mark, 3.5);
  assert.equal(row.blocked_reason, 'already_open');

  // A row whose symbol names no base still finds no mark -- and says so as a
  // skip, rather than being matched to something.
  const none = planFollows({
    signals: [{ id: 8, symbol: '', direction: 'LONG' }],
    positions: [], balance: 1000, prefs: { margin: 50, leverage: 2 }, marks: PRICES,
  });
  assert.deepEqual(none.skips, [{ signal_id: 8, reason: 'no_mark' }]);
});

test("the duel card is built from the agent's calls in the scanner's spelling", async () => {
  // The in-memory shim's own tables: no card yet today, and only these two
  // calls on record, so the card must lead with them before topping up.
  assert.ok(Array.isArray(pool.duelRounds) && Array.isArray(pool.signals));
  pool.duelRounds.length = 0;
  pool.signals.length = 0;
  await signal('d-pendle', 'PENDLE/USDT', 'LONG', 0.9);
  await signal('d-arb', 'ARB/USDT', 'SHORT', 0.8);
  const u = await register();
  const card = await req('GET', '/api/duel/today', { token: u.token });
  assert.equal(card.status, 200, JSON.stringify(card.data));
  assert.deepEqual(card.data.rounds.map((r) => r.symbol).slice(0, 2), ['PENDLEUSDT', 'ARBUSDT'],
    'the card was topped up with majors while the agent had called PENDLE and ARB');
  const stored = (pool.duelRounds || []).map((r) => [r.symbol, r.agent_direction, r.signal_key]);
  assert.deepEqual(stored.slice(0, 2),
    [['PENDLEUSDT', 'long', 'd-pendle'], ['ARBUSDT', 'short', 'd-arb']]);
});

test('buildRounds prices a scanner-spelled call and does not list a major twice', () => {
  const day = new Date().toISOString().slice(0, 10);
  const rounds = duel.buildRounds(day,
    [{ signal_key: 's', symbol: 'SOL/USDT', direction: 'LONG', confidence: 0.7 }], PRICES);
  assert.equal(rounds[0].symbol, 'SOLUSDT');
  assert.equal(rounds[0].agent_direction, 'long');
  assert.equal(rounds.filter((r) => r.symbol === 'SOLUSDT').length, 1,
    'the call and the major top-up are one market');
});
