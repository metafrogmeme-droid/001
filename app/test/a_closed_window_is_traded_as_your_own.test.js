'use strict';
// "Trade it yourself": a call whose window closed before it ended.
//
// A signal is live for the five minutes the bot states, and the Pi app only
// offered its paper button inside them, so a member who opened the app later
// saw every card with no action at all. Re-opening the follow past the window
// would put the engine's name on a call it no longer stands behind
// (arena_signal_trade.js, rule 3). The answer the user chose: past the window
// and not yet ended, the member may take the call's side as THEIR OWN trade,
// at the live mark, not credited to the call (rule 5).
//
// What that has to mean, each driven on the real route:
//   - one call has one door at a time: live → follow only; window closed and
//     not ended → own trade only; ended → neither;
//   - the own trade is a manual position: no signal key, no agent, so no
//     record of the call counts it, and the manual open's gates apply (the
//     armed envelope refuses it as it refuses a typed ticket);
//   - it carries the call's exits only where they are still on the right
//     side of the live mark, and says which it dropped.
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const st = require('../lib/arena_signal_trade');
const SS = require('../public/js/signal-status-model.js');

const NOW = new Date('2026-10-17T12:00:00Z');
const ago = (ms) => new Date(NOW.getTime() - ms);
// Posted ten minutes ago; its five-minute window closed five minutes ago.
const closed = (over) => Object.assign({
  id: 7, symbol: 'BTCUSDT', direction: 'LONG', status: 'NEW',
  entry_price: 60000, stop_loss: 58000, take_profit: 66000,
  created_at: ago(10 * 60000), expires_at: ago(5 * 60000),
}, over || {});
const own = (over, ctx) => st.planOwnTrade(Object.assign({ signal: closed(over), mark: 60600, now: NOW }, ctx || {}));
const follow = (over, ctx) => st.planSignalOpen(Object.assign({
  signal: closed(over), positions: [], balance: 10000, margin: 200, leverage: 2, mark: 60600, now: NOW,
}, ctx || {}));

// ── the planner: one door at a time ──────────────────────────────────────

test('a closed window that has not ended is the member\'s own trade, and not a follow', () => {
  for (const w of SS.PENDING) {
    const o = own({ status: w });
    assert.equal(o.ok, true, w);
    assert.deepEqual(o.data, { symbol: 'BTCUSDT', direction: 'LONG', tp: 66000, sl: 58000,
      dropped: [], signal_entry: 60000 });
    assert.equal(follow({ status: w }).code, 'expired', 'the follow is still refused past the window');
  }
  assert.equal(own({ expires_at: null }).ok, true, 'a row that states no window is not live, and may be traded as one\'s own');
});

test('a live call is followed, never taken as one\'s own', () => {
  const live = { created_at: ago(2 * 60000), expires_at: ago(-3 * 60000) };
  const o = own(live);
  assert.equal(o.ok, false);
  assert.equal(o.code, 'live');
  assert.match(o.error, /still live\. Follow it/);
  assert.equal(follow(live).ok, true, 'and the follow is open');
  assert.equal(own({ expires_at: NOW }).code, 'live', 'the window\'s last instant is still inside it');
});

test('an ended call offers neither door, whatever its window says', () => {
  for (const w of [...SS.TERMINAL, 'CLOSED_EARLY']) {
    const o = own({ status: w });
    assert.equal(o.code, 'ended', w);
    assert.equal(follow({ status: w }).code, 'ended', w);
  }
  assert.match(own({ status: 'STOP' }).error, /hit its stop/);
});

test('the page asks the same question: a call is actionable or ownable or neither, never both', () => {
  // The panel asks `actionable` first, so an `ownable` that also said yes on
  // a live call would hide behind it there, and surface on the next page
  // that asks `ownable` alone. Asked here directly.
  const live = closed({ created_at: ago(2 * 60000), expires_at: ago(-3 * 60000) });
  const cases = [
    ['live', live, true, false],
    ['window closed, pending', closed(), false, true],
    ['window closed, entry hit', closed({ status: 'OPEN' }), false, true],
    ['no window stated', closed({ expires_at: null }), false, true],
    ['ended', closed({ status: 'STOP' }), false, false],
    ['ended inside its window', { ...live, status: 'TARGET' }, false, false],
    ['an older server\'s outcome', closed({ outcome: 'WIN' }), false, false],
    ['locked', closed({ locked: true }), false, false],
  ];
  for (const [what, s, act, own] of cases) {
    assert.equal(SS.actionable(s, NOW), act, `actionable: ${what}`);
    assert.equal(SS.ownable(s, NOW), own, `ownable: ${what}`);
  }
  assert.equal(SS.ownable(null, NOW), false);
});

