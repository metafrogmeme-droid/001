'use strict';
/**
 * Request limits hold across web instances.
 *
 * THE FINDING (2026-10-11). The web host replaces its instance about every 70
 * seconds, with overlap. Every per-route limit (lib/rate_limit.js, and five
 * hand-rolled copies in routes/{insight,lab,macro,market,patterns}.js) counted
 * in a Map in one process: two instances answering at once allowed twice the
 * stated number, and a rotation started the count again.
 *
 * The count is now shared through `rate_hits`; each instance's own window still
 * answers first and is the whole limit while the database does not answer.
 * "Another instance" is lib/rate_limit.js (and a router built on it) loaded
 * again over the one database the instances share.
 */

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const db = require('../db');

const LIB = '../lib/rate_limit';

/** lib/rate_limit.js as a new instance has it: no local counts, breaker closed. */
function instance() {
  delete require.cache[require.resolve(LIB)];
  return require(LIB);
}

/** Drive one request through a limiter: resolves with the status, and whether next() ran before the middleware returned. */
function call(mw, { method = 'POST', ip = '203.0.113.9' } = {}) {
  return new Promise((resolve) => {
    let returned = false;
    const res = {
      headers: {},
      setHeader(k, v) { this.headers[k] = v; },
      status(c) { this.code = c; return this; },
      json(body) { resolve({ status: this.code, body, headers: this.headers, held: returned }); return this; },
    };
    mw({ method, ip }, res, () => resolve({ status: 200, held: returned }));
    returned = true;
  });
}

const settle = () => new Promise((r) => setTimeout(r, 15));
const rows = () => Object.entries(db.pool.rateHits);

/**
 * Hold the clock mid-window, for a test whose count must fall inside one
 * minute. The sliding estimate rounds the remaining allowance up (1.25 → 2
 * slots, below), so a burst on the wall clock that straddles a minute admits
 * one more than `max`: insight's 60 passed 61 of 120 on CI on 2026-10-13.
 */
function inOneWindow(t) {
  t.mock.timers.enable({ apis: ['Date'], now: 29_000_400 * 60_000 + 30_000 });
}

const realExecute = db.pool.execute;
test.beforeEach(() => {
  db.pool.execute = realExecute;
  for (const k of Object.keys(db.pool.rateHits)) delete db.pool.rateHits[k];
});
test.afterEach(() => { db.pool.execute = realExecute; });

// ── the shared count ─────────────────────────────────────────────────────

test('five a minute is five across two instances, not five each', async (t) => {
  inOneWindow(t);
  const a = instance().rateLimit({ name: 't.pair', max: 5, message: 'slow' });
  const b = instance().rateLimit({ name: 't.pair', max: 5, message: 'slow' });
  const out = [];
  for (let i = 0; i < 10; i++) out.push(await call(i % 2 ? b : a));
  assert.strictEqual(out.filter((r) => r.status === 200).length, 5, JSON.stringify(out.map((r) => r.status)));
  const refused = out.filter((r) => r.status === 429);
  assert.strictEqual(refused.length, 5);
  assert.deepStrictEqual(refused[0].body, { error: 'slow' });
  assert.strictEqual(refused[0].headers['Retry-After'], 60);
});

test('a new instance starts from the count the last one left', async (t) => {
  inOneWindow(t);
  const old = instance().rateLimit({ name: 't.rotate', max: 5 });
  for (let i = 0; i < 5; i++) assert.strictEqual((await call(old)).status, 200);
  const next = instance().rateLimit({ name: 't.rotate', max: 5 });
  assert.strictEqual((await call(next)).status, 429, 'the rotation handed out a fresh budget');
  // a different caller is untouched
  assert.strictEqual((await call(next, { ip: '198.51.100.4' })).status, 200);
});

test('a concurrent burst that waits is admitted exactly max times across instances', async (t) => {
  inOneWindow(t);
  const a = instance().rateLimit({ name: 't.burst', max: 5 });
  const b = instance().rateLimit({ name: 't.burst', max: 5 });
  const out = await Promise.all(Array.from({ length: 12 }, (_, i) => call(i % 2 ? b : a)));
  assert.strictEqual(out.filter((r) => r.status === 200).length, 5, JSON.stringify(out.map((r) => r.status)));
  assert.strictEqual(rows().length, 1);
  assert.strictEqual(rows()[0][1].hits, 5);
});

