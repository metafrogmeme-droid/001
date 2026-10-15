'use strict';
/**
 * A new instance holds a call for its first database check instead of
 * refusing it, and a page waits out what is left.
 *
 * Reported 15 October with a screenshot of the dashboard: every panel said
 * "The website's database is not ready yet" under a Retry button, and the
 * topbar said ENGINE STATUS UNKNOWN. The web host starts a new instance every
 * minute or two and sends it calls before its first check has answered; the
 * gate refused those at once, the pages' own retry (2.3 s) rescued about 4 in
 * 10, and each panel then waited for a person to press Retry.
 *
 * Driven here, on the real gate (lib/not_ready_gate.js) and the real readiness
 * module, over HTTP:
 *  - a call that arrives while the first check runs is answered by its route
 *    once the instance is ready, and is refused (marked held) only when the
 *    hold runs out;
 *  - a database that is failing is never held: refused at once, and a held
 *    call is answered the moment an attempt fails;
 *  - a caller that hangs up while held runs no route;
 *  - past `maxHeld` calls, the gate refuses as it did before.
 * And on the client: the page does not spend its retries on a held refusal,
 * and a panel retries a fresh instance's refusal on its own schedule but
 * never a failing database's.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');

const readiness = require('../lib/readiness');
const G = require('../lib/not_ready_gate');
const R = require('../public/js/api-retry');
const M = require('../public/js/panel-error-model');
const { codeOnly } = require('./helpers/code_only');

async function gateServer(opts) {
  const ran = [];
  const app = express();
  app.use(G.notReadyGate({ readiness, ...opts }));
  app.all('/api/data', (req, res) => { ran.push(req.method); res.json({ ok: 1 }); });
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${server.address().port}/api/data`;
  return { server, url, ran };
}

async function timed(p) {
  const t0 = Date.now();
  const res = await p;
  return { res, ms: Date.now() - t0, body: await res.json() };
}

const refusedErr = () => Object.assign(new Error('connect ECONNREFUSED'), { code: 'ECONNREFUSED' });
const tick = (ms) => new Promise((r) => setTimeout(r, ms));

test.beforeEach(() => readiness._reset());
test.afterEach(() => readiness._reset());

// ── the hold ──────────────────────────────────────────────────────────────

test('a call that arrives while the first check runs is answered once the instance is ready', async () => {
  const g = await gateServer({ holdMs: 3000 });
  try {
    setTimeout(() => readiness.markReady(), 150);
    const { res, ms, body } = await timed(fetch(g.url, { method: 'POST', body: '{}' }));
    assert.equal(res.status, 200);
    assert.deepEqual(body, { ok: 1 });
    assert.deepEqual(g.ran, ['POST'], 'the route ran once, after the hold');
    assert.ok(ms >= 100 && ms < 2000, `answered when ready, not at the end of the hold (${ms} ms)`);
  } finally { g.server.close(); }
});

test('a hold that runs out is refused as starting, marked held', async () => {
  const g = await gateServer({ holdMs: 300 });
  try {
    const { res, ms, body } = await timed(fetch(g.url));
    assert.equal(res.status, 503);
    assert.deepEqual(body, { error: 'starting', reason: 'starting' });
    assert.equal(res.headers.get(G.HELD_HEADER), '1');
    assert.equal(res.headers.get('retry-after'), '15');
    assert.ok(ms >= 280, `it held the call (${ms} ms)`);
    assert.deepEqual(g.ran, [], 'no route runs for a refused call');
  } finally { g.server.close(); }
  // The other arm: with the hold off, the same call is refused at once, unheld.
  const off = await gateServer({ holdMs: 0 });
  try {
    const { res, ms } = await timed(fetch(off.url));
    assert.equal(res.status, 503);
    assert.equal(res.headers.get(G.HELD_HEADER), null);
    assert.ok(ms < 250, `${ms} ms`);
  } finally { off.server.close(); }
});

test('a failing database is never held, and a held call hears of a failed attempt at once', async () => {
  readiness.markAttemptFailed(refusedErr());
  const g = await gateServer({ holdMs: 3000 });
  try {
    const { res, ms, body } = await timed(fetch(g.url));
    assert.equal(res.status, 503);
    assert.deepEqual(body, { error: 'starting', reason: 'db_unreachable' });
    assert.equal(res.headers.get(G.HELD_HEADER), null, 'not held: an outage every instance shares');
    assert.ok(ms < 1000, `refused at once (${ms} ms)`);
  } finally { g.server.close(); }

  readiness._reset();
  const h = await gateServer({ holdMs: 3000 });
  try {
    setTimeout(() => readiness.markAttemptFailed(refusedErr()), 150);
    const { res, ms, body } = await timed(fetch(h.url));
    assert.equal(res.status, 503);
    assert.equal(body.reason, 'db_unreachable', 'the reason the attempt found, not "starting"');
    assert.ok(ms < 2000, `answered when the attempt failed, not at the end of the hold (${ms} ms)`);
  } finally { h.server.close(); }
});

test('a caller that hangs up while held runs no route', async () => {
  const g = await gateServer({ holdMs: 3000 });
  try {
    await new Promise((resolve) => {
      const req = http.request(g.url, { method: 'POST' });
      req.on('error', () => resolve());
      req.end('{}');
      setTimeout(() => { req.destroy(); setTimeout(resolve, 100); }, 100);
    });
    readiness.markReady();
    await tick(150);
    assert.deepEqual(g.ran, [], 'the route ran for a caller that had gone');
    // The other arm: a caller still there is served.
    const { res } = await timed(fetch(g.url, { method: 'POST', body: '{}' }));
    assert.equal(res.status, 200);
    assert.deepEqual(g.ran, ['POST']);
  } finally { g.server.close(); }
});

test('past maxHeld calls the gate refuses at once, as before', async () => {
  const g = await gateServer({ holdMs: 600, maxHeld: 1 });
  try {
    const first = timed(fetch(g.url));
    await tick(80);
    const second = await timed(fetch(g.url));
    assert.equal(second.res.status, 503);
    assert.equal(second.res.headers.get(G.HELD_HEADER), null, 'the second call was not held');
    assert.ok(second.ms < 400, `${second.ms} ms`);
    const held = await first;
    assert.equal(held.res.headers.get(G.HELD_HEADER), '1', 'the first one was');
    // And the slot is given back: a third call is held again.
    const third = await timed(fetch(g.url));
    assert.equal(third.res.headers.get(G.HELD_HEADER), '1');
  } finally { g.server.close(); }
});

test('the hold is read from STARTING_HOLD_MS, capped under every page\'s own time limit', () => {
  assert.equal(G.holdMsFrom({}), G.DEFAULT_HOLD_MS);
  assert.equal(G.holdMsFrom({ STARTING_HOLD_MS: '' }), G.DEFAULT_HOLD_MS);
  assert.equal(G.holdMsFrom({ STARTING_HOLD_MS: '0' }), 0, '0 turns it off');
  assert.equal(G.holdMsFrom({ STARTING_HOLD_MS: '2500' }), 2500);
  assert.equal(G.holdMsFrom({ STARTING_HOLD_MS: '90000' }), G.MAX_HOLD_MS);
  assert.equal(G.holdMsFrom({ STARTING_HOLD_MS: 'soon' }), G.DEFAULT_HOLD_MS);
  assert.equal(G.holdMsFrom({ STARTING_HOLD_MS: '-1' }), G.DEFAULT_HOLD_MS);
  // The cap is below the shortest time any page gives a call.
  const PUBLIC = path.join(__dirname, '..', 'public');
  const budgets = [];
  for (const f of ['app.js', 'dashboard.js']) {
    const src = codeOnly(fs.readFileSync(path.join(PUBLIC, 'js', f), 'utf8'));
    for (const m of src.matchAll(/timeoutMs(?: = |: ?)(\d{4,})/g)) budgets.push(Number(m[1]));
  }
  assert.ok(budgets.length > 50, `found ${budgets.length} page budgets`);
  assert.ok(G.MAX_HOLD_MS < Math.min(...budgets), `cap ${G.MAX_HOLD_MS} vs shortest ${Math.min(...budgets)}`);
  // And the server mounts the gate with the hold read from its environment.
  const server = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8'));
  assert.match(server, /app\.use\(notReadyGate\(\{ readiness, holdMs: holdMsFrom\(process\.env\) \}\)\)/);
});

test('whenSettled answers at once once the first attempt has finished, and never keeps the process up', async () => {
  readiness.markReady();
  assert.equal(await readiness.whenSettled(60000), true);
  readiness._reset();
  readiness.markAttemptFailed(refusedErr());
  assert.equal(await readiness.whenSettled(60000), false);
  readiness._reset();
  const t0 = Date.now();
  assert.equal(await readiness.whenSettled(120), false);
  assert.ok(Date.now() - t0 >= 100);
});

// ── the page ──────────────────────────────────────────────────────────────

const ORIGIN = 'https://www.humanoid-traders.com';
const STARTING = (headers = {}) => new Response(JSON.stringify({ error: 'starting', reason: 'starting' }), {
  status: 503, headers: { 'Content-Type': 'application/json', ...headers },
});

test('the page spends no retries on a refusal the instance already held', async () => {
  const run = async (answer) => {
    const calls = [];
    const f = R.wrap(async (input) => { calls.push(input); return answer(); }, { origin: ORIGIN, sleep: async () => {} });
    const res = await f('/api/token');
    return { calls: calls.length, res };
  };
  const held = await run(() => STARTING({ [G.HELD_HEADER]: '1' }));
  assert.equal(held.calls, 1, 'a held refusal is not retried');
  assert.equal(held.res.status, 503);
  assert.deepEqual(await held.res.json(), { error: 'starting', reason: 'starting' }, 'and reaches the page intact');
  const unheld = await run(() => STARTING());
  assert.equal(unheld.calls, R.RETRY_DELAYS_MS.length + 1, 'an unheld one still is');
});

test('a panel retries a fresh instance\'s refusal on its own schedule, and a failing database\'s never', () => {
  const starting = { status: 503, code: 'starting', reason: 'starting' };
  const waits = [];
  for (let n = 0; ; n += 1) {
    const w = M.autoRetryDelay(starting, n);
    if (w == null) break;
    waits.push(w);
    assert.ok(n < 20, 'the schedule ends');
  }
  assert.deepEqual(waits, M.STARTING_RETRY_MS);
  assert.ok(waits.length >= 3 && waits.every((w, i) => i === 0 || w > waits[i - 1]), 'backs off');
  const total = waits.reduce((a, b) => a + b, 0);
  assert.ok(total >= 20000 && total <= 60000, `about half a minute in all (${total} ms)`);
  for (const reason of ['db_unreachable', 'db_timeout', 'db_auth', '']) {
    assert.equal(M.autoRetryDelay({ status: 503, code: 'starting', reason }, 0), null, reason);
  }
  for (const code of ['rate_limited', 'not_allowlisted', '']) {
    assert.equal(M.autoRetryDelay({ status: 503, code, reason: 'starting' }, 0), null, code);
  }
  assert.equal(M.STARTING_AUTO.action, 'retry', 'the Retry button stays while it waits');
});

test('the reason a panel keys on is the gate\'s, read only off the gate\'s own answer', async () => {
  assert.equal(M.reasonOf({ error: 'starting', reason: 'starting' }), 'starting');
  assert.equal(M.reasonOf({ error: 'starting', reason: 'db_unreachable' }), 'db_unreachable');
  assert.equal(M.reasonOf({ error: 'rate_limited', reason: 'starting' }), '', 'another code\'s reason is not read');
  assert.equal(M.reasonOf({ error: 'starting', reason: 'The database at db.internal refused' }), '', 'prose is not a reason');
  assert.equal(M.reasonOf(null), '');
  // What the real gate sends is what the panel model reads.
  const g = await gateServer({ holdMs: 0 });
  try {
    const body = await (await fetch(g.url)).json();
    assert.equal(M.codeOf(body), 'starting');
    assert.equal(M.reasonOf(body), 'starting');
    assert.notEqual(M.autoRetryDelay({ code: M.codeOf(body), reason: M.reasonOf(body) }, 0), null);
  } finally { g.server.close(); }
  // Every reason the gate can send is one the model accepts as a reason.
  for (const reason of readiness.REASONS) {
    assert.equal(M.reasonOf({ error: 'starting', reason }), reason, reason);
  }
});

test('the new sentence is in all fourteen languages', () => {
  const i18n = require('../public/js/i18n');
  const e = i18n.STRINGS[M.STARTING_AUTO.key];
  assert.ok(e, `${M.STARTING_AUTO.key} is not in the dictionary`);
  for (const l of i18n.LANGS) assert.ok(String(e[l.code] || '').trim(), `missing ${l.code}`);
  assert.equal(e.en, M.STARTING_AUTO.fallback);
});