test('no side, no signal or no live mark is refused, never filled at a guess', () => {
  assert.equal(own({ direction: 'FLAT' }).code, 'direction');
  assert.equal(st.planOwnTrade({}).code, 'no_signal');
  assert.equal(own({}, { mark: 0 }).code, 'no_mark');
  assert.equal(own({}, { mark: undefined }).code, 'no_mark');
});

test('exits the market has passed are dropped and named; the rest are kept', () => {
  // LONG: a target below the mark is behind it; a stop above it is too.
  assert.deepEqual(own({}, { mark: 67000 }).data, { symbol: 'BTCUSDT', direction: 'LONG',
    tp: null, sl: 58000, dropped: ['tp'], signal_entry: 60000 });
  assert.deepEqual(own({}, { mark: 57000 }).data.dropped, ['sl']);
  const short = own({ direction: 'SHORT', stop_loss: 62000, take_profit: 55000 }, { mark: 63000 }).data;
  assert.equal(short.sl, null);
  assert.equal(short.tp, 55000);
  assert.deepEqual(short.dropped, ['sl']);
  assert.deepEqual(own({ stop_loss: 0, take_profit: null }).data.dropped, [],
    'a call that stated no exit had none to drop');
});

test('drift is measured from the call\'s entry to the fill', () => {
  assert.equal(st.driftPct(60600, 60000), 1);
  assert.equal(st.driftPct(59400, 60000), -1);
  assert.equal(st.driftPct(60600, null), null, 'no entry on the call, no drift');
  assert.equal(st.driftPct(60600, 0), null);
});

// ── the route, on the in-memory database ─────────────────────────────────

const authModule = require('../auth');
const { setTickerFetcher } = require('../lib/tickers');
const { pool } = require('../db');

const PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 },
  ETHUSDT: { price: 112, change: 0, volume: 1 }, SOLUSDT: { price: 20, change: 0, volume: 1 },
  XRPUSDT: { price: 2, change: 0, volume: 1 }, DOGEUSDT: { price: 100, change: 0, volume: 1 } };
let server, base;
test.before(async () => {
  setTickerFetcher(async () => PRICES);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/arena', require('../routes/arena'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); setTickerFetcher(null); });

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, { method, headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(payload ? { 'Content-Type': 'application/json' } : {}) } }, (res) => {
      let d = ''; res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject); if (payload) r.write(payload); r.end();
  });
}
let n = 0;
async function user() {
  const reg = await req('POST', '/api/auth/register',
    { body: { email: `own${Date.now()}${++n}@x.io`, password: 'longenough1' } });
  assert.ok(reg.data.token, 'registered');
  return { token: reg.data.token, id: jwt.decode(reg.data.token).user_id };
}
/** A LONG call at 100 (stop 95, target 110); `leftMs` from now to the end of its window. */
async function push(key, symbol, leftMs, status = 'NEW') {
  const at = new Date(Date.now() - 10 * 60000);
  await pool.execute(
    'INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern, regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl, created_at, resolved_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
    [key, symbol, 'LONG', 0.7, 1, 't', 'trend', 100, 95, 110, 2, 't', status, null, at, null,
      new Date(Date.now() + leftMs)]);
  const [rows] = await pool.execute('SELECT * FROM signals WHERE signal_key = ?', [key]);
  return rows[0];
}
/** The stored rows, as the route's own loader reads them. */
async function rows(userId) {
  const [r] = await pool.execute(
    'SELECT id, user_id, symbol, direction, entry, margin, leverage, source, tp, sl, exits_edited, trail_pct, trade_key, seal, seal_payload, sealed_at, signal_key, agent_slug, opened_at FROM arena_positions WHERE user_id = ? ORDER BY id DESC', [userId]);
  return r;
}

