/**
 * One venue's connection status never rewrites another venue's.
 *
 * Reported 8 October: the Bitget card went from "connected" to "not
 * connected" when the bot refused a Bybit key. Production's
 * `exchange_status` still had `PRIMARY KEY (user_id)`: the July migration
 * re-keyed it with `ALTER TABLE ... DROP PRIMARY KEY, ADD PRIMARY KEY
 * (user_id, exchange)` inside a catch that swallowed the failure, and TiDB
 * cannot drop a clustered integer primary key. The schema fast path then
 * skipped the block for good. So every ack's `ON DUPLICATE KEY UPDATE
 * exchange = VALUES(exchange)` landed on the user's ONE row, and at 09:04 UTC
 * the Bybit refusal rewrote user 1's Bitget row as Bybit's.
 *
 * `exchange_venue_status` is created keyed (user_id, exchange) from birth (no
 * ALTER, no DROP, no RENAME), seeded once from the legacy table, and every
 * reader and writer moved to it.
 *
 * Driven: `ensureVenueStatusTable` against a recording pool (there is no
 * MySQL or TiDB in this harness, which is said here rather than implied), and
 * the real ack route on the in-memory store. Scanned, comments stripped: the
 * shapes a test cannot reach (which table each route names, what the upsert
 * updates, where the migration calls the helper).
 */
'use strict';

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const db = require('../db');
const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');
const read = (rel) => codeOnly(fs.readFileSync(path.join(APP, rel), 'utf8'));

/** A pool that records what it is asked, and answers COUNT(*) with `n`. */
function recordingPool({ n = 0, seedError = null } = {}) {
  const calls = [];
  const pool = {
    calls,
    async query(sql) {
      calls.push({ via: 'query', sql: String(sql).replace(/\s+/g, ' ').trim() });
      if (/SELECT COUNT\(\*\) AS n FROM exchange_venue_status/i.test(sql)) return [[{ n }], []];
      if (/INSERT IGNORE INTO exchange_venue_status/i.test(sql) && seedError) throw seedError;
      return [[], []];
    },
    async execute(sql) {
      calls.push({ via: 'execute', sql: String(sql).replace(/\s+/g, ' ').trim() });
      return [[], []];
    },
  };
  return pool;
}

test('the per-venue table is created keyed on (user, venue), through the text protocol', async () => {
  const pool = recordingPool({ n: 0 });
  await db.ensureVenueStatusTable(pool);
  const create = pool.calls.find((c) => /CREATE TABLE IF NOT EXISTS exchange_venue_status/.test(c.sql));
  assert.ok(create, 'no CREATE for exchange_venue_status');
  assert.match(create.sql, /PRIMARY KEY \(user_id, exchange\)/);
  assert.match(create.sql, /exchange VARCHAR\(16\) NOT NULL/);
  // DDL through query(): TiDB answers prepared DDL with a 1064.
  assert.ok(pool.calls.every((c) => c.via === 'query'), JSON.stringify(pool.calls));
  // Nothing that TiDB refuses, and nothing that touches the legacy table's key.
  assert.ok(pool.calls.every((c) => !/DROP PRIMARY KEY|RENAME TABLE|ALTER TABLE/i.test(c.sql)));
});

test('it is seeded once, from the legacy rows, and never over a row already there', async () => {
  const empty = recordingPool({ n: 0 });
  await db.ensureVenueStatusTable(empty);
  const seed = empty.calls.find((c) => /INSERT IGNORE INTO exchange_venue_status/.test(c.sql));
  assert.ok(seed, 'an empty table was not seeded');
  assert.match(seed.sql, /SELECT user_id, COALESCE\(NULLIF\(exchange, ''\), 'bitget'\), connected, last_error, updated_at FROM exchange_status/);
  // The other arm: a table that already holds rows is left alone.
  const full = recordingPool({ n: 3 });
  await db.ensureVenueStatusTable(full);
  assert.ok(!full.calls.some((c) => /INSERT/i.test(c.sql)), 'a populated table was re-seeded');
});

