'use strict';
/**
 * The Authority Envelope is ENFORCED — the Guardian principle running live
 * on the paper surface. The contract:
 * - rules compile SERVER-side from the user's own words (a client can never
 *   arm fabricated rules);
 * - only truly-enforceable rules gate opens; the rest are surfaced as
 *   not_enforced_here — the envelope never claims more than it delivers;
 * - every refusal names its rule with the numbers that tripped it;
 * - an armed envelope that cannot be read FAILS CLOSED (opens refused) —
 *   arming means something even mid-outage;
 * - disarming always works: revocable is the whole point.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('http');
const express = require('express');
const jwt = require('jsonwebtoken');
const { pool } = require('../db');
const { checkOpen, ENFORCEABLE } = require('../lib/envelope_guard');

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
    ['envelope@test.dev', 'x'.repeat(60)]);
  uid = u.insertId;
  token = jwt.sign({ user_id: uid, email: 'envelope@test.dev' }, process.env.JWT_SECRET);
  const { setTickerFetcher } = require('../lib/tickers');
  setTickerFetcher(async () => ({
    BTCUSDT: { price: 60000, change: 0, volume: 1 },
    DOGEUSDT: { price: 0.1, change: 0, volume: 1 },
  }));
  const app = express();
  app.use(express.json());
  app.use('/api/arena', require('../routes/arena'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

test('guard units: each enforceable rule refuses with its numbers', () => {
  const rules = [
    { id: 'size_max', value: 5 }, { id: 'dir_long', value: 'long_only' },
    { id: 'loss_dd', value: 8 }, { id: 'scope_majors', value: ['BTC', 'ETH'] },
    { id: 'lev_max', value: 5 }, { id: 'conf_min', value: 70 },
  ];
  const base_ = { symbol: 'BTCUSDT', direction: 'LONG', margin: 100, leverage: 3,
    balance: 10000, startBalance: 10000 };
  assert.strictEqual(checkOpen(rules, base_).ok, true);
  assert.deepEqual(checkOpen(rules, base_).unenforced, ['conf_min'],
    'the envelope never claims conf_min here');
  const size = checkOpen(rules, { ...base_, margin: 600 });
  assert.strictEqual(size.violations[0].id, 'size_max');
  assert.strictEqual(size.violations[0].params.pct, 6);
  const short = checkOpen(rules, { ...base_, direction: 'SHORT' });
  assert.strictEqual(short.violations[0].id, 'dir_long');
  const dd = checkOpen(rules, { ...base_, balance: 9100 });
  assert.strictEqual(dd.violations[0].id, 'loss_dd');
  assert.strictEqual(dd.violations[0].params.dd, 9);
  const scope = checkOpen(rules, { ...base_, symbol: 'DOGEUSDT' });
  assert.strictEqual(scope.violations[0].id, 'scope_majors');
  const lev = checkOpen(rules, { ...base_, leverage: 10 });
  assert.strictEqual(lev.violations[0].id, 'lev_max');
  assert.strictEqual(checkOpen([], base_).ok, true, 'no rules → open account');
});

test('arming compiles server-side and states what it does NOT enforce', async () => {
  const r = await req('PUT', '/api/arena/envelope',
    { text: 'only majors, max 5% per trade, no shorts, max leverage 5, min confidence 70%' });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.body.armed, true);
  assert.ok(r.body.enforced_here.includes('scope_majors'));
  assert.ok(r.body.not_enforced_here.includes('conf_min'),
    'unenforceable rules are stated, not silently claimed');
  const g = await req('GET', '/api/arena/envelope');
  assert.strictEqual(g.body.armed, true);
});

test('the armed envelope refuses a violating open, naming the rule', async () => {
  const bad = await req('POST', '/api/arena/open',
    { symbol: 'DOGEUSDT', direction: 'LONG', margin: 100, leverage: 3 });
  assert.strictEqual(bad.status, 400);
  assert.strictEqual(bad.body.code, 'ENVELOPE');
  assert.strictEqual(bad.body.violations[0].id, 'scope_majors');
  assert.match(bad.body.error, /DOGE/);
  const short = await req('POST', '/api/arena/open',
    { symbol: 'BTCUSDT', direction: 'SHORT', margin: 100, leverage: 3 });
  assert.strictEqual(short.status, 400);
  assert.strictEqual(short.body.violations[0].id, 'dir_long');
});

test('a compliant open passes through the armed envelope', async () => {
  const ok = await req('POST', '/api/arena/open',
    { symbol: 'BTCUSDT', direction: 'LONG', margin: 100, leverage: 3 });
  assert.strictEqual(ok.status, 200, JSON.stringify(ok.body));
});

test('disarming always works, and the account trades freely again', async () => {
  const d = await req('DELETE', '/api/arena/envelope');
  assert.strictEqual(d.body.armed, false);
  const doge = await req('POST', '/api/arena/open',
    { symbol: 'DOGEUSDT', direction: 'LONG', margin: 50, leverage: 2 });
  assert.strictEqual(doge.status, 200, 'no envelope → no gate');
});

test('the UI arms from the intent page and the arena translates refusals', () => {
  const fs = require('fs');
  const path = require('path');
  const intent = fs.readFileSync(path.join(__dirname, '..', 'public', 'intent.html'), 'utf8');
  assert.ok(intent.includes("'/api/arena/envelope'"), 'intent page talks to the envelope API');
  assert.match(intent, /armBtn/);
  assert.match(intent, /not_enforced_here/,
    'the armed card states what is NOT enforced — never claims more than it delivers');
  const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(arena, /code === 'ENVELOPE'/);
  assert.match(arena, /'ev\.' \+ v\.id/, 'refusals translate whole-line by rule id');
  assert.match(arena, /envChip/, 'the arena shows the armed state');
  const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  for (const id of ENFORCEABLE) {
    assert.ok(i18n.includes(`'ev.${id}'`), `ev.${id} is in the dictionary`);
  }
  // The dictionary's English fallback for a violation is the guard's own
  // template — the refusal text never forks between server and client.
  assert.ok(i18n.includes('caps a position at {max}%'));
});

/**
 * Fail the next `pool.execute` whose SQL matches, then restore.
 *
 * `pool` is the same object the route holds, so this reaches the exact read
 * the route makes without stubbing the module graph.
 */
