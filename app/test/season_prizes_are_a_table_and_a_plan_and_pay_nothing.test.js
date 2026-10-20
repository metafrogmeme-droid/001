'use strict';
/**
 * Season prizes in Pi (lib/pi_prizes.js, routes/pi_prizes.js): a table behind
 * an off switch, and a payout plan for the operator. Nothing pays.
 *
 *   - the switch is off unless set exactly, and a table that does not read is
 *     off with its reason, never a different prize;
 *   - the plan ranks a season's closed trades by chosen name, needs enough
 *     closes to place, splits ties, and holds a name whose result cannot be
 *     read instead of counting it as break-even;
 *   - the player's table is for signed-in people, the plan for the operator;
 *   - no code here names a key or calls Pi.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

delete process.env.DATABASE_URL;
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const { pool } = require('../db');
const { findOrCreateOAuthUser, signToken } = require('../auth');
const prizes = require('../lib/pi_prizes');
const router = require('../routes/pi_prizes');
const { codeOnly } = require('./helpers/code_only');

const ON = { PI_SEASON_PRIZES_ENABLED: '1', PI_SEASON_PRIZES: '50,30,20' };

// ── the table ─────────────────────────────────────────────────────────────

test('the switch is off unless it is exactly 1 and the table reads', () => {
  assert.deepStrictEqual(prizes.schedule({}), { on: false, reason: 'off' });
  assert.deepStrictEqual(prizes.schedule({ PI_SEASON_PRIZES: '50,30,20' }), { on: false, reason: 'off' });
  assert.deepStrictEqual(prizes.schedule({ ...ON, PI_SEASON_PRIZES_ENABLED: 'true' }), { on: false, reason: 'off' });
  assert.deepStrictEqual(prizes.schedule({ PI_SEASON_PRIZES_ENABLED: '1' }), { on: false, reason: 'no_table' });
  assert.deepStrictEqual(prizes.schedule(ON), { on: true, places: [50, 30, 20], minTrades: 5, total: 100 });
  assert.deepStrictEqual(prizes.schedule({ ...ON, PI_SEASON_PRIZES: ' 10.5, 0.0000001 ', PI_SEASON_PRIZES_MIN_TRADES: '3' }),
    { on: true, places: [10.5, 0.0000001], minTrades: 3, total: 10.5000001 });
});

test('a table that does not read is off with its reason, never a different prize', () => {
  const bad = (table, reason, extra = {}) => assert.deepStrictEqual(
    prizes.schedule({ ...ON, PI_SEASON_PRIZES: table, ...extra }), { on: false, reason }, table);
  bad('50,thirty,20', 'bad_amount');
  bad('50,,20', 'bad_amount');
  bad('50,0,0', 'bad_amount');
  bad('-5', 'bad_amount');
  bad('1e3', 'bad_amount');
  bad('0.00000001', 'bad_amount');
  bad('10001', 'bad_amount');
  bad('20,30', 'rising');
  bad(Array(11).fill('1').join(','), 'too_many_places');
  bad('50,30', 'bad_min_trades', { PI_SEASON_PRIZES_MIN_TRADES: 'five' });
  bad('50,30', 'bad_min_trades', { PI_SEASON_PRIZES_MIN_TRADES: '0' });
  // Both arms: ten places and equal places are a table.
  assert.strictEqual(prizes.schedule({ ...ON, PI_SEASON_PRIZES: Array(10).fill('1').join(',') }).on, true);
  assert.strictEqual(prizes.schedule({ ...ON, PI_SEASON_PRIZES: '5,5,5' }).on, true);
});

// ── the plan ──────────────────────────────────────────────────────────────

/** n closes of `each` credits for user `id`. */
function closes(id, n, each) {
  return Array.from({ length: n }, () => ({ user_id: id, pnl: each }));
}

