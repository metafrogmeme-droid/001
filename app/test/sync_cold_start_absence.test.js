'use strict';
/**
 * The cold-start path, which is the one nothing ever exercised.
 *
 * GET /api/bot/sync/portfolio-summary has three sources, tried in order:
 * the in-memory summary, the persisted scan in `scan_cache`, and the trades
 * table. The middle one only runs in a fresh process that has not yet
 * received a sync — a restart, a new dyno, the first request after a deploy —
 * so every test that POSTs first takes the first branch and the middle one is
 * never reached.
 *
 * That is not a hypothetical gap. A mutation pass reverting this exact block
 * to `cb.net_pnl || 0` passed the whole express suite: 2318 tests, and the
 * branch that serves the operator's first page load after every restart was
 * not among them. The sibling ingest path WAS covered, which is what made the
 * gap invisible — the same asymmetry that let the equity null be cured on one
 * path and stamped back to 0 by the other.
 *
 * Separate file because the branch requires a module instance with no cached
 * summary, and node:test gives each file its own process.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const APP = path.join(__dirname, '..');
const TOKEN = jwt.sign({ user_id: 1, email: 'op@test.dev' }, process.env.JWT_SECRET);

/** A fresh sync router whose only data source is a scan persisted in the DB. */
function coldServer(circuit_breaker) {
  const pool = {
    execute: async (sql) => {
      if (/scan_cache/.test(sql)) {
        return [[{ scan_json: JSON.stringify({ circuit_breaker,
                                               timestamp: '2026-08-12T07:00:00Z' }) }]];
      }
      return [[]];
    },
  };
  const dbPath = require.resolve(path.join(APP, 'db.js'));
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true,
                            exports: { pool } };
  delete require.cache[require.resolve(path.join(APP, 'routes', 'sync.js'))];
  const router = require(path.join(APP, 'routes', 'sync.js'));
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', router);
  return http.createServer(app);
}

function summary(circuit_breaker) {
  return new Promise((resolve, reject) => {
    const server = coldServer(circuit_breaker);
    server.listen(0, '127.0.0.1', () => {
      http.get({ port: server.address().port, path: '/api/bot/sync/portfolio-summary',
                 headers: { Authorization: `Bearer ${TOKEN}` } }, (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => {
          server.close();
          resolve(JSON.parse(b || '{}').portfolio);
        });
      }).on('error', (e) => { server.close(); reject(e); });
    });
  });
}

test('a cold start does not invent figures the bot said it could not measure', async () => {
  const pf = await summary({
    live_mode: true, equity: 884.31,
    net_pnl: null, win_rate: null, total_trades: 12, open_count: 1,
  });
  assert.ok(pf, 'the cold-start branch produced no summary at all');
  assert.strictEqual(pf.net_pnl, null, '"$0.00" on an unpriceable record');
  assert.strictEqual(pf.win_rate, null, '"0%" claims all twelve lost');
  assert.strictEqual(pf.total_trades, 12);
  assert.strictEqual(pf.equity, 884.31);
});

test('a cold start keeps a genuinely measured zero', async () => {
  const pf = await summary({
    live_mode: true, equity: 1000, net_pnl: 0, win_rate: 0,
    total_trades: 8, open_count: 0,
  });
  assert.strictEqual(pf.net_pnl, 0, 'a measured break-even was discarded');
  assert.strictEqual(pf.win_rate, 0);
});

test('a cold start still honours the unavailable-equity flag', async () => {
  // The property the sibling test pinned on the ingest path. Asserted here
  // too, because "covered on one path" is exactly the assumption that failed.
  const pf = await summary({
    live_mode: true, live_unavailable: true, equity: null,
    net_pnl: null, win_rate: null, total_trades: 12, open_count: 2,
  });
  assert.strictEqual(pf.equity, null);
  assert.strictEqual(pf.live_unavailable, true);
  assert.strictEqual(pf.mode, 'LIVE');
});

test('a cold start forwards the unreadable-record flag', async () => {
  const pf = await summary({
    live_mode: true, equity: 884.31, net_pnl: null, win_rate: null,
    total_trades: 0, open_count: 0, record_unreadable: true,
  });
  assert.strictEqual(pf.record_unreadable, true);
});