test('once a window\'s row exists, a counted request costs this instance one statement', async (t) => {
  inOneWindow(t);
  const lim = instance().rateLimit({ name: 't.cost', max: 5 });
  assert.strictEqual((await call(lim)).status, 200);          // reads the previous window, opens this one
  await settle();
  let asked = 0;
  db.pool.execute = (...x) => { asked += 1; return realExecute.apply(db.pool, x); };
  for (let i = 0; i < 3; i++) assert.strictEqual((await call(lim)).status, 200);
  await settle();
  assert.strictEqual(asked, 3, `${asked} statements for 3 requests`);
});

test('a burst the shared count refused does not use up this instance\'s own window', async (t) => {
  const W = 60_000;
  const start = 29_000_300 * W;
  t.mock.timers.enable({ apis: ['Date'], now: start + 1_000 });
  const a = instance().rateLimit({ name: 't.unspent', max: 5 });
  for (let i = 0; i < 5; i++) assert.strictEqual((await call(a)).status, 200);
  t.mock.timers.setTime(start + 59_000);
  const b = instance().rateLimit({ name: 't.unspent', max: 5 });
  const burst = await Promise.all(Array.from({ length: 5 }, () => call(b)));
  assert.ok(burst.every((r) => r.status === 429), JSON.stringify(burst.map((r) => r.status)));
  // 50 s into the next window the shared count has room (5 - 5 x 1/6 → 5 slots),
  // and b's own window must not still be holding the five it refused.
  t.mock.timers.setTime(start + W + 50_000);
  assert.strictEqual((await call(b)).status, 200);
});

test('a limiter without a name is refused at construction, and so is a malformed one', () => {
  const rl = instance();
  assert.throws(() => rl.rateLimit({ max: 5 }), /needs a name/);
  assert.throws(() => rl.slidingWindow({ name: 'Has Spaces' }), /needs a name/);
  assert.doesNotThrow(() => rl.slidingWindow({ name: 'ok.name_1' }));
});

test('the stored key is the limiter name, the window and a hash, never the address itself', async () => {
  const lim = instance().rateLimit({ name: 't.privacy', max: 5 });
  await call(lim, { ip: '203.0.113.77' });
  const keys = rows().map(([k]) => k);
  assert.strictEqual(keys.length, 1);
  assert.ok(!keys[0].includes('203.0.113.77'), keys[0]);
  assert.match(keys[0], /^t\.privacy:\d+:[0-9a-f]{40}$/);
});

// ── reads are counted beside the request ─────────────────────────────────

test('a read is not held for the database; a write is', async () => {
  const lim = instance().rateLimit({ name: 't.hold', max: 5 });
  db.pool.execute = (...a) => new Promise((r) => setTimeout(r, 30)).then(() => realExecute.apply(db.pool, a));
  const read = await call(lim, { method: 'GET' });
  assert.deepStrictEqual([read.status, read.held], [200, false], 'the GET waited for the database');
  const write = await call(lim, { method: 'POST' });
  assert.deepStrictEqual([write.status, write.held], [200, true], 'the POST did not wait for the shared count');
});

test('a read past the shared limit is refused here once the count has said so, without asking again', async (t) => {
  inOneWindow(t);
  const a = instance().rateLimit({ name: 't.reads', max: 5 });
  const b = instance().rateLimit({ name: 't.reads', max: 5 });
  for (let i = 0; i < 5; i++) assert.strictEqual((await call(a)).status, 200);
  // b has not asked yet: its first read goes through and is counted beside it…
  assert.strictEqual((await call(b, { method: 'GET' })).status, 200);
  await settle();
  // …and the refusal it got back is remembered.
  let asked = 0;
  db.pool.execute = (...x) => { asked += 1; return realExecute.apply(db.pool, x); };
  const again = await call(b, { method: 'GET' });
  assert.strictEqual(again.status, 429);
  assert.strictEqual(asked, 0, 'a remembered refusal asked the database again');
  assert.strictEqual(rows()[0][1].hits, 5, 'the shared count went past the limit');
});

test('this instance\'s own window still refuses first, without a database round trip', async () => {
  const lim = instance().rateLimit({ name: 't.local', max: 3 });
  for (let i = 0; i < 3; i++) assert.strictEqual((await call(lim)).status, 200);
  let asked = 0;
  db.pool.execute = (...x) => { asked += 1; return realExecute.apply(db.pool, x); };
  const r = await call(lim);
  assert.deepStrictEqual([r.status, r.held, asked], [429, false, 0]);
});

