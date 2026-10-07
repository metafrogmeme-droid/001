'use strict';
/**
 * A heartbeat that arrives on a fresh website process must not replace the
 * saved scan.
 *
 * `POST /api/bot/sync/scan` with `{heartbeat: true}` is the bot's cadence
 * stamp: every 20 s for the length of every batch. PR 499 added its branch
 * ABOVE the restart guard (`if (!latestScan) await getLatestScan()`) whose
 * own comment says it exists so a push after a website restart does not
 * REPLACE the saved scan in the DB, cards and all. `latestScan` is null at
 * module load, so the first heartbeat on a fresh process took the `else`
 * arm, set `latestScan = {heartbeat_at}` and executed `REPLACE INTO
 * scan_cache` with that object: entry_cards, symbols, scan_at, deepscan and
 * the breaker block were gone until the next manual /scan, and the
 * autonomous cycle's own pushes carry none of them. Driven here against a
 * pool that persists the row across fresh requires of the router, the way a
 * deploy persists the DB across restarts.
 *
 * The guard now runs first. And a heartbeat that finds nothing loaded (no
 * saved scan, or a read that failed) keeps the beat in memory and writes
 * nothing: a REPLACE is the one statement that can turn a saved scan this
 * process could not read into a row holding one timestamp.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const APP = path.join(__dirname, '..');
const TOKEN = jwt.sign({ user_id: 1, email: 'op@test.dev' }, process.env.JWT_SECRET);
const SECRET = process.env.BOT_SYNC_SECRET;

// A pool that PERSISTS scan_cache row 1 across fresh requires of sync.js,
// the way the database persists across website restarts. `readFails` makes
// the load raise, the restart whose saved copy this process cannot read.
function makeDb() {
  const db = { scan_json: null, readFails: false, writes: 0 };
  db.pool = {
    execute: async (sql, params) => {
      if (/FROM users WHERE id/.test(sql)) return [[{ plan: 'admin' }]];
      if (/REPLACE INTO scan_cache/.test(sql)) { db.scan_json = params[0]; db.writes += 1; return [{}]; }
      if (/SELECT scan_json(, updated_at)? FROM scan_cache/.test(sql)) {
        if (db.readFails) throw new Error('scan_cache unreadable');
        return [db.scan_json ? [{ scan_json: db.scan_json, updated_at: new Date() }] : []];
      }
      return [[]];
    },
  };
  return db;
}

// == a website restart: the router's module state (latestScan) starts null.
function freshServer(db) {
  const dbPath = require.resolve(path.join(APP, 'db.js'));
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true, exports: { pool: db.pool } };
  delete require.cache[require.resolve(path.join(APP, 'routes', 'sync.js'))];
  const router = require(path.join(APP, 'routes', 'sync.js'));
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', router);
  return http.createServer(app);
}

function request(port, method, p, { body, token, secret } = {}) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : '';
    const r = http.request({
      port, method, path: p,
      headers: {
        ...(body ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(secret ? { 'X-Bot-Secret': secret } : {}),
      },
    }, (res) => {
      let b = '';
      res.on('data', (c) => { b += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: b ? JSON.parse(b) : {} }));
    });
    r.on('error', reject);
    r.end(data);
  });
}

async function withServer(db, fn) {
  const server = freshServer(db);
  await new Promise((res) => server.listen(0, '127.0.0.1', res));
  try { return await fn(server.address().port); } finally { server.close(); }
}

const post = (port, body) => request(port, 'POST', '/api/bot/sync/scan', { body, secret: SECRET });
const get = (port) => request(port, 'GET', '/api/bot/sync/scan', { token: TOKEN });

const A_REAL_SCAN = {
  circuit_breaker: { equity: 141.22, total_trades: 2, live_mode: true },
  regime: { label: 'NEUTRAL', gate: 63000 },
  symbols: { BTCUSDT: { score: 0.7, status: 'setup' } },
  entry_cards: [{ symbol: 'BTC', direction: 'LONG', entry: '62970', stop_loss: '62750',
                  tp1: '63300', rr: '1.5', trigger: 'RSI 55' }],
  entry_cards_read: { results: 40, above_floor: 3, considered: 3, cards: 1, no_atr: 2,
                      no_direction: 0, floor: 0.4, shown_max: 8 },
};
const stored = (db) => JSON.parse(db.scan_json);

test('a heartbeat that is the first push after a restart stamps the saved scan, cards and all', async () => {
  const db = makeDb();
  await withServer(db, async (port) => {
    assert.equal((await post(port, A_REAL_SCAN)).status, 200);
  });
  assert.equal(stored(db).entry_cards.length, 1);
  // Restart. No reader has hit GET /scan; the 20 s heartbeat arrives first.
  await withServer(db, async (port) => {
    const r = await post(port, { heartbeat: true });
    assert.equal(r.status, 200);
    assert.deepEqual(r.data, { ok: true, heartbeat: true });
    const row = stored(db);
    assert.equal(row.entry_cards.length, 1, 'the saved cards survived the heartbeat');
    assert.deepEqual(row.symbols, A_REAL_SCAN.symbols);
    assert.deepEqual(row.circuit_breaker, A_REAL_SCAN.circuit_breaker);
    assert.ok(row.scan_at, 'the scan keeps its own time');
    assert.ok(row.heartbeat_at, 'and gained the beat');
    const g = await get(port);
    assert.equal(g.status, 200);
    assert.equal(g.data.scan.entry_cards.length, 1);
    assert.ok(g.data.scan.heartbeat_at);
  });
});

test('a heartbeat on a process that loaded no saved scan is kept in memory and writes nothing', async () => {
  // No saved scan at all: nothing to replace, and nothing is written.
  const empty = makeDb();
  await withServer(empty, async (port) => {
    const r = await post(port, { heartbeat: true });
    assert.equal(r.status, 200);
    assert.equal(empty.writes, 0, 'no REPLACE for a bare beat');
    assert.equal(empty.scan_json, null);
    const g = await get(port);
    assert.ok(g.data.scan && g.data.scan.heartbeat_at, 'the status line still reads the beat');
  });
  // A saved scan this process could not READ: the read fails, the beat
  // stays in memory, and the row is not replaced with a timestamp.
  const unread = makeDb();
  await withServer(unread, async (port) => { await post(port, A_REAL_SCAN); });
  const before = unread.scan_json;
  unread.readFails = true;
  await withServer(unread, async (port) => {
    const r = await post(port, { heartbeat: true });
    assert.equal(r.status, 200);
    assert.equal(unread.scan_json, before, 'a scan the process could not read is not overwritten');
  });
  unread.readFails = false;
  await withServer(unread, async (port) => {
    const g = await get(port);
    assert.equal(g.data.scan.entry_cards.length, 1, 'the cards are still there for the next process');
  });
});

test('the other arm: a heartbeat on a loaded process still stamps and stores the beat', async () => {
  const db = makeDb();
  await withServer(db, async (port) => {
    await post(port, A_REAL_SCAN);
    const writes = db.writes;
    await post(port, { heartbeat: true });
    assert.equal(db.writes, writes + 1, 'the beat is persisted when there is a scan to stamp');
    assert.ok(stored(db).heartbeat_at);
    assert.equal(stored(db).entry_cards.length, 1);
  });
});

// ── a read that failed and then recovered ───────────────────────────────────
// One heartbeat during a failed read left `latestScan = {heartbeat_at}`, and
// the restart guard tested `latestScan`, so the saved scan was never loaded
// for the life of the process: the cards stayed off the dashboard after the
// database recovered, and the next cycle summary replaced the row without
// them. The guard asks whether the row was READ now, and retries until it is.

const A_SUMMARY = { circuit_breaker: { equity: 140.5, total_trades: 3, live_mode: true },
                    regime: { label: 'RISK_ON', gate: 64000 } };

test('a beat during a failed read does not hide the saved scan once the read recovers', async () => {
  const db = makeDb();
  await withServer(db, async (port) => { await post(port, A_REAL_SCAN); });
  db.readFails = true;
  await withServer(db, async (port) => {
    await post(port, { heartbeat: true });
    db.readFails = false;
    const g = await get(port);
    assert.equal(g.data.scan.entry_cards.length, 1, 'the saved cards are read once the row reads');
    assert.ok(g.data.scan.heartbeat_at, 'and the beat kept in memory is laid over them');
    await post(port, A_SUMMARY);
    const row = stored(db);
    assert.equal(row.entry_cards.length, 1, 'the cycle summary carried the saved cards forward');
    assert.deepEqual(row.circuit_breaker, A_SUMMARY.circuit_breaker);
  });
});

test('a cycle summary that arrives while the saved row is unread is kept in memory, not written', async () => {
  const db = makeDb();
  await withServer(db, async (port) => { await post(port, A_REAL_SCAN); });
  const saved = db.scan_json;
  db.readFails = true;
  await withServer(db, async (port) => {
    await post(port, A_SUMMARY);
    assert.equal(db.scan_json, saved, 'no REPLACE of a row this process could not read');
    // Nor does a heartbeat write the in-memory summary over it.
    await post(port, { heartbeat: true });
    assert.equal(db.scan_json, saved);
    // The read recovers: the saved cards under the newer summary and beat.
    db.readFails = false;
    const g = await get(port);
    assert.equal(g.data.scan.entry_cards.length, 1);
    assert.deepEqual(g.data.scan.circuit_breaker, A_SUMMARY.circuit_breaker);
    assert.ok(g.data.scan.heartbeat_at);
  });
});

test('a real scan pushed while the saved row is unread is the newer scan and is written', async () => {
  const db = makeDb();
  await withServer(db, async (port) => { await post(port, A_REAL_SCAN); });
  db.readFails = true;
  const fresh = { ...A_REAL_SCAN, entry_cards: [{ ...A_REAL_SCAN.entry_cards[0], symbol: 'ETH' }] };
  await withServer(db, async (port) => {
    await post(port, fresh);
    assert.equal(stored(db).entry_cards[0].symbol, 'ETH');
    // It is the saved scan now; a summary after it carries its cards.
    await post(port, A_SUMMARY);
    assert.equal(stored(db).entry_cards[0].symbol, 'ETH');
    assert.deepEqual(stored(db).circuit_breaker, A_SUMMARY.circuit_breaker, 'and the summary is written');
  });
});

test('GET /scan says a saved scan it could not read is unreadable, and an empty one is empty', async () => {
  const unread = makeDb();
  await withServer(unread, async (port) => { await post(port, A_REAL_SCAN); });
  unread.readFails = true;
  await withServer(unread, async (port) => {
    const g = await get(port);
    assert.equal(g.status, 503);
    assert.equal(g.data.scan, undefined);
  });
  const empty = makeDb();
  await withServer(empty, async (port) => {
    const g = await get(port);
    assert.equal(g.status, 200);
    assert.equal(g.data.scan, null);
    assert.match(g.data.message, /No scan data yet/);
  });
});
