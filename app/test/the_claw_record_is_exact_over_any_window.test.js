'use strict';
/**
 * GET /api/public/claw-record is exact over any window, reads `window=`, and
 * pages its resolved calls.
 *
 * It read only `?days=`, so a link asking `?window=90` got the 30-day record
 * under the 90-day label it asked for; and it read the window's newest 20,000
 * rows and counted them in JS, so at ~700 calls a day a 30-, 90- or 365-day
 * record was a partial count. The counts are the database's now (grouped SQL,
 * folded by the same fold the pure path uses) and the resolved list is a page
 * with a cursor that does not skip the many calls resolving on one bar close.
 * A live call is counted and never listed, on every page.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');

const { pool } = require('../db');
const cr = require('../lib/claw_record');
const route = require('../routes/claw_record');

let server; let base;
test.before(async () => {
  const app = express();
  app.use('/api/public/claw-record', route);
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

// The route is IP-rate-limited (30 a minute); this file stays well under it.
const get = (p) => new Promise((resolve, reject) => {
  route._resetCache();
  http.get(base + p, (res) => {
    let d = ''; res.on('data', (c) => { d += c; });
    res.on('end', () => resolve({ status: res.statusCode, body: d ? JSON.parse(d) : null }));
  }).on('error', reject);
});

const H = 3600000;
const hoursAgo = (h) => new Date(Date.now() - h * H);
let n = 0;
/** A call created `h` hours ago, sealed at creation unless told otherwise. */
function call({ h = 24, pnl = 1, status = 'TARGET', resolved = undefined, key = undefined,
  sealed = true, sealAfterH = 0, symbol = 'BTC/USDT' } = {}) {
  n += 1;
  const created = hoursAgo(h);
  const resolvedAt = resolved !== undefined ? resolved
    : (pnl === null ? null : new Date(created.getTime() + 2 * H));
  return {
    id: n, signal_key: key || `k${String(n).padStart(6, '0')}`, symbol, direction: 'LONG',
    status, pnl, created_at: created, resolved_at: resolvedAt,
    seal: sealed ? 'ab'.repeat(32) : null,
    sealed_at: sealed ? new Date(created.getTime() + sealAfterH * H) : null,
  };
}
const seed = (rows) => { pool.signals.length = 0; pool.signals.push(...rows); };

test('the window is read from ?window= or ?days=, and anything else is 30', async () => {
  seed([call({ h: 20 * 24 }), call({ h: 3 * 24 })]);
  const cases = [
    ['?window=7', 7, 1], ['?days=7', 7, 1], ['?window=90&days=7', 90, 2],
    ['?window=14', 30, 2], ['?days=abc', 30, 2],
  ];
  for (const [q, days, calls] of cases) {
    const r = await get(`/api/public/claw-record${q}`);
    assert.equal(r.status, 200, q);
    assert.equal(r.body.window_days, days, q);
    assert.equal(r.body.sealed_calls, calls, q);
  }
  assert.equal(cr.windowOf(['7', '30']), 30, 'a repeated parameter is not a window');
});

test('more than 20,000 calls in a window are counted exactly, and not called partial', async () => {
  const rows = [];
  for (let i = 0; i < 20000; i += 1) rows.push(call({ h: 1 + (i % 700), pnl: i % 2 ? 1.5 : -1, status: i % 2 ? 'TARGET' : 'STOP' }));
  for (let i = 0; i < 300; i += 1) rows.push(call({ h: 2, pnl: null, status: 'NEW' }));
  for (let i = 0; i < 200; i += 1) rows.push(call({ h: 5, sealed: false }));
  seed(rows);
  const r = await get('/api/public/claw-record?window=30');
  assert.equal(r.status, 200);
  assert.equal(r.body.sealed_calls, 20300);
  assert.equal(r.body.resolved, 20000);
  assert.deepEqual([r.body.wins, r.body.losses, r.body.flat], [10000, 10000, 0]);
  assert.deepEqual(r.body.no_outcome, { new: 300 });
  assert.deepEqual(r.body.excluded, { unsealed: 200, sealed_after_outcome: 0 });
  assert.equal(r.body.days.reduce((a, d) => a + d.sealed, 0), 20300);
  assert.equal(r.body.partial, false);
  assert.equal(r.body.verdict, 'measured');
  assert.equal(r.body.recent.length, cr.RECENT);
  assert.equal(r.body.avg_r, 0.25);
});

