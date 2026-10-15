'use strict';
/**
 * The pages retry the site's own "starting" refusal, and nothing else.
 *
 * THE FINDING (2026-10-11). The web host sends a new instance traffic before it
 * has checked its database, and until it has, the not-ready gate in server.js
 * answers every /api/ call 503 {error: 'starting', reason: 'starting'}. Sampled
 * live: 8 of 55 calls in one minute. public/js/api-retry.js, the first script
 * on every page, retries exactly that answer, so the call reaches the instance
 * beside it that is ready.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const http = require('node:http');
const express = require('express');
const { codeOnly } = require('./helpers/code_only');

const PUBLIC = path.join(__dirname, '..', 'public');
const SCRIPT = path.join(PUBLIC, 'js', 'api-retry.js');
const R = require('../public/js/api-retry');

const ORIGIN = 'https://www.humanoid-traders.com';
const json = (status, body) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});
const STARTING = () => json(503, { error: 'starting', reason: 'starting' });

/** A fetch that answers from a script, recording what it was asked. */
function scripted(...answers) {
  const calls = [];
  const fetchImpl = async (input, init) => {
    calls.push({ input, init });
    const next = answers.length > 1 ? answers.shift() : answers[0];
    return next();
  };
  return { fetchImpl, calls };
}

function wrapped(fake, extra = {}) {
  const slept = [];
  const f = R.wrap(fake.fetchImpl, { origin: ORIGIN, sleep: async (ms) => { slept.push(ms); if (extra.onSleep) extra.onSleep(ms); }, ...extra });
  return { f, slept };
}

// ── what is retried ──────────────────────────────────────────────────────

test('a fresh instance\'s starting answer is retried, and the ready answer reaches the page', async () => {
  const fake = scripted(STARTING, () => json(200, { ok: 1 }));
  const { f, slept } = wrapped(fake);
  const res = await f('/api/token');
  assert.strictEqual(res.status, 200);
  assert.deepStrictEqual(await res.json(), { ok: 1 });
  assert.strictEqual(fake.calls.length, 2);
  assert.deepStrictEqual(slept, [R.RETRY_DELAYS_MS[0]]);
});

test('when every retry meets starting too, the last refusal reaches the page with its body intact', async () => {
  const fake = scripted(STARTING);
  const { f, slept } = wrapped(fake);
  const res = await f('/api/portfolio');
  assert.strictEqual(res.status, 503);
  assert.deepStrictEqual(await res.json(), { error: 'starting', reason: 'starting' });
  assert.strictEqual(fake.calls.length, R.RETRY_DELAYS_MS.length + 1);
  assert.deepStrictEqual(slept, R.RETRY_DELAYS_MS);
  const waited = R.RETRY_DELAYS_MS.reduce((a, b) => a + b, 0);
  assert.ok(waited <= 3000, `${waited} ms of waiting: the pages give a call 8-10 s in all`);
});

test('a database that is failing is not retried: every instance shares it', async () => {
  for (const reason of ['db_timeout', 'db_unreachable', 'db_error']) {
    const fake = scripted(() => json(503, { error: 'starting', reason }));
    const res = await wrapped(fake).f('/api/portfolio');
    assert.strictEqual(res.status, 503);
    assert.strictEqual(fake.calls.length, 1, reason);
    // The page still reads the reason it should show: looking did not consume it.
    assert.deepStrictEqual(await res.json(), { error: 'starting', reason });
  }
});

test('any other answer is the page\'s, untouched: another 503, a 502, a 429, a 200', async () => {
  for (const make of [
    () => json(503, { error: 'bot_unavailable' }),
    () => new Response('<html>busy</html>', { status: 503 }),
    () => json(502, { error: 'starting', reason: 'starting' }),
    () => json(429, { error: 'rate_limited' }),
    () => json(200, { error: 'starting', reason: 'starting' }),
  ]) {
    const fake = scripted(make);
    await wrapped(fake).f('/api/x');
    assert.strictEqual(fake.calls.length, 1);
  }
});

