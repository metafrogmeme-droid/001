'use strict';
/**
 * The public status page reported the database healthy without reading it.
 *
 *     components.database = { state: 'ok', mode: p.dbMode() };
 *
 * a literal, beside an honesty note reading "nothing here is hand-set". Driven
 * on 2026-09-26 with every query refused (ECONNREFUSED): GET /api/public/status
 * answered `status: ok`, `database: ok` while a sign-in answered 500. The
 * database is the one component every account-facing route depends on, and
 * the page that exists to report trouble reported none from no reading at all.
 *
 * The database line is a READ now: a trivial query under a short deadline,
 * `ok` only when it came back, `unreachable` when it threw or hung, and an
 * unreachable database counts toward the overall status like any other
 * component. Driven through the real default probe and the real route.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');
const { pool } = require('../db');
const status = require('../lib/status');

const realExecute = pool.execute.bind(pool);
function refuseEveryQuery() {
  pool.execute = async () => {
    const e = new Error('connect ECONNREFUSED 10.0.0.7:3306');
    e.code = 'ECONNREFUSED';
    throw e;
  };
}
function restore() { pool.execute = realExecute; }

test.afterEach(() => { restore(); status.setProbes(null); });

test('the real probe reads ok from a database that answers', async () => {
  assert.deepEqual(await status.defaultProbes().pingDatabase(), { state: 'ok' });
});

test('the real probe reads unreachable from a database that refuses', async () => {
  refuseEveryQuery();
  assert.deepEqual(await status.defaultProbes().pingDatabase(), { state: 'unreachable' });
});

test('the real probe reads unreachable from a database that never answers', async () => {
  pool.execute = () => new Promise(() => {});      // a hung connection
  const t0 = Date.now();
  const r = await status.defaultProbes().pingDatabase();
  assert.deepEqual(r, { state: 'unreachable' });
  assert.ok(Date.now() - t0 < 6000, 'the probe must have a deadline, or it hangs the page');
});

test('the probe asks the database, not a flag', async () => {
  const seen = [];
  pool.execute = async (sql, params) => { seen.push(sql); return realExecute(sql, params); };
  await status.defaultProbes().pingDatabase();
  assert.deepEqual(seen, ['SELECT 1']);
});

/** The public route over the REAL default probes, with the bot links off. */
async function publicStatus() {
  const app = express();
  app.use('/api/public/status', require('../routes/public_status'));
  const server = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  try {
    return await new Promise((resolve, reject) => {
      http.get(`http://127.0.0.1:${server.address().port}/api/public/status`, (res) => {
        let d = '';
        res.on('data', (c) => { d += c; });
        res.on('end', () => resolve({ status: res.statusCode, data: JSON.parse(d) }));
      }).on('error', reject);
    });
  } finally { server.close(); }
}

test('an unreachable database is on the page and the page is not ok', async () => {
  delete process.env.WEB_GATEWAY_SECRET;
  delete process.env.BOT_API_URL;
  // Everything the page reads OUTSIDE the database is healthy, so the database
  // is the only thing that can move the verdict.
  const probes = status.defaultProbes();
  const now = new Date().toISOString();
  status.setProbes({ ...probes,
    getScan: async () => ({ received_at: now }),
    getReports: async () => ({ received_at: now }) });

  const healthy = await publicStatus();
  assert.equal(healthy.data.components.database.state, 'ok');
  assert.equal(healthy.data.status, 'ok');

  refuseEveryQuery();
  const out = await publicStatus();
  assert.equal(out.status, 200);
  assert.equal(out.data.components.database.state, 'unreachable',
    'the database was reported ok while every query was refused');
  assert.notEqual(out.data.status, 'ok', 'an unreachable database is a worrying component');
  assert.match(out.data.honesty_note, /nothing here is hand-set/);
});

test('a probe that throws is unreachable, never ok', async () => {
  status.setProbes({ ...status.defaultProbes(),
    pingDatabase: async () => { throw new Error('boom'); } });
  const s = await status.buildStatus();
  assert.equal(s.components.database.state, 'unreachable');
});
