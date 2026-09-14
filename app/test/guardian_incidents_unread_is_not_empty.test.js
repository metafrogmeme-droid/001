'use strict';
/**
 * Two server seams the decision log stands on.
 *
 * 1. /api/guardian/incidents is THREE-state, like its sibling /flight: a
 *    flight_cache row nobody could read used to answer 200 with counts of
 *    zero and "the controls have had nothing to stop or recover" — an
 *    all-clear about the safety ledger manufactured from a failed read.
 *    This is the reading the Guardian view's incidents panel takes on its
 *    own (it has no flight sibling to throw first); the decision log
 *    sequences its two reads, so there the flight read fails first.
 * 2. The anonymous scrub says WHICH absence: `result.fill_priced` is a
 *    boolean about the record (was a P&L ever there?) set after the scrub
 *    drops `pnl_usd`, so "sign in to see it" is said only when signing in
 *    would show a number, and the unpriced close the engine deliberately
 *    books stays "not recorded" for every viewer.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const ROOT = path.join(__dirname, '..');
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
const { sanitizeRecord, DOLLAR_KEY } = require(path.join(ROOT, 'lib', 'flight'));

function appWith(router) {
  const app = express();
  app.use('/api/guardian', router);
  return app;
}
async function get(app, url) {
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  try {
    const { port } = server.address();
    return await new Promise((resolve, reject) => {
      http.get(`http://127.0.0.1:${port}${url}`, (res) => {
        let body = '';
        res.on('data', (c) => { body += c; });
        res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(body || '{}') }));
      }).on('error', reject);
    });
  } finally {
    await new Promise((r) => server.close(r));
  }
}

test('an unreadable flight cache answers /incidents with 503, not counts of zero', async () => {
  const { pool } = require(path.join(ROOT, 'db'));
  const sync = require(path.join(ROOT, 'routes', 'sync'));
  const guardian = require(path.join(ROOT, 'routes', 'guardian'));
  const real = pool.execute;
  pool.execute = async () => { throw new Error('pool exhausted'); };
  try {
    const r = await get(appWith(guardian), '/api/guardian/incidents');
    assert.equal(r.status, 503, `an unreadable incident ledger answered ${r.status}: ${JSON.stringify(r.body)}`);
    assert.ok(!/nothing to stop or recover/.test(JSON.stringify(r.body)), 'a database failure was published as an all-clear');
    assert.ok(!('counts' in r.body), 'no counts are invented for a record nobody read');
    assert.match(r.body.note, /not the same as/i);
    assert.equal(sync.getLatestFlight.lastReadFailed, true);
  } finally {
    pool.execute = real;
  }
});

test('a genuinely empty flight cache still answers /incidents with 200 and its own note', async () => {
  const { pool } = require(path.join(ROOT, 'db'));
  const guardian = require(path.join(ROOT, 'routes', 'guardian'));
  const real = pool.execute;
  pool.execute = async () => [[]];
  try {
    const r = await get(appWith(guardian), '/api/guardian/incidents');
    assert.equal(r.status, 200);
    assert.deepEqual(r.body.counts, { block: 0, recovery: 0, flag: 0 });
    assert.match(r.body.note, /nothing to stop or recover/);
  } finally {
    pool.execute = real;
  }
});

test('the anonymous scrub says whether a P&L was ever there, without carrying one', () => {
  const priced = sanitizeRecord({ decision_id: 'd', result: { pnl_usd: 12.5, exit_price: 60100, close_reason: 'tp' } });
  assert.equal(priced.result.fill_priced, true);
  assert.ok(!('pnl_usd' in priced.result), 'the amount itself is still dropped');
  const unpriced = sanitizeRecord({ decision_id: 'd', result: { pnl_usd: null, exit_price: 60100 } });
  assert.equal(unpriced.result.fill_priced, false, 'the unpriced close the engine deliberately books');
  const absent = sanitizeRecord({ decision_id: 'd', result: { exit_price: 60100 } });
  assert.equal(absent.result.fill_priced, false);
  const junk = sanitizeRecord({ decision_id: 'd', result: { pnl_usd: 'n/a' } });
  assert.equal(junk.result.fill_priced, false, 'a value that is not a number is not a price');
  assert.equal(sanitizeRecord({ decision_id: 'd', result: null }).result, null, 'no result, no marker');
  assert.equal(sanitizeRecord({ decision_id: 'd' }).result, undefined);
  // The marker is not a dollar key: the redaction sweep that strips every
  // DOLLAR_KEY field must not strip it, and it must not carry an amount.
  assert.ok(!DOLLAR_KEY.test('fill_priced'), 'the marker\'s name is not a currency key');
  for (const k of Object.keys(priced.result)) assert.ok(!DOLLAR_KEY.test(k), `${k} survived the scrub`);
});
