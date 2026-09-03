'use strict';
/**
 * The tripwire engine failed silently. `runOnce` caught the whole pass and
 * returned 0, the interval swallowed the rest, and lib/alerts.js contained no
 * log line at all -- so a dead ticker feed or a lost DB meant every user's
 * alerts stopped firing while the panel kept showing green "armed" badges.
 * "armed" is a claim about the future, and it is only true while something
 * evaluates the alert every minute.
 *
 * Now the engine records what it did, warns once and then every ten minutes,
 * /api/alerts reports it, and the panel says when armed is not being watched.
 * Same shape and same fix as the bot's proactive monitor, one commit earlier.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const express = require('express');
const authModule = require('../auth');
const alerts = require('../lib/alerts');

let server, base;
const TICKERS = { BTCUSDT: { price: 98_000, change: -2.5 } };

test.before(async () => {
  alerts.setTickerFetcher(async () => TICKERS);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/alerts', require('../routes/alerts'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

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
      res.on('data', (c) => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}
let seq = 0;
async function newUser() {
  seq++;
  const r = await req('POST', '/api/auth/register', {
    body: { email: `engine${seq}@example.com`, password: 'longenough1' },
  });
  assert.equal(r.status, 200);
  return r.data.token;
}

function withWarnCapture(fn) {
  const calls = [];
  const orig = console.warn;
  console.warn = (...a) => calls.push(a.join(' '));
  return Promise.resolve().then(fn).finally(() => { console.warn = orig; }).then((v) => ({ v, calls }));
}

// ── the accounting ───────────────────────────────────────────────────────

test('a clean pass is recorded as ok, even when there is nothing to evaluate', async () => {
  alerts.__testResetEngineState();
  assert.equal(alerts.engineStatus().last_ok_at, null, 'never ran → null, not a timestamp');
  await alerts.runOnce(async () => {});
  const s = alerts.engineStatus();
  assert.ok(s.last_ok_at, 'an empty table is still a completed pass');
  assert.equal(s.consecutive_failures, 0);
  assert.equal(s.last_error, null);
  assert.equal(s.running, false, 'startAlertEngine was never called here');
});

test('a dead ticker feed is counted, named, and warned about once -- not every minute', async () => {
  alerts.__testResetEngineState();
  const token = await newUser();
  await req('POST', '/api/alerts', { token, body: { symbol: 'BTC', metric: 'price', op: '<', threshold: 99000 } });
  alerts.setTickerFetcher(async () => { throw new Error('feed down'); });
  try {
    const { v: first, calls } = await withWarnCapture(() => alerts.runOnce(async () => {}));
    assert.equal(first, 0);
    assert.equal(calls.length, 1, 'the first failure is logged');
    assert.match(calls[0], /feed down/);
    assert.match(calls[0], /not being evaluated/);
    const { calls: again } = await withWarnCapture(async () => {
      await alerts.runOnce(async () => {});
      await alerts.runOnce(async () => {});
    });
    assert.equal(again.length, 0, 'a still-failing engine is not re-logged every pass');
    const s = alerts.engineStatus();
    assert.equal(s.consecutive_failures, 3);
    assert.match(s.last_error, /feed down/);
    assert.ok(s.last_run_at, 'it did run; it did not succeed');
    assert.equal(s.last_ok_at, null, 'no pass has completed since the reset');
  } finally {
    alerts.setTickerFetcher(async () => TICKERS);
  }
  // Recovery clears the record and the alert fires on the next pass.
  const pushes = [];
  const tripped = await alerts.runOnce(async (p) => pushes.push(p));
  assert.equal(tripped, 1);
  const s = alerts.engineStatus();
  assert.equal(s.consecutive_failures, 0);
  assert.equal(s.last_error, null);
  assert.ok(s.last_ok_at);
});

test('the route reports the engine beside the alerts', async () => {
  alerts.__testResetEngineState();
  await alerts.runOnce(async () => {});
  const token = await newUser();
  const r = await req('GET', '/api/alerts', { token });
  assert.equal(r.status, 200);
  const e = r.data.engine;
  assert.ok(e && typeof e === 'object', 'engine is part of the payload');
  for (const k of ['running', 'last_run_at', 'last_ok_at', 'consecutive_failures', 'last_error']) {
    assert.ok(k in e, `engine.${k}`);
  }
  assert.equal(e.consecutive_failures, 0);
});

// ── the panel's words ────────────────────────────────────────────────────

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
function copyFn() {
  const start = dash.indexOf('function alertEngineCopy(engine, nowMs)');
  const end = dash.indexOf('// end alertEngineCopy');
  assert.ok(start > 0 && end > start, 'alertEngineCopy seam not found');
  const ctx = {}; vm.createContext(ctx);
  vm.runInContext(dash.slice(start, end) + '\nthis.f = alertEngineCopy;', ctx);
  return ctx.f;
}
const NOW = Date.parse('2026-09-03T12:00:00Z');
const iso = (secAgo) => new Date(NOW - secAgo * 1000).toISOString();

test('no engine field, or an unreadable one, makes no claim', () => {
  const f = copyFn();
  assert.equal(f(undefined, NOW), null);
  assert.equal(f(null, NOW), null);
  assert.equal(f('weird', NOW), null);
  assert.equal(f({ running: true }, NOW), null, 'a count that cannot be read is not zero failures');
});

test('a fresh clean pass reads as a quiet fact, not a warning', () => {
  const out = copyFn()({ running: true, last_ok_at: iso(20), consecutive_failures: 0, last_error: null }, NOW);
  assert.equal(out.tone, 'muted');
  assert.match(out.text, /20s ago/);
});

test('failures say the engine is not watching, and when it last did', () => {
  const out = copyFn()({ running: true, last_ok_at: iso(600), consecutive_failures: 9, last_error: 'feed down' }, NOW);
  assert.equal(out.tone, 'danger');
  assert.match(out.text, /not evaluated alerts since 10m ago/);
  assert.match(out.text, /9 failed passes/);
  assert.match(out.text, /feed down/);
  assert.match(out.text, /not being watched/);
});

test('failures with no clean pass on record say "yet", not a made-up time', () => {
  const out = copyFn()({ running: true, last_ok_at: null, consecutive_failures: 2, last_error: 'db' }, NOW);
  assert.equal(out.tone, 'danger');
  assert.match(out.text, /alerts yet/);
});

test('a stopped engine is said outright', () => {
  const out = copyFn()({ running: false, last_ok_at: iso(5), consecutive_failures: 0, last_error: null }, NOW);
  assert.equal(out.tone, 'danger');
  assert.match(out.text, /not running/);
});

test('a stale clean pass is a warning, a never-run engine is a neutral note', () => {
  const f = copyFn();
  assert.equal(f({ running: true, last_ok_at: iso(900), consecutive_failures: 0 }, NOW).tone, 'warn');
  const never = f({ running: true, last_ok_at: null, consecutive_failures: 0 }, NOW);
  assert.equal(never.tone, 'muted');
  assert.match(never.text, /not completed a pass yet/);
});

test('the panel takes its line from the seam and demotes the badge under a dead engine', () => {
  const i = dash.indexOf('async function loadAlertList()');
  assert.ok(i > 0);
  const block = dash.slice(i, dash.indexOf('function wireAlertsPanel()', i));
  assert.ok(block.includes('alertEngineCopy(r.data && r.data.engine'), 'the panel must read the engine through the seam');
  assert.ok(block.includes("armed · not being evaluated"), 'a green "armed" under a dead engine is a false claim');
  assert.ok(block.includes('engHtml +'), 'the engine line precedes both the empty and the populated list');
});