test('only this site\'s own /api/ is retried', async () => {
  const cases = [
    ['/api/token', 2],
    [`${ORIGIN}/api/token`, 2],
    [new URL('/api/token', ORIGIN), 2],
    ['https://api.example.com/api/token', 1],
    ['/js/app.js', 1],
    ['/apiary', 1],
  ];
  for (const [input, n] of cases) {
    const fake = scripted(STARTING, () => json(200, {}));
    await wrapped(fake).f(input);
    assert.strictEqual(fake.calls.length, n, String(input));
  }
});

test('a POST with a plain body is sent again unchanged; a Request object or a stream is not', async () => {
  const init = { method: 'POST', body: JSON.stringify({ a: 1 }), headers: { 'Content-Type': 'application/json' } };
  const fake = scripted(STARTING, () => json(200, {}));
  await wrapped(fake).f('/api/controls/mode', init);
  assert.strictEqual(fake.calls.length, 2);
  assert.strictEqual(fake.calls[1].init, init);

  const asRequest = scripted(STARTING, () => json(200, {}));
  await wrapped(asRequest).f(new Request(`${ORIGIN}/api/x`, { method: 'POST', body: 'x' }));
  assert.strictEqual(asRequest.calls.length, 1);

  const stream = scripted(STARTING, () => json(200, {}));
  await wrapped(stream).f('/api/x', { method: 'POST', body: new ReadableStream(), duplex: 'half' });
  assert.strictEqual(stream.calls.length, 1);
});

test('a caller that aborts stops the retries, during the wait or before it', async () => {
  const ctrl = new AbortController();
  const fake = scripted(STARTING);
  const { f } = wrapped(fake, { onSleep: () => ctrl.abort() });
  const res = await f('/api/portfolio', { signal: ctrl.signal });
  assert.strictEqual(res.status, 503);
  assert.strictEqual(fake.calls.length, 1);

  const early = new AbortController();
  const answered = scripted(() => { early.abort(); return STARTING(); });
  const w = wrapped(answered);
  await w.f('/api/portfolio', { signal: early.signal });
  assert.deepStrictEqual([answered.calls.length, w.slept], [1, []], 'it waited for a caller that had gone');
});

// ── the answer it keys on is the gate's ──────────────────────────────────

test('the gate is the one place that answers starting, before any route runs', () => {
  const APP = path.join(__dirname, '..');
  const files = ['server.js', 'auth.js']
    .concat(fs.readdirSync(path.join(APP, 'routes')).map((f) => `routes/${f}`))
    .concat(fs.readdirSync(path.join(APP, 'lib')).map((f) => `lib/${f}`))
    .filter((f) => f.endsWith('.js'));
  const sites = [];
  for (const f of files) {
    const src = codeOnly(fs.readFileSync(path.join(APP, f), 'utf8'));
    if (/error:\s*'starting'/.test(src)) sites.push(f);
  }
  // Retrying a POST is safe only because nothing ran: a route that did work and
  // then answered `starting` would be run twice.
  assert.deepStrictEqual(sites, ['lib/not_ready_gate.js']);
  const server = codeOnly(fs.readFileSync(path.join(APP, 'server.js'), 'utf8'));
  const gateAt = server.indexOf('app.use(notReadyGate(');
  assert.ok(gateAt > 0 && gateAt < server.indexOf("app.use('/api/auth'"),
    'the gate must sit ahead of every API router');
});

/** The real gate, unheld, in a bare app with one /api/ route. */
async function gateServer(opts = {}) {
  const { notReadyGate } = require('../lib/not_ready_gate');
  const readiness = require('../lib/readiness');
  const app = express();
  app.use(notReadyGate({ readiness, holdMs: 0, ...opts }));
  app.get('/api/token', (req, res) => res.json({ ok: 1 }));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, url: `http://127.0.0.1:${server.address().port}/api/token` };
}

test('a fresh instance\'s refusal is exactly what the retry keys on', async () => {
  const readiness = require('../lib/readiness');
  readiness._reset();
  const g = await gateServer();
  try {
    const res = await fetch(g.url);
    assert.ok(await R.isStarting(res), `${res.status} ${await res.clone().text()} would not be retried`);
    assert.ok(!R.wasHeld(res), 'an unheld refusal is retried by the page');
  } finally { g.server.close(); readiness._reset(); }
});

