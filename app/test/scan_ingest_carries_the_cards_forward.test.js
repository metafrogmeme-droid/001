'use strict';
/**
 * A cycle summary must not wipe the last scan's setups.
 *
 * `POST /api/bot/sync/scan` replaces the stored scan WHOLESALE, and the bot's
 * autonomous cycle pushes a SUMMARY through that route every cycle — the
 * circuit-breaker block and the regime, with no scan behind it. It used to
 * carry `entry_cards: []` and `symbols: {}`, so the last manual `/scan`'s cards
 * were gone within a cycle and the panel printed "No qualifying setups in the
 * last scan — the gate is doing its job": a claim about the RISK GATE,
 * assembled from a payload that ran no scan.
 *
 * The producer OMITS those blocks now when it scanned nothing (an empty list is
 * a scan that found nothing, which is a different fact), and this route carries
 * them forward with the age of the SCAN that produced them. The deep-scan block
 * beside it has been carried forward this way since it was written; this is the
 * same sentence one block over, and its own comment says so.
 *
 * NO TTL here, deliberately: the panel already bounds how old a scan may be
 * before it says so, and a second threshold in the ingest would be a second
 * answer about when a setup is out of date.
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

function freshServer() {
  const pool = {
    execute: async (sql) => {
      if (/FROM users WHERE id/.test(sql)) return [[{ plan: 'admin' }]];
      return [[]];
    },
  };
  const dbPath = require.resolve(path.join(APP, 'db.js'));
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true, exports: { pool } };
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
        ...(body ? { 'Content-Type': 'application/json',
                     'Content-Length': Buffer.byteLength(data) } : {}),
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

async function withServer(fn) {
  const server = freshServer();
  await new Promise((res) => server.listen(0, '127.0.0.1', res));
  try { return await fn(server.address().port); } finally { server.close(); }
}

const SECRET = process.env.BOT_SYNC_SECRET;

/** What a manual /scan pushes: three blocks that describe one scan. */
const A_REAL_SCAN = {
  circuit_breaker: { equity: 141.22, total_trades: 2, live_mode: true },
  regime: { label: 'NEUTRAL', gate: 63000 },
  symbols: { BTCUSDT: { score: 0.7, status: 'setup' } },
  entry_cards: [{ symbol: 'BTC', direction: 'LONG', entry: '62970',
                  stop_loss: '62750', tp1: '63300', rr: '1.5', trigger: 'RSI 55' }],
  entry_cards_read: { results: 40, above_floor: 3, considered: 3, cards: 1,
                      no_atr: 2, no_direction: 0, floor: 0.4, shown_max: 8 },
};

/** What the autonomous cycle pushes: the breaker and the regime, no scan. */
const A_CYCLE_SUMMARY = {
  circuit_breaker: { equity: 139.04, total_trades: 2, live_mode: true },
  regime: { label: 'RISK_OFF', gate: 61000 },
};

const post = (port, body) =>
  request(port, 'POST', '/api/bot/sync/scan', { body, secret: SECRET });
const get = (port) =>
  request(port, 'GET', '/api/bot/sync/scan', { token: TOKEN });

test('a cycle summary does not wipe the last scan\'s cards', async () => {
  await withServer(async (port) => {
    assert.equal((await post(port, A_REAL_SCAN)).status, 200);
    assert.equal((await post(port, A_CYCLE_SUMMARY)).status, 200);
    const { data } = await get(port);
    assert.equal(data.scan.entry_cards.length, 1, 'the summary wiped the cards');
    assert.equal(data.scan.entry_cards[0].symbol, 'BTC');
    assert.deepEqual(Object.keys(data.scan.symbols), ['BTCUSDT']);
  });
});

test('the reading travels with the cards it describes', async () => {
  // The counts and the list must always describe the SAME scan: carrying the
  // cards forward while the summary's own reading replaced them would be two
  // different scans on one payload.
  await withServer(async (port) => {
    await post(port, A_REAL_SCAN);
    await post(port, A_CYCLE_SUMMARY);
    const { data } = await get(port);
    assert.equal(data.scan.entry_cards_read.results, 40);
    assert.equal(data.scan.entry_cards_read.cards, 1);
  });
});

test('the summary still replaces what it is for', async () => {
  await withServer(async (port) => {
    await post(port, A_REAL_SCAN);
    await post(port, A_CYCLE_SUMMARY);
    const { data } = await get(port);
    assert.equal(data.scan.regime.label, 'RISK_OFF');
    assert.equal(data.scan.circuit_breaker.equity, 139.04);
  });
});

test('a real scan that found nothing DOES clear the cards', async () => {
  // An empty list is a reading: a scan ran and found nothing. Carrying stale
  // cards past it would publish setups the newest scan did not find.
  await withServer(async (port) => {
    await post(port, A_REAL_SCAN);
    await post(port, { ...A_CYCLE_SUMMARY, symbols: {}, entry_cards: [],
                       entry_cards_read: { results: 40, above_floor: 0, considered: 0,
                                           cards: 0, no_atr: 0, no_direction: 0,
                                           floor: 0.4, shown_max: 8 } });
    const { data } = await get(port);
    assert.deepEqual(data.scan.entry_cards, []);
    assert.equal(data.scan.entry_cards_read.above_floor, 0);
  });
});

