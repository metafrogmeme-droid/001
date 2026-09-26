'use strict';
/**
 * An open-position count nobody read is not a flat book.
 *
 * Both cache writers in routes/sync.js built the summary's `open_count` as
 * `cb.open_count || 0`, under a comment saying `_build_scan_payload` "only
 * ever raises it from a real read". Driven on 2026-09-26 it did not: when the
 * venue readout failed (or was skipped by a /venue switch away from Bitget)
 * and the engine's balance cache was stale, the bot sent `open_count: 0` for a
 * book nobody had looked at, and the slot chip read "Open Positions: 0/5" in
 * green. The bot sends null for that now, and this is the website's half: a
 * null must reach the summary as null, on the ingest path AND on the
 * cold-start path, while a counted 0 still arrives as 0.
 *
 * The summary is served to anonymous callers too, and `open_count` is a count,
 * so the public view carries it. That is why the null matters here: "0 open
 * positions" is a public claim about the operator's book.
 *
 * Separate file because the cold-start branch needs a module instance with no
 * cached summary, and node:test gives each file its own process.
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

/** A fresh sync router whose only persisted scan is `circuit_breaker`. */
function freshServer(circuit_breaker) {
  const pool = {
    execute: async (sql) => {
      if (/FROM users WHERE id/.test(sql)) return [[{ plan: 'admin' }]];
      if (/SELECT scan_json FROM scan_cache/.test(sql)) {
        if (!circuit_breaker) return [[]];
        return [[{ scan_json: JSON.stringify({ circuit_breaker,
                                               timestamp: '2026-09-26T07:00:00Z' }) }]];
      }
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

async function withServer(circuit_breaker, fn) {
  const server = freshServer(circuit_breaker);
  await new Promise((res) => server.listen(0, '127.0.0.1', res));
  try {
    return await fn(server.address().port);
  } finally {
    server.close();
  }
}

// The payload the bot sends when the venue readout failed and the balance
// cache was stale: the realized record from the trade file, and nothing
// about the account as it stands.
const UNREAD_BOOK = {
  live_mode: true, live_unavailable: true, equity: null,
  net_pnl: 6.0, win_rate: 50.0, total_trades: 2, open_count: null,
};

test('the cold-start path keeps an unread open count unread', async () => {
  await withServer(UNREAD_BOOK, async (port) => {
    const op = await request(port, 'GET', '/api/bot/sync/portfolio-summary', { token: TOKEN });
    assert.equal(op.status, 200);
    assert.equal(op.data.portfolio.open_count, null,
      'an unread book was published as a count');
    // The realized record the trade file supports still arrives.
    assert.equal(op.data.portfolio.total_trades, 2);
  });
});

test('the anonymous summary does not say the operator holds nothing', async () => {
  await withServer(UNREAD_BOOK, async (port) => {
    const anon = await request(port, 'GET', '/api/bot/sync/portfolio-summary');
    assert.equal(anon.status, 200);
    assert.equal(anon.data.portfolio.open_count, null);
  });
});

test('a count the bot did not send at all is null, not 0', async () => {
  const { open_count: _omit, ...noCount } = UNREAD_BOOK;
  await withServer(noCount, async (port) => {
    const op = await request(port, 'GET', '/api/bot/sync/portfolio-summary', { token: TOKEN });
    assert.equal(op.data.portfolio.open_count, null);
  });
});

test('a counted flat book is still 0 on the cold-start path', async () => {
  await withServer({ ...UNREAD_BOOK, live_unavailable: false, equity: 141.22, open_count: 0 },
    async (port) => {
      const op = await request(port, 'GET', '/api/bot/sync/portfolio-summary', { token: TOKEN });
      assert.equal(op.data.portfolio.open_count, 0);
    });
});

test('the scan ingest keeps an unread open count unread', async () => {
  await withServer(null, async (port) => {
    const secret = process.env.BOT_SYNC_SECRET;
    const r = await request(port, 'POST', '/api/bot/sync/scan',
      { body: { symbols: {}, circuit_breaker: UNREAD_BOOK }, secret });
    assert.equal(r.status, 200);
    const op = await request(port, 'GET', '/api/bot/sync/portfolio-summary', { token: TOKEN });
    assert.equal(op.data.portfolio.open_count, null,
      'the ingest stamped an unread book back to 0');
  });
});

test('the scan ingest keeps a counted 0 as 0', async () => {
  await withServer(null, async (port) => {
    const secret = process.env.BOT_SYNC_SECRET;
    await request(port, 'POST', '/api/bot/sync/scan', {
      body: { symbols: {}, circuit_breaker: { ...UNREAD_BOOK, live_unavailable: false,
                                               equity: 141.22, open_count: 0 } },
      secret,
    });
    const op = await request(port, 'GET', '/api/bot/sync/portfolio-summary', { token: TOKEN });
    assert.equal(op.data.portfolio.open_count, 0);
  });
});
