'use strict';
/**
 * GET /api/call/latest — the endpoint behind the front door's receipt.
 *
 * Two things are worth driving here rather than reading.
 *
 * ROUTE ORDER. `KEY_RE` matches the literal string "latest", so if `/latest`
 * were registered after `/:key` it would be swallowed by the lookup and
 * answer 404 "No sealed call with that id" — which reads as "we have no
 * receipts" rather than "you hit the wrong route". A landing widget would
 * then correctly hide itself forever, and the page would silently lose its
 * strongest claim with nothing failing anywhere.
 *
 * ABSENT vs UNREADABLE. An empty table and an unreachable database are
 * different facts and get different codes: 404 says "we looked, there are
 * none yet", 503 says "we could not look". Collapsing them would let an
 * outage present as "this engine has never made a call".
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/call', require('../routes/call'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

function req(method, p, { body, headers } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: Object.assign(
        payload ? { 'Content-Type': 'application/json' } : {}, headers || {}),
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => {
        let j = null;
        try { j = JSON.parse(d); } catch (_) { /* non-JSON body */ }
        resolve({ status: res.statusCode, body: j, raw: d,
          cache: res.headers['cache-control'] });
      });
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

const BOT = { 'X-Bot-Secret': 's'.repeat(48) };

const CALL = {
  signal_key: 'sig:btc:latest-test',
  symbol: 'BTC/USDT', direction: 'LONG', status: 'NEW',
  entry_price: 64210.5, stop_loss: 63400, take_profit: 65900,
  confidence: 72, pattern: 'breakout', regime: 'trend',
  created_at: new Date().toISOString(),
};

// ── the route resolves at all ─────────────────────────────────────────────

test('/latest is not swallowed by the /:key lookup', async () => {
  const r = await req('GET', '/api/call/latest');
  assert.notStrictEqual(r.status, 400, 'a 400 means KEY_RE rejected "latest"');
  if (r.status === 404) {
    // The empty-table answer is its own sentence, distinct from the
    // per-key miss that /:key would have produced.
    assert.match(String(r.body && r.body.error), /No sealed call yet/,
      'a 404 here must be the empty-table answer, not the per-key miss');
    assert.ok(!/with that id/.test(String(r.body && r.body.error)),
      'that wording proves the request fell through to /:key');
  }
});

test('an absent receipt is a 404 that says so, never an empty 200', async () => {
  const r = await req('GET', '/api/call/latest');
  assert.notStrictEqual(r.status, 200, 'nothing is sealed yet in this fixture');
  assert.ok(r.body && r.body.error, 'and it names the reason');
  assert.ok(!r.body.seal, 'no seal field on a failure');
});

// ── once a call exists, it comes back whole ───────────────────────────────

test('a sealed call is served complete, with a verifiable seal', async () => {
  // No skip branch. An earlier draft fell back to assert.ok(true) when the
  // in-memory shim could not serve the query — which would have passed
  // silently the day the endpoint broke. Verified by hand that this fixture
  // does seed and does serve, so it is asserted.
  const seeded = await req('POST', '/api/bot/sync/signals',
    { headers: BOT, body: { signals: [CALL] } });
  assert.strictEqual(seeded.status, 200, 'the fixture seeds a signal');

  const r = await req('GET', '/api/call/latest');
  assert.strictEqual(r.status, 200, 'and the endpoint serves it');
  assert.match(String(r.body.seal), /^[0-9a-f]{64}$/, 'a real sha256');
  assert.ok(r.body.current && r.body.current.signal_key, 'carries a key to verify');
  assert.strictEqual(r.body.current.symbol, 'BTC/USDT');
  assert.strictEqual(r.body.kind, 'signal');
  assert.ok(r.body.seal_payload, 'the exact sealed string, for re-derivation');
  assert.match(String(r.cache || ''), /max-age/, 'the busiest page gets a cache header');
});

test('the served seal actually verifies', async () => {
  // The whole promise of the card is "check this yourself", so the test does
  // exactly what the visitor's browser will do rather than trusting the field.
  const r = await req('GET', '/api/call/latest');
  assert.strictEqual(r.status, 200);
  const derived = require('node:crypto')
    .createHash('sha256').update(r.body.seal_payload, 'utf8').digest('hex');
  assert.strictEqual(derived, r.body.seal,
    'sha256(seal_payload) must equal seal — the receipt is the claim');
});

// ── the public-surface rule ───────────────────────────────────────────────

test('the receipt carries no dollar figure', async () => {
  const r = await req('GET', '/api/call/latest');
  assert.strictEqual(r.status, 200);
  const flat = JSON.stringify(r.body);
  assert.ok(!/"pnl"/.test(flat), 'pnl never reaches a public receipt');
  assert.ok(!/"margin"/.test(flat));
});

// ── the wiring, which behaviour here cannot reach ─────────────────────────

test('/latest is registered before /:key in the source', () => {
  // Behavioural coverage above can only prove the route answers TODAY. This
  // pins the ordering that makes it answer at all, because swapping the two
  // registrations is a silent, plausible-looking edit.
  const src = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'routes', 'call.js'), 'utf8');
  const latest = src.indexOf("router.get('/latest'");
  const byKey = src.indexOf("router.get('/:key'");
  assert.ok(latest > 0 && byKey > 0, 'both routes exist');
  assert.ok(latest < byKey,
    '/latest must be registered first or /:key captures the word "latest"');
});

test('an unreadable database is a 503, and never an empty success', () => {
  const src = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'routes', 'call.js'), 'utf8');
  const fn = src.slice(src.indexOf("router.get('/latest'"), src.indexOf("router.get('/:key'"));
  const code = fn.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.match(code, /catch[\s\S]*?status\(503\)/, 'a read failure is a 503');
  assert.ok(!/res\.json\(\{\s*\}\)/.test(code), 'never an empty 200 body');
  assert.match(code, /err\.stack/, 'the log carries a stack');
});