test('the plan ranks chosen names with enough closes, on percent return', () => {
  const sched = prizes.schedule({ ...ON, PI_SEASON_PRIZES_MIN_TRADES: '2' });
  const handles = new Map([[1, 'ana'], [2, 'bo'], [3, 'cy'], [4, 'di']]);
  const trades = [
    ...closes(1, 2, 100),   // +2%
    ...closes(2, 2, 300),   // +6%
    ...closes(3, 1, 5000),  // +50%, but one close: does not place
    ...closes(4, 2, -50),   // -1%
    ...closes(5, 3, 900),   // +27%, no chosen name: does not place
  ];
  const p = prizes.plan(trades, handles, sched);
  assert.strictEqual(p.final, true);
  assert.deepStrictEqual(p.places.map((x) => [x.place, x.handle, x.return_pct, x.amount]),
    [[1, 'bo', 6, 50], [2, 'ana', 2, 30], [3, 'di', -1, 20]]);
  assert.strictEqual(p.unawarded, 0);
  assert.deepStrictEqual(p.held, []);
});

test('ties share the prizes of the places they span, whatever the row order', () => {
  const sched = prizes.schedule({ ...ON, PI_SEASON_PRIZES_MIN_TRADES: '1' });
  const handles = new Map([[1, 'ana'], [2, 'bo'], [3, 'cy'], [4, 'di']]);
  // Two tied for first: 50 + 30 between them; the third is third.
  const a = prizes.plan([...closes(2, 1, 400), ...closes(1, 1, 400), ...closes(3, 1, 100)], handles, sched);
  assert.deepStrictEqual(a.places.map((x) => [x.place, x.handle, x.amount]), [[1, 'ana', 40], [1, 'bo', 40], [3, 'cy', 20]]);
  // Three tied for second, across the table's edge: 30 + 20 between three.
  const b = prizes.plan([...closes(1, 1, 900), ...closes(4, 1, 100), ...closes(2, 1, 100), ...closes(3, 1, 100)], handles, sched);
  assert.deepStrictEqual(b.places.map((x) => [x.place, x.handle, x.amount]),
    [[1, 'ana', 50], [2, 'bo', 16.6666667], [2, 'cy', 16.6666667], [2, 'di', 16.6666667]]);
  // Fewer names than places: the rest is unawarded, not handed down.
  const c = prizes.plan(closes(1, 1, 100), handles, sched);
  assert.deepStrictEqual(c.places.map((x) => x.amount), [50]);
  assert.strictEqual(c.unawarded, 50);
});

test('a name whose result cannot be read is held, not counted as break-even', () => {
  const sched = prizes.schedule({ ...ON, PI_SEASON_PRIZES: '50', PI_SEASON_PRIZES_MIN_TRADES: '1' });
  const handles = new Map([[1, 'ana'], [2, 'bo']]);
  // ana leads on what reads, with one close that does not.
  const held = prizes.plan([{ user_id: 1, pnl: 500 }, { user_id: 1, pnl: null }, { user_id: 2, pnl: 100 }], handles, sched);
  assert.strictEqual(held.final, false);
  assert.deepStrictEqual(held.held, [{ user_id: 1, handle: 'ana', unreadable: 1 }]);
  // Just outside the paid places, an unreadable close holds the plan too:
  // read, it might be inside them.
  const outside = prizes.plan([{ user_id: 1, pnl: 500 }, { user_id: 2, pnl: 100 }, { user_id: 2, pnl: 'n/a' }], handles, sched);
  assert.strictEqual(outside.final, false);
  assert.deepStrictEqual(outside.held.map((h) => h.handle), ['bo']);
  // Both arms: every result read, the plan is final.
  assert.strictEqual(prizes.plan([{ user_id: 1, pnl: 500 }, { user_id: 2, pnl: 0 }], handles, sched).final, true);
  // A real 0 is a measured break-even, not an unreadable one.
  assert.deepStrictEqual(prizes.plan([{ user_id: 2, pnl: 0 }], handles, sched).held, []);
});