test('scan_at is stamped by the scan, not by the summary that follows it', async () => {
  await withServer(async (port) => {
    await post(port, A_REAL_SCAN);
    const first = (await get(port)).data.scan;
    assert.ok(first.scan_at, 'a real scan stamps its own time');
    await new Promise((r) => setTimeout(r, 20));
    await post(port, A_CYCLE_SUMMARY);
    const second = (await get(port)).data.scan;
    assert.equal(second.scan_at, first.scan_at,
      'the summary re-dated cards it did not produce');
    assert.notEqual(second.received_at, first.received_at,
      'received_at is this push; scan_at is the scan');
  });
});

test('a fresh scan re-stamps scan_at', async () => {
  await withServer(async (port) => {
    await post(port, A_REAL_SCAN);
    const first = (await get(port)).data.scan.scan_at;
    await new Promise((r) => setTimeout(r, 20));
    await post(port, A_REAL_SCAN);
    const second = (await get(port)).data.scan.scan_at;
    assert.notEqual(second, first);
    assert.ok(Date.parse(second) > Date.parse(first));
  });
});

test('a summary with no scan ever stored carries no cards and no stamp', async () => {
  // The state the panel calls `no_scan`: this build pushes the three blocks
  // together, so a store that has only ever seen summaries has none of them.
  await withServer(async (port) => {
    await post(port, A_CYCLE_SUMMARY);
    const { data } = await get(port);
    assert.equal('entry_cards' in data.scan, false);
    assert.equal('entry_cards_read' in data.scan, false);
    assert.equal('scan_at' in data.scan, false);
  });
});

test('an empty cards list on the FIRST push is a scan, not an absence', async () => {
  await withServer(async (port) => {
    await post(port, { ...A_CYCLE_SUMMARY, entry_cards: [], symbols: {} });
    const { data } = await get(port);
    assert.deepEqual(data.scan.entry_cards, []);
    assert.ok(data.scan.scan_at, 'a list supplied IS a scan and gets a stamp');
  });
});

test('a block sent as null is a statement, not an absence', async () => {
  // PRESENCE, not truthiness. `[]` and `{}` are both truthy in JS, so no
  // payload this producer can build tells the two readings apart: every block
  // it sends is truthy and every block it omits is `undefined`. An explicit
  // `null` is the input that does, and carrying the last scan's cards over it
  // would publish setups the newest push had denied.
  await withServer(async (port) => {
    await post(port, A_REAL_SCAN);
    await post(port, { ...A_CYCLE_SUMMARY, entry_cards: null, symbols: null,
                       entry_cards_read: null });
    const { data } = await get(port);
    assert.equal(data.scan.entry_cards, null, 'a null was filled in with stale cards');
    assert.equal(data.scan.entry_cards_read, null);
  });
});

test('the ingest applies no freshness bound of its own', async () => {
  // The deep-scan block beside it is dropped past DEEPSCAN_TTL_MS. Reading the
  // route rather than waiting hours: the carry-forward loop must not consult a
  // clock, because the panel is the one place that decides a card is old.
  const src = require('node:fs').readFileSync(
    path.join(APP, 'routes', 'sync.js'), 'utf8');
  const start = src.indexOf('const scanBlocks = {};');
  const end = src.indexOf('latestScan = {', start);
  assert.ok(start > 0 && end > start);
  const block = src.slice(start, end);
  assert.equal(/TTL|Date\.now\(\) -/.test(block), false,
    'a second freshness answer in the ingest');
});

test('after a website restart, a summary push carries the SAVED scan forward', async () => {
  // A fresh process: `latestScan` is empty and the last scan is in the DB.
  const saved = {
    entry_cards: [{ symbol: 'SOL/USDT', direction: 'LONG' }],
    symbols: { 'SOL/USDT': { price: 150 } },
    entry_cards_read: { considered: 1, cards: 1 },
    scan_at: '2026-09-29T10:00:00.000Z',
  };
  const writes = [];
  const pool = {
    execute: async (sql, args) => {
      if (/FROM users WHERE id/.test(sql)) return [[{ plan: 'admin' }]];
      if (/SELECT scan_json/.test(sql)) return [[{ scan_json: JSON.stringify(saved) }]];
      if (/REPLACE INTO scan_cache/.test(sql)) writes.push(JSON.parse(args[0]));
      return [[]];
    },
  };
  const dbPath = require.resolve(path.join(APP, 'db.js'));
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true, exports: { pool } };
  delete require.cache[require.resolve(path.join(APP, 'routes', 'sync.js'))];
  const router = require(path.join(APP, 'routes', 'sync.js'));
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', router);
  const server = http.createServer(app);
  await new Promise((res) => server.listen(0, '127.0.0.1', res));
  try {
    const port = server.address().port;
    const r = await request(port, 'POST', '/api/bot/sync/scan', {
      body: { regime: { label: 'NEUTRAL', gate: 0 }, circuit_breaker: {} }, secret: SECRET,
    });
    assert.equal(r.status, 200);
    assert.equal(writes.length, 1);
    assert.deepEqual(writes[0].entry_cards, saved.entry_cards,
      'the first summary after a restart replaced the saved cards');
    assert.deepEqual(writes[0].symbols, saved.symbols);
    assert.equal(writes[0].scan_at, saved.scan_at, 'the cards keep their scan age');
  } finally { server.close(); }
});