test('a failed seed is logged with its code and does not stop the website booting', async () => {
  const err = Object.assign(new Error('table exchange_status does not exist'), { code: 'ER_NO_SUCH_TABLE' });
  const pool = recordingPool({ n: 0, seedError: err });
  const logged = [];
  const orig = console.error;
  console.error = (...a) => logged.push(a.join(' '));
  try {
    await db.ensureVenueStatusTable(pool);
  } finally {
    console.error = orig;
  }
  assert.ok(logged.some((l) => l.includes('exchange_venue_status seed failed') && l.includes('ER_NO_SUCH_TABLE')),
    JSON.stringify(logged));
});

test('the migration creates it, and the fast path notices it is missing', () => {
  assert.ok(db.EXPECTED_TABLES.includes('exchange_venue_status'));
  const src = read('db.js');
  const m = src.indexOf('async function migrate()');
  const legacy = src.indexOf('CREATE TABLE IF NOT EXISTS exchange_status', m);
  const call = src.indexOf('await ensureVenueStatusTable(pool);', m);
  assert.ok(m > 0 && legacy > m && call > legacy,
    'migrate() must create the per-venue table after the legacy one it seeds from');
  assert.ok(!/DROP PRIMARY KEY/.test(src), 'the ALTER TiDB cannot run is still in the migration');
});

test('every reader and writer names the per-venue table, and no upsert moves a row to another venue', () => {
  const files = ['routes/credentials.js', 'routes/controls.js', 'routes/sync.js',
    'lib/networth.js', 'lib/holdings.js'];
  for (const f of files) {
    const src = read(f);
    assert.ok(!/\bexchange_status\b/.test(src), `${f} still reads or writes the legacy table`);
    assert.ok(/\bexchange_venue_status\b/.test(src), `${f} names no status table at all`);
  }
  const sync = read('routes/sync.js');
  assert.ok(!/exchange\s*=\s*VALUES\(exchange\)/.test(sync),
    'an ack still rewrites the venue of the row it lands on');
  // The erasure list removes both: the rows a user had, under either name.
  const erasure = fs.readFileSync(path.join(APP, 'lib', 'account_erasure.js'), 'utf8');
  assert.match(erasure, /'exchange_status'/);
  assert.match(erasure, /'exchange_venue_status'/);
});

// ── driven: the real ack route, a refusal for one venue beside another ──────

let server, base;

function request(method, p, { secret, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(secret ? { 'x-bot-secret': secret } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

test('a refused Bybit key leaves the connected Bitget row as it was', async (t) => {
  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  t.after(() => server.close());
  base = `http://127.0.0.1:${server.address().port}`;
  const uid = 4242;
  await db.pool.execute(
    `INSERT INTO exchange_venue_status (user_id, exchange, connected, last_error)
     VALUES (?, ?, ?, NULL) ON DUPLICATE KEY UPDATE connected = VALUES(connected)`, [uid, 'bitget', true]);
  await db.pool.execute(
    `INSERT INTO pending_credentials (user_id, telegram_id, exchange, action, encrypted_payload)
     VALUES (?, ?, ?, 'connect', ?)`, [uid, '9003', 'bybit', '{}']);
  const ack = await request('POST', '/api/bot/sync/credentials/ack', {
    secret: process.env.BOT_SYNC_SECRET,
    body: { acks: [{ user_id: uid, action: 'connect', ok: false, error: 'Bybit mainnet does not know this API key (code 10003).' }] },
  });
  assert.equal(ack.status, 200);
  const [rows] = await db.pool.execute(
    'SELECT connected, exchange, last_error FROM exchange_venue_status WHERE user_id = ?', [uid]);
  const by = Object.fromEntries(rows.map((r) => [r.exchange, r]));
  assert.equal(by.bitget && by.bitget.connected, true, 'the Bitget row was rewritten');
  assert.equal(by.bitget.last_error, null);
  assert.equal(by.bybit && by.bybit.connected, false);
  assert.match(by.bybit.last_error, /code 10003/);
});
