'use strict';

/**
 * Boot resilience — the website must not need the database to be reachable in
 * order to exist.
 *
 * The outage this pins: server.js awaited migrate() BEFORE app.listen() and
 * called process.exit(1) on failure, so an unreachable database meant the port
 * was never bound. That is not a degraded site, it is a blackout — every path
 * hangs, static pages included, and a restarting platform crash-loops with no
 * healthy origin. Reproduced with DATABASE_URL pointed at an unroutable host:
 * "Migration failed: connect ECONNREFUSED", process exited, port never bound.
 *
 * The contract now:
 *   - liveness is unconditional and touches nothing;
 *   - readiness is earned by migration and re-earned by retry;
 *   - /readyz leaks no driver text, host, credential or stack (F-15);
 *   - DB-backed /api/* refuses with 503 while unready rather than rendering an
 *     error as if it were data ("unreadable is never zero");
 *   - static content is never gated on the database.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const readiness = require('../lib/readiness');

const SERVER_SRC = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');

test.beforeEach(() => readiness._reset());

// ── the state module ──────────────────────────────────────────────────────

test('starts not-ready with a reason, and becomes ready on success', () => {
  const before = readiness.snapshot();
  assert.equal(before.ready, false);
  assert.equal(before.reason, 'starting');
  assert.equal(before.ready_since, null);

  readiness.markReady();
  const after = readiness.snapshot();
  assert.equal(after.ready, true);
  assert.equal(after.reason, 'ready');
  assert.ok(after.ready_since, 'a ready moment is recorded');
});

test('classifies failures into the coarse vocabulary only', () => {
  assert.equal(readiness.classify({ code: 'ECONNREFUSED' }), 'db_unreachable');
  assert.equal(readiness.classify({ code: 'ETIMEDOUT' }), 'db_unreachable');
  assert.equal(readiness.classify({ code: 'ENOTFOUND' }), 'db_unreachable');
  assert.equal(readiness.classify({ code: 'ER_ACCESS_DENIED_ERROR' }), 'db_auth');
  // An unrecognised CODE collapses — it does not pass detail through.
  assert.equal(readiness.classify({ code: 'ER_SOMETHING_NEW' }), 'db_error');
  // A failure with NO code is a different signal: every mysql2 error that
  // reaches the network carries one, so its absence means the options never
  // parsed. Bucketing it with unknown codes sent an operator hunting an IP
  // allowlist for a malformed `ssl` parameter.
  assert.equal(readiness.classify(new Error('boom')), 'db_url');
  // An ABSENT error claims nothing — there is no failure to attribute.
  assert.equal(readiness.classify(null), 'db_error');
  assert.equal(readiness.classify(undefined), 'db_error');
});

test('the readiness body never carries driver text, host or credentials', () => {
  const err = new Error(
    "connect ECONNREFUSED 10.1.2.3:3306 for user 'root' password 'hunter2'");
  err.code = 'ECONNREFUSED';
  readiness.markAttemptFailed(err);

  const body = JSON.stringify(readiness.snapshot());
  for (const leak of ['10.1.2.3', '3306', 'root', 'hunter2', 'ECONNREFUSED', 'connect']) {
    assert.ok(!body.includes(leak), `/readyz must not leak ${leak}: ${body}`);
  }
  assert.equal(readiness.snapshot().reason, 'db_unreachable');
});

// ── which store is behind "ready" ─────────────────────────────────────────

test('/readyz names the store, so a deliberate in-memory mode cannot pass for the database', () => {
  readiness.markReady();
  const mem = readiness.readyzBody(readiness.snapshot(), 'memory');
  assert.equal(mem.ready, true);
  assert.equal(mem.backend, 'memory');
  assert.equal(mem.persistent, false, 'ready on memory persists nothing, and the body says so');
  const db = readiness.readyzBody(readiness.snapshot(), 'mysql');
  assert.equal(db.backend, 'mysql');
  assert.equal(db.persistent, true);
  // The snapshot fields survive untouched beside the new ones.
  for (const k of ['ready', 'reason', 'attempts', 'ready_since']) assert.ok(k in mem, k);
});

test('a backend outside the vocabulary is unknown, not guessed in either direction', () => {
  const odd = readiness.readyzBody(readiness.snapshot(), 'postgres-someday');
  assert.equal(odd.backend, 'unknown');
  assert.equal(odd.persistent, null);
  assert.deepEqual(readiness.BACKENDS, ['mysql', 'memory']);
});

test('the route serves readyzBody with the live backend, and still carries no internals', () => {
  const i = SERVER_SRC.indexOf("app.get('/readyz'");
  const body = SERVER_SRC.slice(i, i + 700);
  assert.match(body, /readiness\.readyzBody\(snap, backend\(\)\)/,
    '/readyz answers with the bare snapshot again — an in-memory deployment reads as the database');
  assert.match(SERVER_SRC, /const \{[^}]*\bbackend\b[^}]*\} = require\('\.\/db'\)/,
    'backend() is not imported from ./db');
  const err = new Error("connect ECONNREFUSED 10.1.2.3:3306 for user 'root'");
  err.code = 'ECONNREFUSED';
  readiness.markAttemptFailed(err);
  const text = JSON.stringify(readiness.readyzBody(readiness.snapshot(), 'memory'));
  for (const leak of ['10.1.2.3', '3306', 'root', 'ECONNREFUSED']) assert.ok(!text.includes(leak), leak);
});

test('classify reads only the error code, never the message', () => {
  // A message mentioning a known code must NOT be promoted to that code —
  // otherwise arbitrary driver prose could steer the public reason field. It
  // carries no code, so it classifies on that absence alone.
  assert.equal(readiness.classify({ message: 'ECONNREFUSED somewhere' }), 'db_url');
  assert.notEqual(readiness.classify({ message: 'ECONNREFUSED somewhere' }),
    'db_unreachable', 'prose must never be promoted into a code');
});

test('every emitted reason is in the declared vocabulary', () => {
  const seen = [readiness.snapshot().reason];
  for (const code of ['ECONNREFUSED', 'ER_ACCESS_DENIED_ERROR', 'WEIRD']) {
    readiness.markAttemptFailed({ code });
    seen.push(readiness.snapshot().reason);
  }
  readiness.markReady();
  seen.push(readiness.snapshot().reason);
  for (const r of seen) assert.ok(readiness.REASONS.includes(r), `undeclared reason: ${r}`);
});

test('attempts accumulate, and readiness once earned is not revoked', () => {
  readiness.markAttemptFailed({ code: 'ECONNREFUSED' });
  readiness.markAttemptFailed({ code: 'ECONNREFUSED' });
  assert.equal(readiness.snapshot().attempts, 2);
  assert.equal(readiness.isReady(), false);

  readiness.markReady();
  const at = readiness.snapshot().ready_since;
  // a later transient error is a request-time problem, not a boot-time one
  readiness.markAttemptFailed({ code: 'ETIMEDOUT' });
  assert.equal(readiness.isReady(), true);
  assert.equal(readiness.snapshot().reason, 'ready');
  assert.equal(readiness.snapshot().ready_since, at, 'the ready moment is stable');
});

// ── the wiring in server.js ───────────────────────────────────────────────

test('the port is bound BEFORE the database is touched', () => {
  const listenAt = SERVER_SRC.indexOf('app.listen(PORT');
  const migrateAt = SERVER_SRC.indexOf('await migrateWithRetry()');
  assert.ok(listenAt > 0 && migrateAt > 0, 'both the listen and the migration must exist');
  assert.ok(listenAt < migrateAt,
    'app.listen must come first — awaiting migrate() before listening is the '
    + 'blackout this test exists to prevent');
});

test('a failed migration retries instead of exiting the process', () => {
  const startup = SERVER_SRC.slice(SERVER_SRC.indexOf('async function migrateWithRetry'));
  assert.ok(!/process\.exit\(/.test(startup),
    'the startup path must not exit on a database failure — exiting before '
    + 'listening is what produced a crash loop with no healthy origin');
  assert.ok(/markAttemptFailed/.test(startup), 'each failed attempt is recorded');
  assert.ok(/markReady\(\)/.test(startup), 'success marks the process ready');
});

test('probes are mounted ahead of the parsers, the static handler and the routers', () => {
  const healthzAt = SERVER_SRC.indexOf("app.get('/healthz'");
  const readyzAt = SERVER_SRC.indexOf("app.get('/readyz'");
  const staticAt = SERVER_SRC.indexOf('express.static(');
  const firstRouter = SERVER_SRC.indexOf("app.use('/api/auth'");
  assert.ok(healthzAt > 0 && readyzAt > 0, 'both probes must exist');
  assert.ok(healthzAt < staticAt && readyzAt < staticAt,
    'a probe behind the stack it observes cannot report on it');
  assert.ok(healthzAt < firstRouter && readyzAt < firstRouter);
});

test('liveness depends on nothing that can hang', () => {
  const body = SERVER_SRC.slice(
    SERVER_SRC.indexOf("app.get('/healthz'"),
    SERVER_SRC.indexOf("app.get('/readyz'"));
  for (const f of ['pool', 'migrate', 'await ', 'fs.', 'fetch(']) {
    assert.ok(!body.includes(f),
      `liveness must not touch ${f} — a probe that can hang is worse than none`);
  }
});

test('DB-backed api routes are gated while unready, static content is not', () => {
  const gate = SERVER_SRC.slice(
    SERVER_SRC.indexOf('const DB_FREE_API'),
    SERVER_SRC.indexOf("app.use('/api/auth'"));
  assert.ok(/readiness\.isReady\(\)/.test(gate), 'the gate consults readiness');
  assert.ok(/req\.path\.startsWith\('\/api\/'\)/.test(gate),
    'only /api/ is gated — static pages must survive a database outage');
  assert.ok(/status\(503\)/.test(gate), 'an ungated answer would render an error as data');
  // the gate must sit AFTER the static handler, so assets still serve
  assert.ok(SERVER_SRC.indexOf('express.static(') < SERVER_SRC.indexOf('const DB_FREE_API'));
});

test('the not-ready refusal leaks nothing beyond the coarse reason', () => {
  const gate = SERVER_SRC.slice(
    SERVER_SRC.indexOf('const DB_FREE_API'),
    SERVER_SRC.indexOf("app.use('/api/auth'"));
  const json = gate.slice(gate.indexOf('res.status(503)'));
  assert.ok(/reason:\s*snap\.reason/.test(json), 'only the coarse reason is returned');
  // `error: 'starting'` is a fixed literal and fine; what must never appear is
  // a read THROUGH an error object or the connection string.
  assert.ok(!/err\.|\.message|\.stack|DATABASE_URL/.test(json),
    'no error detail may reach the refusal body');
});
