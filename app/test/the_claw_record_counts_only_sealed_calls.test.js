'use strict';
/**
 * The Claw Record (lib/claw_record.js, GET /api/public/claw-record,
 * /claw-record): the agent's own calls, counted only where sealed before
 * their outcome.
 *
 * Each rule is driven both ways: a sealed call counts and an unsealed one
 * does not; a seal before the outcome counts and one after does not; a call
 * with no outcome stays out of the rate and is not a loss; below the floor
 * the rate is shown and no verdict is drawn, at the floor it is measured; a
 * resolved call is shown with its receipt and a live one is only counted; a
 * database that does not answer is "could not be read", never an empty
 * record.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const { pool } = require('../db');
const cr = require('../lib/claw_record');
const { render } = require('../public/js/claw-record.js');

const NOW = new Date('2026-10-21T12:00:00Z');
const at = (h) => new Date(NOW.getTime() - h * 3600000).toISOString();
let n = 0;
/** A call `h` hours old; `seal` hours after creation (null: unsealed); `outcome` hours after creation with R `pnl`. */
function call({ h = 24, sealAfter = 0, resolveAfter = 6, pnl = 1, status = 'TARGET', symbol = 'BTC/USDT', direction = 'LONG' } = {}) {
  n += 1;
  const created = new Date(NOW.getTime() - h * 3600000);
  return {
    signal_key: `sig-${n}`, symbol, direction, status,
    pnl: pnl === null ? null : pnl,
    created_at: created.toISOString(),
    resolved_at: pnl === null ? null : new Date(created.getTime() + resolveAfter * 3600000).toISOString(),
    seal: sealAfter === null ? null : 'ab'.repeat(32),
    sealed_at: sealAfter === null ? null : new Date(created.getTime() + sealAfter * 3600000).toISOString(),
  };
}

test('a call counts only when sealed, and not after its outcome', () => {
  assert.strictEqual(cr.eligibility(call()), 'sealed');
  assert.strictEqual(cr.eligibility(call({ sealAfter: null })), 'unsealed');
  assert.strictEqual(cr.eligibility({ ...call(), sealed_at: null }), 'unsealed', 'a seal with no time is no seal');
  assert.strictEqual(cr.eligibility(call({ sealAfter: 7, resolveAfter: 6 })), 'sealed_after_outcome');
  assert.strictEqual(cr.eligibility(call({ sealAfter: 5, resolveAfter: 6 })), 'sealed', 'sealed while the call was open');
  assert.strictEqual(cr.eligibility(call({ pnl: null, status: 'NEW' })), 'sealed', 'a live sealed call is eligible, not yet scored');
});

test('the rate is over sealed, resolved calls only; no outcome is not a loss; flat is not a loss', () => {
  const rows = [
    call({ pnl: 2 }), call({ pnl: 1.5 }), call({ pnl: -1 }), call({ pnl: 0 }),     // 2 wins, 1 loss, 1 flat
    call({ pnl: null, status: 'EXPIRED' }), call({ pnl: null, status: 'NEW' }),    // no outcome
    call({ pnl: 5, sealAfter: null }),                                            // unsealed win: not counted
    call({ pnl: 5, sealAfter: 7, resolveAfter: 6 }),                              // sealed after: not counted
    call({ pnl: 3, h: 24 * 40 }),                                                 // outside 30 days
  ];
  const r = cr.computeClawRecord(rows, { now: NOW, windowDays: 30 });
  assert.strictEqual(r.sealed_calls, 6);
  assert.strictEqual(r.resolved, 4);
  assert.deepStrictEqual([r.wins, r.losses, r.flat], [2, 1, 1]);
  assert.strictEqual(r.win_rate, 50);
  assert.strictEqual(r.avg_r, 0.63, '(2 + 1.5 - 1 + 0) / 4, gross');
  assert.deepStrictEqual(r.no_outcome, { expired: 1, new: 1 });
  assert.deepStrictEqual(r.excluded, { unsealed: 1, sealed_after_outcome: 1 });
  assert.strictEqual(r.verdict, 'too_few');
  assert.strictEqual(r.floor, 350);
  // The longer window takes the older call in.
  assert.strictEqual(cr.computeClawRecord(rows, { now: NOW, windowDays: 90 }).resolved, 5);
});

test('nothing resolved is no rate, not 0%; the floor is where a verdict starts', () => {
  const none = cr.computeClawRecord([call({ pnl: null, status: 'NEW' })], { now: NOW });
  assert.strictEqual(none.win_rate, null);
  assert.strictEqual(none.avg_r, null);
  const under = Array.from({ length: cr.SAMPLE_FLOOR - 1 }, (_, i) => call({ pnl: i % 2 ? 1 : -1, h: 1 + (i % 600) }));
  assert.strictEqual(cr.computeClawRecord(under, { now: NOW, windowDays: 30 }).verdict, 'too_few');
  const at = [...under, call({ pnl: 1, h: 2 })];
  const r = cr.computeClawRecord(at, { now: NOW, windowDays: 30 });
  assert.strictEqual(r.resolved, cr.SAMPLE_FLOOR);
  assert.strictEqual(r.verdict, 'measured');
});