// ── the sliding estimate ─────────────────────────────────────────────────

test('the previous window still counts, weighted by how much of it overlaps the last minute', async (t) => {
  const W = 60_000;
  const start = 29_000_000 * W;              // a window boundary
  t.mock.timers.enable({ apis: ['Date'], now: start - 30_000 });
  const old = instance().rateLimit({ name: 't.slide', max: 5 });
  for (let i = 0; i < 5; i++) assert.strictEqual((await call(old)).status, 200);

  // a quarter into the next window, on a new instance (its own window is empty):
  // 5 - 5 x 0.75 = 1.25 → 2 slots, where a plain per-window count would give 5
  t.mock.timers.setTime(start + W / 4);
  const next = instance().rateLimit({ name: 't.slide', max: 5 });
  const out = [];
  for (let i = 0; i < 4; i++) out.push((await call(next)).status);
  assert.deepStrictEqual(out, [200, 200, 429, 429]);

  // nine tenths in: 5 - 5 x 0.1 = 4.5 → 5 slots, 2 already taken
  t.mock.timers.setTime(start + (9 * W) / 10);
  const later = instance().rateLimit({ name: 't.slide', max: 5 });
  const more = [];
  for (let i = 0; i < 4; i++) more.push((await call(later)).status);
  assert.deepStrictEqual(more, [200, 200, 200, 429]);
});

test('a full previous window at the boundary leaves no slot and writes nothing', async (t) => {
  const W = 60_000;
  const start = 29_000_100 * W;
  t.mock.timers.enable({ apis: ['Date'], now: start - 1_000 });
  const old = instance().rateLimit({ name: 't.edge', max: 4 });
  for (let i = 0; i < 4; i++) await call(old);
  t.mock.timers.setTime(start);
  const next = instance().rateLimit({ name: 't.edge', max: 4 });
  assert.strictEqual((await call(next)).status, 429);
  assert.strictEqual(rows().length, 1, 'a row was opened for a window with no allowance');
});

// ── when the database does not answer ────────────────────────────────────

test('a database that refuses: each instance limits alone, nothing is refused for it, and it stops asking for a while', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: 1_800_000_000_000 });
  const rl = instance();
  const lim = rl.rateLimit({ name: 't.down', max: 3 });
  let asked = 0;
  db.pool.execute = () => {
    asked += 1;
    const e = new Error('connect ECONNREFUSED db.internal.example:4000');
    e.code = 'ECONNREFUSED';
    return Promise.reject(e);
  };
  const warned = [];
  t.mock.method(console, 'warn', (m) => warned.push(String(m)));

  const out = [];
  for (let i = 0; i < 4; i++) out.push((await call(lim)).status);
  assert.deepStrictEqual(out, [200, 200, 200, 429], 'the old per-instance limit, no more and no less');
  assert.strictEqual(asked, 1, 'the breaker did not open after the first failure');
  assert.strictEqual(warned.length, 1);
  assert.match(warned[0], /ECONNREFUSED/);
  assert.ok(!warned[0].includes('db.internal'), 'the driver message reached the log');

  t.mock.timers.setTime(1_800_000_000_000 + rl.BREAKER_MS + 1);
  db.pool.execute = realExecute;
  await call(lim, { ip: '198.51.100.20' });
  assert.ok(rows().length >= 1, 'the shared count was not tried again after the breaker');
});

test('once the breaker closes, the same caller in the same window is counted again', async (t) => {
  const T = 1_800_000_000_000;
  t.mock.timers.enable({ apis: ['Date'], now: T });
  const rl = instance();
  const lim = rl.rateLimit({ name: 't.retry', windowMs: 600_000, max: 10 });
  let asked = 0;
  db.pool.execute = () => { asked += 1; const e = new Error('x'); e.code = 'ECONNRESET'; return Promise.reject(e); };
  t.mock.method(console, 'warn', () => {});
  assert.strictEqual((await call(lim)).status, 200);
  assert.strictEqual(asked, 1);
  t.mock.timers.setTime(T + rl.BREAKER_MS + 1);         // same 10-minute window, breaker closed
  db.pool.execute = (...x) => { asked += 1; return realExecute.apply(db.pool, x); };
  assert.strictEqual((await call(lim)).status, 200);
  assert.ok(asked > 1, 'the failed read of the previous window was kept and never retried');
  assert.strictEqual(rows().length, 1);
});