test('the plan refuses to run with the prizes off', () => {
  assert.throws(() => prizes.plan([], new Map(), prizes.schedule({})), /off/);
});

// ── the routes ────────────────────────────────────────────────────────────

let seq = 0;
async function account({ admin = false, handle = null } = {}) {
  seq += 1;
  const user = await findOrCreateOAuthUser({ provider: 'pi', providerId: `pi-prize-uid-${seq}-${Date.now()}` });
  const row = pool.users.find((u) => u.id === user.id);
  if (admin) row.plan = 'admin';
  if (handle) row.leaderboard_handle = handle;
  return { id: user.id, token: signToken(user) };
}

async function serve(env) {
  router._deps = { env, now: () => new Date('2026-10-20T12:00:00Z') };
  const app = express();
  app.use('/api/pi/prizes', router);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function get(base, p, token) {
  const r = await fetch(`${base}${p}`, { headers: token ? { authorization: `Bearer ${token}` } : {} });
  return { status: r.status, body: await r.json().catch(() => null) };
}

test('the table is for signed-in people, the plan for the operator, and off is off', async () => {
  pool.arenaSeasons.push({ id: 7001, name: 'Prize Season', starts_at: '2026-10-01T00:00:00Z', ends_at: '2026-10-31T00:00:00Z', rules: null });
  const player = await account({ handle: 'prize_player' });
  const other = await account({ handle: 'prize_other' });
  const op = await account({ admin: true });
  for (let i = 0; i < 5; i += 1) {
    pool.arenaTrades.push({ id: 980000 + i, user_id: player.id, symbol: 'BTCUSDT', pnl: 200, seal: null, closed_at: '2026-10-10T10:00:00Z' });
    pool.arenaTrades.push({ id: 981000 + i, user_id: other.id, symbol: 'BTCUSDT', pnl: 100, seal: null, closed_at: '2026-10-10T10:00:00Z' });
  }
  let s = await serve({});
  try {
    assert.strictEqual((await get(s.base, '/api/pi/prizes')).status, 401);
    assert.deepStrictEqual((await get(s.base, '/api/pi/prizes', player.token)).body, { enabled: false });
    assert.deepStrictEqual((await get(s.base, '/api/pi/prizes/plan', op.token)).body, { enabled: false, reason: 'off' });
  } finally {
    s.server.close();
  }
  s = await serve(ON);
  try {
    const table = await get(s.base, '/api/pi/prizes', player.token);
    assert.strictEqual(table.status, 200);
    assert.deepStrictEqual(table.body, { enabled: true, places: [50, 30, 20], min_trades: 5, total: 100,
      season: { name: 'Prize Season', ends_at: '2026-10-31T00:00:00Z', status: 'live' } });
    // The plan: the operator only.
    assert.strictEqual((await get(s.base, '/api/pi/prizes/plan', player.token)).status, 403);
    const plan = await get(s.base, '/api/pi/prizes/plan', op.token);
    assert.strictEqual(plan.status, 200);
    assert.strictEqual(plan.body.running, true);
    assert.strictEqual(plan.body.final, false, 'a running season\'s plan is a preview');
    assert.strictEqual(plan.body.paid_by, 'operator');
    const mine = plan.body.places.filter((x) => x.handle === 'prize_player' || x.handle === 'prize_other');
    assert.deepStrictEqual(mine.map((x) => [x.handle, x.return_pct]), [['prize_player', 10], ['prize_other', 5]]);
  } finally {
    s.server.close();
  }
});

test('nothing here names a key or calls Pi', () => {
  for (const f of ['lib/pi_prizes.js', 'routes/pi_prizes.js']) {
    const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', f), 'utf8'));
    assert.doesNotMatch(src, /SEED|PRIVATE|SECRET|API_KEY|PI_NETWORK|api\.minepi\.com|fetch\(|https?:\/\//i, f);
    assert.doesNotMatch(src, /require\(['"]\.\.\/lib\/pi_payments['"]\)|stellar/i, f);
  }
});