// ── it is loaded, first, everywhere ──────────────────────────────────────

/** The script tags of a page, in order, outside HTML comments. */
function scriptsOf(html) {
  return [...html.replace(/<!--[\s\S]*?-->/g, '').matchAll(/<script\b([^>]*)>/gi)].map((m) => m[1]);
}

test('every page loads it as its first script, and blocking', () => {
  const pages = fs.readdirSync(PUBLIC).filter((f) => f.endsWith('.html'));
  assert.ok(pages.length >= 40, `${pages.length} pages`);
  for (const p of pages) {
    const tags = scriptsOf(fs.readFileSync(path.join(PUBLIC, p), 'utf8'));
    if (!tags.length) continue;
    assert.match(tags[0], /src="\/js\/api-retry\.js\?v=\d+"/, `${p}: first script is ${tags[0]}`);
    assert.doesNotMatch(tags[0], /\b(async|defer|type=)/, `${p}: it must run before the page's own scripts`);
  }
});

async function getHtml(router, mount, p) {
  const app = express();
  app.use(mount, router);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  try {
    const r = await fetch(`http://127.0.0.1:${server.address().port}${mount}${p}`);
    return await r.text();
  } finally {
    server.close();
  }
}

test('the embed and Mini App pages load it first too', async () => {
  for (const [mount, file, pages] of [
    ['/embed', 'embed', ['/signals', '/arena', '/chart']],
    ['/miniapp', 'miniapp', ['/arena']],
  ]) {
    const router = require(`../routes/${file}`);
    for (const p of pages) {
      const tags = scriptsOf(await getHtml(router, mount, p));
      assert.ok(tags.length, `${mount}${p} has no scripts`);
      assert.match(tags[0], /src="\/js\/api-retry\.js\?v=\d+"/, `${mount}${p}: first script is ${tags[0]}`);
    }
  }
});

test('in a page it wraps fetch once, however many times it is loaded', async () => {
  const src = fs.readFileSync(SCRIPT, 'utf8');
  const fake = scripted(STARTING);
  const window = { fetch: fake.fetchImpl, location: { origin: ORIGIN } };
  const ctx = vm.createContext({
    window, URL, Response, AbortController, setTimeout: (fn) => setImmediate(fn),
  });
  vm.runInContext(src, ctx);
  vm.runInContext(src, ctx);
  assert.strictEqual(window.fetch.__rcStartingRetry, true);
  await window.fetch('/api/token');
  assert.strictEqual(fake.calls.length, R.RETRY_DELAYS_MS.length + 1, 'wrapped twice multiplies the retries');
});

// ── the cost, where an operator can read it ──────────────────────────────

test('a booted instance records how long its database check took and when it became ready', { timeout: 60000 }, async (t) => {
  const { spawn } = require('node:child_process');
  const port = 3500 + (process.pid % 100);
  const env = { ...process.env, PORT: String(port), JWT_SECRET: 'j'.repeat(48), BOT_SYNC_SECRET: 'b'.repeat(48), DIAG_TOKEN: 'd'.repeat(32) };
  delete env.DATABASE_URL;
  const child = spawn(process.execPath, ['server.js'], { cwd: path.join(__dirname, '..'), env, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = '';
  child.stdout.on('data', (d) => { log += d; });
  child.stderr.on('data', (d) => { log += d; });
  t.after(() => child.kill('SIGKILL'));
  let ok = null;
  for (let i = 0; i < 80 && !ok; i += 1) {
    await new Promise((r) => setTimeout(r, 250));
    try {
      const r = await fetch(`http://127.0.0.1:${port}/diagz`, { headers: { 'X-Diag-Token': 'd'.repeat(32) } });
      if (r.status !== 200) continue;
      const body = await r.json();
      ok = (body.events || []).find((e) => e.event === 'migration_ok') || null;
    } catch (_) { /* not listening yet */ }
  }
  assert.ok(ok, `no migration_ok event. Log:\n${log}`);
  assert.match(ok.detail, /^after_attempts=0 path=memory check_ms=\d+ ready_after_ms=\d+$/);
});
