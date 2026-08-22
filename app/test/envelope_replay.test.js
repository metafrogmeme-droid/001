'use strict';
/**
 * Envelope replay — "previews before it binds", delivered honestly.
 *
 * The counterfactual contract, which is the whole point:
 * - the report says which recorded opens the policy would have REFUSED and
 *   what those trades REALLY returned (facts about trades that happened);
 * - it NEVER produces a hypothetical equity curve or a "would have been"
 *   number — refusing a trade changes everything after it, so that value is
 *   unknowable and is omitted rather than invented;
 * - the balance at each decision point is the balance the account ACTUALLY
 *   had, reconstructed from the recorded sequence in order;
 * - an unreadable row is skipped, never counted as compliant (a silent pass
 *   would flatter the policy);
 * - a preview stores nothing and arms nothing.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const http = require('http');
const express = require('express');
const jwt = require('jsonwebtoken');
const { pool } = require('../db');
const { replay } = require('../lib/envelope_replay');

let server, base, token, uid;

function req(method, p, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, { method,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}`,
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}) } }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(Buffer.concat(chunks) || '{}') }));
    });
    r.on('error', reject);
    if (data) r.write(data);
    r.end();
  });
}

test.before(async () => {
  const [u] = await pool.execute(
    'INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, NOW())',
    ['replay@test.dev', 'x'.repeat(60)]);
  uid = u.insertId;
  token = jwt.sign({ user_id: uid, email: 'replay@test.dev' }, process.env.JWT_SECRET);
  const app = express();
  app.use(express.json());
  app.use('/api/arena', require('../routes/arena'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

const T = (o) => Object.assign(
  { symbol: 'BTCUSDT', direction: 'LONG', margin: 100, leverage: 2, pnl: 0,
    opened_at: new Date() }, o);

test('the walk uses the REAL balance path, in order', () => {
  // A 6%-of-book cap: at 10000 a 500 margin is 5% (allowed); after a big
  // loss drops the book to 6000, the same 500 is 8.3% (refused). Only a
  // real-path walk gets this right.
  const rules = [{ id: 'size_max', value: 6 }];
  const r = replay(rules, [
    T({ margin: 500, pnl: -4000 }),   // 5% of 10000 → allowed, then book = 6000
    T({ margin: 500, pnl: 0 }),       // 8.3% of 6000 → refused
  ], 10000);
  assert.strictEqual(r.checked, 2);
  assert.strictEqual(r.refused_count, 1);
  assert.strictEqual(r.refused[0].rule_id, 'size_max');
  assert.strictEqual(r.refused[0].params.pct, 8.3);
});

test('refused trades report what they ACTUALLY returned', () => {
  const rules = [{ id: 'dir_long', value: 'long_only' }];
  const r = replay(rules, [
    T({ direction: 'SHORT', margin: 100, pnl: 30 }),    // +30%
    T({ direction: 'SHORT', margin: 200, pnl: -20 }),   // -10%
    T({ direction: 'LONG', margin: 100, pnl: 50 }),     // allowed
  ], 10000);
  assert.strictEqual(r.refused_count, 2);
  assert.strictEqual(r.allowed_count, 1);
  assert.deepEqual(r.refused.map((x) => x.rom_pct), [30, -10]);
  assert.strictEqual(r.refused_rom_pct_sum, 20);
  assert.strictEqual(r.refused_rom_pct_avg, 10);
  assert.deepEqual(r.by_rule, { dir_long: 2 });
});

test('no hypothetical equity curve is ever produced', () => {
  const r = replay([{ id: 'dir_long', value: 'long_only' }],
    [T({ direction: 'SHORT', pnl: 999 })], 10000);
  // WALKED, NOT JOINED. `Object.keys(r).join(' ')` sees only the TOP level, so
  // a `summary: { would_have_saved: 4210 }` — the exact shape a "helpful"
  // addition takes — passed straight through it. The walk below reaches every
  // key at every depth.
  //
  // The prose assertion that used to close this test (`does not ship
  // fabricated histories`, matched against envelope_replay.js) was a comment.
  // Adding the field and leaving the comment satisfied it completely.
  const FORBIDDEN = ['would_have', 'hypothetical', 'equity', 'simulated_balance',
    'saved', 'counterfactual', 'curve', 'projected'];
  const seen = [];
  (function walk(v, at) {
    if (!v || typeof v !== 'object') return;
    for (const k of Object.keys(v)) {
      const where = at ? `${at}.${k}` : k;
      for (const f of FORBIDDEN) if (k.toLowerCase().includes(f)) seen.push(where);
      walk(v[k], where);
    }
  }(r, ''));
  assert.deepStrictEqual(seen, [],
    'the replay is emitting a field that names a history nobody lived — it '
    + 'reports what the envelope WOULD have refused, never what the account '
    + 'would have been worth');

  // The user-facing half of the same promise, which is a real drawn string.
  assert.match(r.sample_note, /never what your account "would have been"/);
});

test('unreadable rows are skipped, never counted as compliant', () => {
  const r = replay([{ id: 'size_max', value: 1 }], [
    T({ margin: 0, pnl: 10 }),        // unreadable margin
    T({ margin: 500, pnl: 0 }),       // 5% > 1% → refused
  ], 10000);
  assert.strictEqual(r.checked, 1, 'the unreadable row is not checked');
  assert.strictEqual(r.refused_count, 1);
  assert.strictEqual(r.allowed_count, 0);
});

test('an empty history reports emptiness, never a clean bill', () => {
  const r = replay([{ id: 'size_max', value: 5 }], [], 10000);
  assert.strictEqual(r.checked, 0);
  assert.strictEqual(r.refused_count, 0);
  assert.strictEqual(r.refused_rom_pct_avg, null, 'no data → null, never 0');
});

test('the endpoint compiles server-side, replays, and arms nothing', async () => {
  for (const t of [
    { symbol: 'BTCUSDT', direction: 'LONG', margin: 100, pnl: 10 },
    { symbol: 'DOGEUSDT', direction: 'LONG', margin: 100, pnl: -50 },
    { symbol: 'BTCUSDT', direction: 'SHORT', margin: 100, pnl: 20 },
  ]) {
    await pool.execute(
      'INSERT INTO arena_trades (user_id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, trade_key, seal, seal_payload, sealed_at, opened_at, closed_at, signal_key, agent_slug) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [uid, t.symbol, t.direction, 100, 110, t.margin, 2, t.pnl, 'manual',
        null, null, null, null, new Date(), new Date(), null, null]);
  }
  const r = await req('POST', '/api/arena/envelope/preview',
    { text: 'only majors, no shorts, min confidence 70%' });
  assert.strictEqual(r.status, 200, JSON.stringify(r.body));
  assert.strictEqual(r.body.armed, false, 'a preview arms nothing');
  assert.strictEqual(r.body.checked, 3);
  assert.strictEqual(r.body.refused_count, 2, 'the DOGE open and the SHORT');
  assert.deepEqual(Object.keys(r.body.by_rule).sort(), ['dir_long', 'scope_majors']);
  assert.ok(r.body.not_enforced_here.includes('conf_min'),
    'the preview states what it does NOT enforce, like the arm does');
  assert.match(r.body.sample_note, /changes everything after it/);
  // and nothing was stored
  const g = await req('GET', '/api/arena/envelope');
  assert.strictEqual(g.body.armed, false);
});

test('the intent page previews before it binds', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'intent.html'), 'utf8');
  assert.match(html, /envelope\/preview/);
  assert.match(html, /prevBtn/);
  assert.match(html, /never\s+\/\/ claims what the account "would have been"/);
  const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  for (const k of ['in.prev_b', 'in.prev_h', 'in.prev_avg', 'in.prev_note', 'in.prev_none']) {
    assert.ok(i18n.includes(`'${k}'`), `${k} in the dictionary`);
  }
});