test('/open-signal {own: true} opens a manual position: no signal key, no agent, the live mark', async () => {
  const u = await user();
  const sig = await push('own-open-1', 'BTC/USDT', -60000);
  const r = await req('POST', '/api/arena/open-signal', { token: u.token,
    body: { signal_key: 'own-open-1', own: true, margin: 200, leverage: 2, agent_slug: 'momentum' } });
  assert.equal(r.status, 200, JSON.stringify(r.data));
  assert.equal(r.data.filled.own, true);
  assert.equal(r.data.filled.entry, 100, 'filled at the live mark');
  assert.equal(r.data.filled.tp, 110);
  assert.equal(r.data.filled.sl, 95);
  assert.deepEqual(r.data.filled.dropped, []);
  assert.equal(r.data.filled.drift_pct, 0);
  assert.equal(r.data.filled.drift_warn, false);
  assert.equal(r.data.attribution, undefined, 'an own trade names no agent, whatever the request claimed');
  assert.equal(r.data.filled.signal_id, undefined, 'and is not tied to the call');

  const [p] = await rows(u.id);
  assert.equal(p.source, 'manual');
  assert.equal(p.signal_key, null, 'a record of the call would count this trade');
  assert.equal(p.agent_slug, null);
  assert.equal(Number(p.margin), 200);

  // The follow on the same call, by id: refused, and nothing more opened.
  const f = await req('POST', '/api/arena/open-signal', { token: u.token,
    body: { signal_id: sig.id, margin: 200, leverage: 2 } });
  assert.equal(f.status, 400);
  assert.equal(f.data.code, 'expired');
  assert.equal((await rows(u.id)).length, 1);
});

test('/open-signal {own: true} carries only the exits still ahead of the mark', async () => {
  const u = await user();
  await push('own-open-2', 'ETHUSDT', -60000);        // ETH marks 112: the 110 target is behind it
  const r = await req('POST', '/api/arena/open-signal', { token: u.token,
    body: { signal_key: 'own-open-2', own: true, margin: 200, leverage: 2 } });
  assert.equal(r.status, 200, JSON.stringify(r.data));
  assert.equal(r.data.filled.tp, null);
  assert.equal(r.data.filled.sl, 95);
  assert.deepEqual(r.data.filled.dropped, ['tp']);
  assert.equal(r.data.filled.drift_pct, 12);
  assert.equal(r.data.filled.drift_warn, true, 'a fill 12% from the call is not flagged');
});

test('/open-signal {own: true} refuses a live call and an ended one, and opens nothing', async () => {
  const u = await user();
  await push('own-live', 'SOLUSDT', 4 * 60000);
  await push('own-stop', 'XRPUSDT', -60000, 'STOP');
  const live = await req('POST', '/api/arena/open-signal', { token: u.token,
    body: { signal_key: 'own-live', own: true, margin: 200, leverage: 2 } });
  assert.equal(live.status, 400);
  assert.equal(live.data.code, 'live');
  const ended = await req('POST', '/api/arena/open-signal', { token: u.token,
    body: { signal_key: 'own-stop', own: true, margin: 200, leverage: 2 } });
  assert.equal(ended.status, 400);
  assert.equal(ended.data.code, 'ended');
  assert.match(ended.data.error, /hit its stop/);
  assert.equal((await rows(u.id)).length, 0);
  const a = await req('GET', '/api/arena/account', { token: u.token });
  assert.equal(a.data.balance, a.data.start_balance, 'no margin was taken');

  // Both arms: the live call still follows.
  const ok = await req('POST', '/api/arena/open-signal', { token: u.token,
    body: { signal_key: 'own-live', margin: 200, leverage: 2 } });
  assert.equal(ok.status, 200, JSON.stringify(ok.data));
  const [p] = await rows(u.id);
  assert.equal(p.source, 'signal');
  assert.equal(p.signal_key, 'own-live');
});

test('an own trade passes the manual open\'s gates: an armed envelope refuses it', async () => {
  const u = await user();
  await push('own-doge', 'DOGEUSDT', -60000);
  const armed = await req('PUT', '/api/arena/envelope', { token: u.token, body: { text: 'only majors, max leverage 5' } });
  assert.equal(armed.data.armed, true, 'the fixture envelope did not arm');
  const r = await req('POST', '/api/arena/open-signal', { token: u.token,
    body: { signal_key: 'own-doge', own: true, margin: 200, leverage: 2 } });
  assert.equal(r.status, 400, JSON.stringify(r.data));
  assert.equal(r.data.code, 'ENVELOPE');
  assert.equal((await rows(u.id)).length, 0);

  await req('DELETE', '/api/arena/envelope', { token: u.token });
  const ok = await req('POST', '/api/arena/open-signal', { token: u.token,
    body: { signal_key: 'own-doge', own: true, margin: 200, leverage: 2 } });
  assert.equal(ok.status, 200, JSON.stringify(ok.data));
});

test('only a literal own: true asks for the own trade', async () => {
  const u = await user();
  await push('own-truthy', 'BTCUSDT', -60000);
  for (const v of ['true', 1, 'yes']) {
    const r = await req('POST', '/api/arena/open-signal', { token: u.token,
      body: { signal_key: 'own-truthy', own: v, margin: 200, leverage: 2 } });
    assert.equal(r.data.code, 'expired', `own: ${JSON.stringify(v)} was read as a request for the own trade`);
  }
});
