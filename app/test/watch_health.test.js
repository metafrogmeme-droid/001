'use strict';
/**
 * Every background watcher reports its own liveness.
 *
 * Six sweeps ran under `.catch(() => {})` with no log line and no surface --
 * the tripwire engine's old shape, one commit earlier. The arena liquidation
 * watch is the one that matters: a dead ticker feed ended near-liquidation
 * warnings with a bare `return`, and a calm paper floor and a dead feed read
 * the same. Now each sweep records ok / failed / skipped in a shared
 * registry, the first failure is logged and then every ten minutes, and
 * /diagz serves the snapshot.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const wh = require('../lib/watch_health');

function withWarnCapture(fn) {
  const calls = [];
  const orig = console.warn;
  console.warn = (...a) => calls.push(a.join(' '));
  return Promise.resolve().then(fn).finally(() => { console.warn = orig; }).then((v) => ({ v, calls }));
}

// ── the registry ─────────────────────────────────────────────────────────

test('a registered watcher that has not completed a pass says never, not ok', () => {
  wh.__testReset();
  wh.register('x');
  const s = wh.snapshot().x;
  assert.equal(s.state, 'never');
  assert.equal(s.last_ok_at, null);
  assert.equal(s.consecutive_failures, 0);
});

test('ok, failed and skipped are three states, each said', async () => {
  wh.__testReset();
  const h = wh.register('x');
  h.ok();
  assert.equal(wh.snapshot().x.state, 'ok');
  const { calls } = await withWarnCapture(async () => { h.failed(new Error('feed down')); h.failed(new Error('feed down')); });
  const f = wh.snapshot().x;
  assert.equal(f.state, 'failed');
  assert.equal(f.consecutive_failures, 2);
  assert.match(f.last_error, /feed down/);
  assert.ok(f.last_ok_at, 'the last clean pass is kept, so the outage has a start');
  assert.equal(calls.length, 1, 'logged once, not once per pass');
  assert.match(calls[0], /Watcher x pass failed/);
  h.skipped('push not configured');
  const s = wh.snapshot().x;
  assert.equal(s.state, 'skipped');
  assert.equal(s.skipped_reason, 'push not configured');
  h.ok();
  assert.equal(wh.snapshot().x.state, 'ok');
  assert.equal(wh.snapshot().x.last_error, null);
});

test('registering twice returns the same accounting', () => {
  wh.__testReset();
  wh.register('y').ok();
  wh.register('y').failed(new Error('e'));
  assert.equal(wh.snapshot().y.consecutive_failures, 1);
});

// ── the arena liquidation watch, the one that matters ────────────────────

test('a dead ticker feed is a failed pass of the arena watch, not a calm floor', async () => {
  wh.__testReset();
  const { pool } = require('../db');
  const arena = require('../lib/arena_watch');
  await pool.execute(
    'INSERT INTO arena_positions (user_id, symbol, direction, entry, margin, leverage, source, tp, sl, trade_key, seal, seal_payload, sealed_at, opened_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
    [7001, 'BTCUSDT', 'LONG', 100, 100, 10, 'manual', null, null, null, null, null, null, new Date()]);
  const push = { isConfigured: () => true, notifySubscribers: async () => {} };
  const { calls } = await withWarnCapture(() => arena.runOnce({ push, getTickers: async () => { throw new Error('tickers unavailable'); } }));
  const s = wh.snapshot().arena_watch;
  assert.equal(s.state, 'failed');
  assert.match(s.last_error, /tickers unavailable/);
  assert.equal(calls.length, 1);
  assert.match(calls[0], /arena_watch/);
  // ...and a working feed on the next pass clears it.
  await arena.runOnce({ push, getTickers: async () => ({ BTCUSDT: { price: 100 } }) });
  const ok = wh.snapshot().arena_watch;
  assert.equal(ok.state, 'ok');
  assert.equal(ok.consecutive_failures, 0);
});

test('push not configured is skipped, said as such, and not a failure', async () => {
  wh.__testReset();
  const arena = require('../lib/arena_watch');
  await arena.runOnce({ push: { isConfigured: () => false, notifySubscribers: async () => {} }, getTickers: async () => ({}) });
  const s = wh.snapshot().arena_watch;
  assert.equal(s.state, 'skipped');
  assert.match(s.skipped_reason, /push not configured/);
});

// ── every sweep is registered, and the operator can read it ─────────────

test('every sweep started by the server reports to the registry', () => {
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  const started = [...server.matchAll(/require\('\.\/lib\/([a-z_]+)'\)\.(start[A-Za-z]+)\(\)/g)].map((m) => m[1]);
  assert.ok(started.length >= 6, `expected the started sweeps, got ${started.join(',')}`);
  for (const lib of started) {
    const src = fs.readFileSync(path.join(__dirname, '..', 'lib', `${lib}.js`), 'utf8');
    const reports = src.includes("require('./watch_health').register(") || src.includes('engineStatus');
    assert.ok(reports, `${lib}.js is started by the server but reports no liveness`);
  }
});

test('/diagz serves the snapshot beside the tripwire engine', () => {
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  const i = server.indexOf("app.get('/diagz'");
  const block = server.slice(i, i + 3000);
  assert.ok(block.includes("require('./lib/watch_health').snapshot()"), 'watchers must be on the diagnosis endpoint');
  assert.ok(block.includes("require('./lib/alerts').engineStatus()"), 'so must the tripwire engine');
});