async function withFailingRead(pattern, fn) {
  const real = pool.execute.bind(pool);
  pool.execute = async (sql, params) => {
    if (pattern.test(sql)) throw new Error('injected read failure');
    return real(sql, params);
  };
  try { return await fn(); } finally { pool.execute = real; }
}

test('an unreadable armed envelope fails CLOSED', async () => {
  // EXERCISED, NOT MATCHED. This asserted three phrases out of arena.js —
  // `fail CLOSED`, `opens are refused until it can be`, and the season read's
  // fail-open note. All three are COMMENTS. Deleting the try/catch that
  // implements the refusal left every one of them in place, so the test that
  // guards the envelope's central promise passed against a route that had
  // stopped keeping it.
  //
  // Found by stripping comments from every shipped source and re-running the
  // suite: 41 of 2,955 tests failed, and this was one of them.
  const armed = await req('PUT', '/api/arena/envelope', { text: 'only majors, max leverage 5' });
  assert.strictEqual(armed.body.armed, true, 'the fixture envelope did not arm');

  const refused = await withFailingRead(/FROM arena_envelopes/i, () =>
    req('POST', '/api/arena/open',
      { symbol: 'BTCUSDT', direction: 'LONG', margin: 10, leverage: 2 }));
  assert.strictEqual(refused.status, 503, JSON.stringify(refused.body));
  assert.match(refused.body.error, /could not be read/i,
    'the refusal must say the envelope was unreadable, not invent another reason');
  assert.match(refused.body.error, /fail closed/i);
});

test('a season read failure does NOT block an open', async () => {
  // THE OTHER HALF, and the reason the first one is not simply "wrap
  // everything in a refusal". A season is a competition rule; an envelope is
  // the user's own authority. Only one of them earns a refusal when it cannot
  // be read, and a test that only pinned the strict side would be satisfied by
  // a route that refuses on both.
  await req('DELETE', '/api/arena/envelope');
  const ok = await withFailingRead(/FROM arena_seasons/i, () =>
    req('POST', '/api/arena/open',
      { symbol: 'BTCUSDT', direction: 'LONG', margin: 10, leverage: 2 }));
  assert.notStrictEqual(ok.status, 503,
    'an unreadable SEASON now blocks opens — the two reads have been collapsed '
    + 'into one policy and the envelope no longer means anything specific');
});
