'use strict';
/**
 * The operator's own dashboard scored its unpriced closes twice wrongly.
 *
 * `operatorPortfolio()` in routes/portfolio.js ran these two queries:
 *
 *     SELECT COALESCE(SUM(pnl), 0) as net_pnl, COUNT(*) as total_trades
 *       FROM trades WHERE user_id = ? AND status = 'CLOSED'
 *     SELECT COUNT(*) as wins
 *       FROM trades WHERE user_id = ? AND status = 'CLOSED' AND pnl > 0
 *
 * and returned `wins / total_trades`. `trades.pnl` is DECIMAL(14,2) and
 * NULLABLE, and routes/sync.js forwards the gateway's `pnl` uncoerced on both
 * of its insert paths, so a CLOSED row with no recorded P&L genuinely reaches
 * user 1. For every such row:
 *
 *   - `COALESCE(SUM(pnl), 0)` printed an unpriceable book as a measured $0.00
 *     — CLAUDE.md's "sum over a set that includes unreadable rows";
 *   - `COUNT(*)` put it in the win-rate DENOMINATOR but never the numerator,
 *     so each unpriced close dragged the operator's win rate down. That is
 *     `losses = len(all) - wins` wearing a different spelling.
 *
 * Both sibling paths — sync.js's DB fallback and its push handler — were
 * rewritten to score the priced rows explicitly. This function serves ONLY the
 * operator account (userId === BOT_USER_ID), which is why the sweep missed it.
 *
 * The stub answers the aggregate query directly, so what is under test is the
 * route's READER: which count it divides by, and what it does when nothing
 * could be priced at all.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_USER_ID = '1';

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const APP = path.join(__dirname, '..');

/**
 * Serve routes/portfolio.js for the operator with a planted CLOSED aggregate.
 * `agg` is exactly what MySQL returns for the scored-denominator query.
 */
function server(agg) {
  const pool = {
    execute: async (sql) => {
      if (/FROM equity_snapshots/.test(sql)) {
        return [[{ equity: '1000.00', snapshot_at: new Date() }]];
      }
      if (/FROM scan_cache/.test(sql)) return [[]];
      if (/status = 'OPEN'/.test(sql)) return [[]];
      if (/FROM trades/.test(sql)) return [[agg]];
      return [[]];
    },
  };
  const dbPath = require.resolve(path.join(APP, 'db.js'));
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true,
                            exports: { pool } };
  const authPath = require.resolve(path.join(APP, 'auth.js'));
  require.cache[authPath] = { id: authPath, filename: authPath, loaded: true,
    exports: { authMiddleware: (req, _res, next) => {
      req.user = { user_id: 1, email: 'op@test.io' }; next();
    } } };
  delete require.cache[require.resolve(path.join(APP, 'routes', 'portfolio.js'))];
  const app = express();
  app.use(express.json());
  app.use('/api/portfolio', require(path.join(APP, 'routes', 'portfolio.js')));
  return http.createServer(app);
}

function portfolio(agg) {
  return new Promise((resolve, reject) => {
    const s = server(agg);
    s.listen(0, '127.0.0.1', () => {
      http.get({ port: s.address().port, path: '/api/portfolio' }, (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => {
          s.close();
          resolve({ status: res.statusCode, body: JSON.parse(b || '{}') });
        });
      }).on('error', (e) => { s.close(); reject(e); });
    });
  });
}

test('an unpriced close leaves the win-rate denominator', async () => {
  // Five closes, two priced (one won), three with a NULL pnl.
  const { body } = await portfolio(
    { total: 5, scored: 2, wins: 1, net_pnl: 30 });
  assert.strictEqual(body.win_rate, 50,
    'wins / COUNT(*) reported 20% — three unscorable rows counted as losses');
  assert.strictEqual(body.total_trades, 5);
  assert.strictEqual(body.scored_trades, 2);
  assert.strictEqual(body.unpriced_trades, 3,
    'how much of the book the rate covers must travel with the rate');
});

test('a book with nothing priceable is null, not break-even', async () => {
  const { body } = await portfolio(
    { total: 4, scored: 0, wins: 0, net_pnl: null });
  assert.strictEqual(body.total_pnl, null,
    'COALESCE(SUM(pnl),0) printed an unpriceable book as a measured $0.00');
  assert.strictEqual(body.win_rate, null,
    '0% claims all four lost; the truth is none could be scored');
  assert.strictEqual(body.total_trades, 4);
  assert.strictEqual(body.unpriced_trades, 4);
});

test('a fully priced book still reads exactly as before', async () => {
  // The fix must not move a number that was already correct.
  const { body } = await portfolio(
    { total: 4, scored: 4, wins: 3, net_pnl: 125.5 });
  assert.strictEqual(body.total_pnl, 125.5);
  assert.strictEqual(body.win_rate, 75);
  assert.strictEqual(body.total_trades, 4);
  assert.strictEqual(body.unpriced_trades, 0);
});

test('a measured break-even is a real result, not an absence', async () => {
  // `if (total_pnl)` and `total_pnl || null` both erase this row. 0.0 is a
  // real, measured, break-even book and must print as one.
  const { body } = await portfolio(
    { total: 2, scored: 2, wins: 0, net_pnl: 0 });
  assert.strictEqual(body.total_pnl, 0, 'a scored 0.00 is a measurement');
  assert.strictEqual(body.win_rate, 0, 'two scored, none won — that IS 0%');
  assert.strictEqual(body.unpriced_trades, 0);
});