test('the database\'s count equals the pure count on a mixed window', async () => {
  const rows = [
    call({ h: 30, pnl: '2.50000000' }), call({ h: 30, pnl: '-1.00000000', status: 'STOP' }),
    call({ h: 40, pnl: 0, status: 'TARGET' }),                         // flat
    call({ h: 50, pnl: null, status: 'EXPIRED' }), call({ h: 50, pnl: null, status: 'expired' }),
    call({ h: 51, pnl: null, status: '' }),                            // no word: unknown
    call({ h: 52, pnl: null, status: 'AMBIGUOUS', resolved: hoursAgo(50) }),
    call({ h: 60, pnl: 3, sealAfterH: 5 }),                            // sealed after its outcome
    call({ h: 61, pnl: 3, sealed: false }),
    call({ h: 62, pnl: 1, sealed: true }),
    call({ h: 24 * 45, pnl: 4 }),                                      // outside 30 days
    call({ h: 3, pnl: null, status: 'NEW', symbol: 'LIVEONLY/USDT' }),
  ];
  rows[9].seal = '';                                                   // an empty seal is no seal
  seed(rows);
  const r = await get('/api/public/claw-record?window=30&limit=200');
  assert.equal(r.status, 200);
  const pure = cr.computeClawRecord(rows, { now: new Date(), windowDays: 30, limit: 200 });
  for (const k of ['sealed_calls', 'resolved', 'wins', 'losses', 'flat', 'win_rate', 'avg_r',
    'no_outcome', 'excluded', 'verdict', 'recent']) {
    assert.deepEqual(r.body[k], pure[k], k);
  }
  assert.deepEqual(r.body.days.map(({ root, anchors, ...d }) => d), pure.days);
  assert.deepEqual(r.body.excluded, { unsealed: 2, sealed_after_outcome: 1 });
  assert.deepEqual(r.body.no_outcome, { expired: 2, unknown: 1, ambiguous: 1, new: 1 });
});

test('the resolved list is a page: limit is capped, the cursor covers ties, live calls never appear', async () => {
  const T = hoursAgo(5);
  const later = new Date(T.getTime() + H);
  const earlier = new Date(T.getTime() - H);
  const rows = [];
  for (let i = 1; i <= 7; i += 1) rows.push(call({ h: 10, key: `r${i}`, resolved: T }));
  rows.push(call({ h: 10, key: 'a1', resolved: later }), call({ h: 10, key: 'a2', resolved: later }));
  rows.push(call({ h: 10, key: 'z1', resolved: earlier }));
  rows.push(call({ h: 10, key: 'after-seal', resolved: T, sealAfterH: 9 }));   // not eligible
  for (let i = 0; i < 3; i += 1) rows.push(call({ h: 1, pnl: null, status: 'NEW', key: `LIVE-${i}`, symbol: 'LIVEONLY/USDT' }));
  seed(rows);

  const all = await get('/api/public/claw-record?limit=500');
  assert.equal(all.body.limit, cr.MAX_RECENT);
  assert.deepEqual(all.body.recent.map((x) => x.key),
    ['a2', 'a1', 'r7', 'r6', 'r5', 'r4', 'r3', 'r2', 'r1', 'z1']);
  assert.equal(all.body.next_before, null);

  const seen = []; const bodies = [];
  let q = '/api/public/claw-record?limit=3';
  for (let page = 0; page < 6 && q; page += 1) {
    const r = await get(q);
    assert.equal(r.status, 200);
    bodies.push(JSON.stringify(r.body));
    seen.push(...r.body.recent.map((x) => x.key));
    q = r.body.next_before
      ? `/api/public/claw-record?limit=3&before=${encodeURIComponent(r.body.next_before)}`
        + `&before_key=${encodeURIComponent(r.body.next_before_key)}`
      : null;
  }
  assert.deepEqual(seen, all.body.recent.map((x) => x.key), 'every resolved call once, in order');
  for (const b of bodies) {
    assert.ok(!b.includes('LIVEONLY') && !b.includes('LIVE-'), 'a live call is never listed');
    assert.ok(!b.includes('after-seal'), 'a call sealed after its outcome is never listed');
  }
  // `before` alone pages by time: strictly older than the time given.
  const older = await get(`/api/public/claw-record?before=${encodeURIComponent(T.toISOString())}`);
  assert.deepEqual(older.body.recent.map((x) => x.key), ['z1']);
  // The counts are the window's whatever page is asked for.
  assert.equal(older.body.resolved, all.body.resolved);
  const bad = await get('/api/public/claw-record?before=not-a-time');
  assert.equal(bad.status, 400);
});

test('limit and cursor values read the way the route reads them', () => {
  assert.equal(cr.recentLimitOf(undefined), cr.RECENT);
  assert.equal(cr.recentLimitOf('abc'), cr.RECENT);
  assert.equal(cr.recentLimitOf('0'), 1);
  assert.equal(cr.recentLimitOf('-4'), 1);
  assert.equal(cr.recentLimitOf('200'), 200);
  assert.equal(cr.recentLimitOf('201'), cr.MAX_RECENT);
  assert.equal(cr.cursorOf(undefined, 'k'), null);
  assert.equal(cr.cursorOf('nope', 'k'), undefined);
  assert.equal(cr.cursorOf(['a', 'b'], 'k'), undefined);
  assert.deepEqual(cr.cursorOf('2026-11-01T10:00:00Z', ''), { at: new Date('2026-11-01T10:00:00Z'), key: null });
  assert.equal(cr.cursorOf('2026-11-01T10:00:00Z', 'k1').key, 'k1');
});