test('a slow database holds a write no longer than the budget, then not at all', { timeout: 5_000 }, async () => {
  const rl = instance();
  const lim = rl.rateLimit({ name: 't.slow', max: 5 });
  db.pool.execute = () => new Promise(() => { /* never answers */ });
  let t0 = Date.now();
  assert.strictEqual((await call(lim)).status, 200, 'a slow database refused a request');
  const first = Date.now() - t0;
  assert.ok(first >= rl.SHARED_BUDGET_MS - 20 && first < rl.SHARED_BUDGET_MS + 250, `held ${first} ms`);
  t0 = Date.now();
  assert.strictEqual((await call(lim)).status, 200);
  assert.ok(Date.now() - t0 < 50, `held ${Date.now() - t0} ms after the breaker opened`);
});

test('no more than MAX_INFLIGHT shared counts are in flight on one instance', async () => {
  const rl = instance();
  const lim = rl.rateLimit({ name: 't.inflight', max: 50 });
  let asked = 0;
  db.pool.execute = () => { asked += 1; return new Promise(() => {}); };
  const out = [];
  for (let i = 0; i < rl.MAX_INFLIGHT + 5; i++) out.push(await call(lim, { method: 'GET', ip: `10.0.0.${i}` }));
  assert.ok(out.every((r) => r.status === 200));
  assert.strictEqual(asked, rl.MAX_INFLIGHT);
  await new Promise((r) => setTimeout(r, rl.SHARED_BUDGET_MS + 50)); // let them time out
});

test('rows are pruned once their window can no longer be read', async (t) => {
  const W = 60_000;
  const start = 29_000_200 * W;
  t.mock.timers.enable({ apis: ['Date'], now: start + 1_000 });
  await call(instance().rateLimit({ name: 't.prune', max: 5 }));
  assert.strictEqual(rows().length, 1);
  t.mock.timers.setTime(start + W + 1_000);           // still the previous window of this one
  await call(instance().rateLimit({ name: 't.prune', max: 5 }));
  await settle();
  assert.strictEqual(rows().length, 2, 'a row was pruned while it was still the previous window');
  t.mock.timers.setTime(start + 3 * W + 1_000);
  await call(instance().rateLimit({ name: 't.prune', max: 5 }));
  await settle();
  const wins = rows().map(([k]) => Number(k.split(':')[1])).sort();
  assert.deepStrictEqual(wins, [Math.floor((start + 3 * W) / W)]);
});

// ── the MCP window and the converted routers ─────────────────────────────

test('the MCP chat window is shared: six a minute per caller across instances', async (t) => {
  inOneWindow(t);
  const a = instance().slidingWindow({ name: 't.ask', windowMs: 60_000, max: 6 });
  const b = instance().slidingWindow({ name: 't.ask', windowMs: 60_000, max: 6 });
  const got = [];
  for (let i = 0; i < 12; i++) got.push(await (i % 2 ? b : a).allow('10.9.9.9'));
  assert.strictEqual(got.filter(Boolean).length, 6, JSON.stringify(got));
});

async function serve(router) {
  const app = express();
  app.set('trust proxy', false);
  app.use(router);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return server;
}

for (const [file, max] of [['market', 120], ['macro', 40], ['patterns', 60], ['insight', 60]]) {
  test(`routes/${file}.js: its ${max}-a-minute limit is one limit across two instances`, async (t) => {
    inOneWindow(t);
    const routerOn = () => {
      delete require.cache[require.resolve(LIB)];
      delete require.cache[require.resolve(`../routes/${file}`)];
      return require(`../routes/${file}`);
    };
    const servers = [await serve(routerOn()), await serve(routerOn())];
    try {
      const codes = [];
      for (let i = 0; i < 2 * max; i++) {
        const { port } = servers[i % 2].address();
        // POST to a path no route answers: the limiter runs, then 404.
        const r = await fetch(`http://127.0.0.1:${port}/__no_such_route`, { method: 'POST' });
        codes.push(r.status);
      }
      const passed = codes.filter((c) => c !== 429).length;
      assert.strictEqual(passed, max, `${passed} of ${2 * max} passed; per-instance limits would pass ${2 * max}`);
    } finally {
      for (const s of servers) s.close();
    }
  });
}