test('a resolved call is shown with its receipt; a live one is only counted', () => {
  const live = call({ pnl: null, status: 'NEW', symbol: 'LIVEONLY/USDT', direction: 'SHORT' });
  const done = call({ pnl: -1, status: 'STOP', symbol: 'ETH/USDT', direction: 'SHORT' });
  const r = cr.computeClawRecord([live, done], { now: NOW });
  assert.deepStrictEqual(r.recent.map((x) => x.key), [done.signal_key]);
  assert.strictEqual(r.recent[0].receipt, `/call/${done.signal_key}`);
  assert.strictEqual(r.recent[0].outcome, 'loss');
  const json = JSON.stringify(r);
  assert.ok(!json.includes('LIVEONLY'), 'a live call\'s market is not in the record');
  assert.ok(!json.includes(live.signal_key), 'nor its key');
  assert.ok(!/usd"|\$|_usd|amount/i.test(json.replace(/USDT/g, '')), 'counts, percent and R only');
  assert.ok(cr.WINDOWS.includes(cr.windowOf('90')) && cr.windowOf('999') === cr.DEFAULT_WINDOW && cr.windowOf('x') === 30);
});

// ── the route ────────────────────────────────────────────────────────────────

let server; let base;
test.before(async () => {
  const app = express();
  app.use('/api/public/claw-record', require('../routes/claw_record'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

const get = (p) => new Promise((resolve, reject) => {
  http.get(base + p, (res) => {
    let d = ''; res.on('data', (c) => { d += c; });
    res.on('end', () => resolve({ status: res.statusCode, body: d ? JSON.parse(d) : null }));
  }).on('error', reject);
});

test('the route serves the record with each day\'s root, and a dead database is not an empty record', async () => {
  const now = Date.now();
  const day = new Date(now - 2 * 86400000).toISOString().slice(0, 10);
  const created = new Date(`${day}T10:00:00Z`);
  pool.signals.push(
    { signal_key: 'route-win', symbol: 'SOL/USDT', direction: 'LONG', status: 'TARGET', pnl: 2,
      created_at: created, resolved_at: new Date(created.getTime() + 3600000), seal: 'cd'.repeat(32), sealed_at: created },
    { signal_key: 'route-unsealed', symbol: 'SOL/USDT', direction: 'LONG', status: 'TARGET', pnl: 2,
      created_at: created, resolved_at: new Date(created.getTime() + 3600000), seal: null, sealed_at: null });
  await pool.execute('INSERT INTO seal_roots (day, root, seal_count, leaves, computed_at) VALUES (?, ?, ?, ?, ?)',
    [day, 'ef'.repeat(32), 1, JSON.stringify(['cd'.repeat(32)]), new Date()]);
  require('../routes/claw_record')._resetCache();
  let r = await get('/api/public/claw-record?days=7');
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.body.window_days, 7);
  assert.strictEqual(r.body.resolved, 1);
  assert.strictEqual(r.body.excluded.unsealed, 1);
  const d = r.body.days.find((x) => x.day === day);
  assert.strictEqual(d.root, 'ef'.repeat(32));
  assert.ok(Array.isArray(d.anchors));
  assert.strictEqual(r.body.roots_readable, true);
  assert.strictEqual(r.body.partial, false);

  // A window that reaches the read cap says its counts are partial.
  const route = require('../routes/claw_record');
  route._resetCache();
  route._limits.maxRows = 1;
  try {
    r = await get('/api/public/claw-record?days=30');
  } finally { route._limits.maxRows = 20000; route._resetCache(); }
  assert.strictEqual(r.body.partial, true);

  const real = pool.execute;
  pool.execute = async () => { const e = new Error('connect ECONNREFUSED 10.0.0.1:3306'); e.name = 'ConnectionError'; throw e; };
  try {
    r = await get('/api/public/claw-record?days=7');
  } finally { pool.execute = real; }
  assert.strictEqual(r.status, 503);
  assert.strictEqual(r.body.exception, 'ConnectionError');
  assert.ok(!JSON.stringify(r.body).includes('10.0.0.1'), 'the error names the class, never the message');

  // A database that answers in a shape the record cannot read is answered,
  // never left hanging (Express does not catch an async throw).
  require('../routes/claw_record')._resetCache();
  pool.execute = async () => [{ rows: 'not a list' }, []];
  try {
    r = await get('/api/public/claw-record?days=7');
  } finally { pool.execute = real; }
  assert.strictEqual(r.status, 500);
  assert.strictEqual(r.body.exception, 'TypeError');
  assert.ok(!('detail' in r.body) && !/not a function/.test(JSON.stringify(r.body)));
});

// ── the page ─────────────────────────────────────────────────────────────────

test('the page never paints the rate as a verdict below the floor, and says so when it cannot read', () => {
  const T = (k, en) => en;
  assert.match(render(null, T), /could not be read/);
  assert.match(render({ error: 'x' }, T), /could not be read/, 'an error body is not a record');
  const few = cr.computeClawRecord([call({ pnl: 1 }), call({ pnl: -1 })], { now: NOW });
  const html = render(few, T);
  assert.match(html, /data-field="win_rate">50%/);
  assert.match(html, /cr-v cr-muted" data-field="win_rate"/, 'muted below the floor');
  assert.match(html, /Too few to judge/);
  assert.match(html, /2 of 350 resolved calls/);
  assert.match(html, /href="\/call\/sig-/, 'each resolved call links its receipt');
  assert.doesNotMatch(html, /var\(--up\)/, 'no rate is ever painted green');
  const many = cr.computeClawRecord(Array.from({ length: 350 }, (_, i) => call({ pnl: i % 3 ? 1 : -1, h: 1 + (i % 600) })), { now: NOW });
  const html2 = render(many, T);
  assert.match(html2, /class="cr-v" data-field="win_rate"/, 'the other arm: measured is not muted');
  assert.match(html2, />Measured</);
  assert.match(render(cr.computeClawRecord([], { now: NOW }), T), /No sealed call was made in this window/);
});
