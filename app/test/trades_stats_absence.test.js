'use strict';
/**
 * `losses: totalTrades - wins` on the operator's own stats panel.
 *
 * GET /api/trades/stats built its answer from three queries and got the same
 * nullable column wrong three ways:
 *
 *     COALESCE(SUM(pnl), 0) as net_pnl        -> unpriceable book reads $0.00
 *     winRate = wins / COUNT(*)               -> unpriced closes drag it down
 *     losses: totalTrades - wins              -> unpriced closes ARE losses
 *
 * `trades.pnl` is `DECIMAL(14,2)`, nullable, so all three are reachable. A
 * book of 20 closes with 8 of them unpriced and 6 of the 12 priced ones
 * winning read **30% · 14 losses** instead of **50% · 6 losses** — worse than
 * the account was, in the number the operator checks first. And the third
 * line is in CLAUDE.md's table of banned shapes verbatim.
 *
 * WHY THIS FILE EXISTS SEPARATELY FROM trade_stats_absence.test.js. That file
 * pins the shared reader, which is correct and was already correct before the
 * route used it. A mutant that reverted the ROUTE to `totalTrades - wins`
 * sailed straight through the entire express suite: the arithmetic was
 * covered and the wiring was not, and the wiring is what shipped. So this
 * one runs the actual handler against a planted table.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const APP = path.join(__dirname, '..');
const TOKEN = jwt.sign({ user_id: 1, email: 'op@test.dev' }, process.env.JWT_SECRET);

/** Serve routes/trades.js against a planted `trades` table. */
function makeServer(rows) {
  const closed = rows.filter((r) => (r.status || 'CLOSED') === 'CLOSED');
  const open = rows.filter((r) => r.status === 'OPEN');
  const pool = {
    execute: async (sql) => {
      if (/FROM trades WHERE user_id = \? AND status = \?/.test(sql)
          && /SELECT pnl/.test(sql)) {
        return [closed.map((r) => ({ pnl: r.pnl, size_usd: r.size_usd ?? 100,
                                     fees: r.fees ?? 0 }))];
      }
      if (/open_count/.test(sql)) return [[{ open_count: open.length }]];
      if (/equity_snapshots/.test(sql)) return [[{ equity: '1000.00' }]];
      return [[]];
    },
  };
  const dbPath = require.resolve(path.join(APP, 'db.js'));
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true,
                            exports: { pool } };
  delete require.cache[require.resolve(path.join(APP, 'routes', 'trades.js'))];
  const router = require(path.join(APP, 'routes', 'trades.js'));
  const app = express();
  app.use(express.json());
  app.use('/api/trades', router);
  return http.createServer(app);
}

function stats(rows) {
  return new Promise((resolve, reject) => {
    const server = makeServer(rows);
    server.listen(0, '127.0.0.1', () => {
      http.get({ port: server.address().port, path: '/api/trades/stats',
                 headers: { Authorization: `Bearer ${TOKEN}` } }, (res) => {
        let b = '';
        res.on('data', (d) => { b += d; });
        res.on('end', () => {
          server.close();
          resolve({ status: res.statusCode, body: JSON.parse(b || '{}') });
        });
      }).on('error', (e) => { server.close(); reject(e); });
    });
  });
}

const closed = (...pnls) => pnls.map((p) => ({ pnl: p, size_usd: 100, fees: 0 }));

test('unpriced closes are not losses and not in the denominator', async () => {
  // 20 closed, 12 priced, 6 of those won. The route reported 30% / 14L.
  const rows = closed(...Array(6).fill(10), ...Array(6).fill(-5),
                      ...Array(8).fill(null));
  const { status, body } = await stats(rows);
  assert.equal(status, 200);
  assert.strictEqual(body.total_trades, 20);
  assert.strictEqual(body.win_rate, 50, 'unpriced closes were in the denominator');
  assert.strictEqual(body.wins, 6);
  assert.strictEqual(body.losses, 6, 'unpriced closes were filed as defeats');
  assert.strictEqual(body.unpriced, 8);
});

test('a break-even close is neither a win nor a loss', async () => {
  const { body } = await stats(closed(10, 0, -5));
  assert.strictEqual(body.wins, 1);
  assert.strictEqual(body.losses, 1, '0.00 is flat, not down');
  assert.strictEqual(body.breakeven, 1);
  assert.ok(Math.abs(body.win_rate - 33.3) < 0.05);
});

test('the buckets account for every closed trade', async () => {
  const { body } = await stats(closed(10, -5, 0, null, 3));
  assert.strictEqual(body.wins + body.losses + body.breakeven + body.unpriced,
    body.total_trades, 'losses derived by subtraction would still satisfy this '
    + 'only by absorbing the other two buckets');
  assert.strictEqual(body.losses, 1);
});

test('a book nothing can price reports null, not zero', async () => {
  const { body } = await stats(closed(null, null, null));
  assert.strictEqual(body.net_pnl, null, 'COALESCE(SUM(pnl),0) claims break-even');
  assert.strictEqual(body.win_rate, null, '0% claims all three lost');
  assert.strictEqual(body.total_trades, 3);
  assert.strictEqual(body.unpriced, 3);
});

test('an empty book claims nothing', async () => {
  const { body } = await stats([]);
  assert.strictEqual(body.total_trades, 0);
  assert.strictEqual(body.net_pnl, null);
  assert.strictEqual(body.win_rate, null);
  assert.strictEqual(body.losses, 0);
});

test('a measured flat book still reports its zero', async () => {
  // The other direction: suppressing a real 0 is the same defect facing the
  // other way. Two closes that both broke even is a result.
  const { body } = await stats(closed(0, 0));
  assert.strictEqual(body.net_pnl, 0);
  assert.strictEqual(body.win_rate, 0, 'neither won — that IS 0%');
  assert.strictEqual(body.breakeven, 2);
  assert.strictEqual(body.losses, 0);
});

test('the realized total covers only the rows it could read', async () => {
  const { body } = await stats(closed(10.5, -4.25, null));
  assert.strictEqual(body.net_pnl, 6.25);
});

test('DECIMAL columns arriving as strings are parsed, not dropped', async () => {
  // mysql2 returns DECIMAL as a string; the reader has to parse, and a failed
  // parse must land on "absent", never on 0.
  const { body } = await stats([{ pnl: '10.50', size_usd: '100', fees: '0.25' },
                                { pnl: '-2.25', size_usd: '100', fees: '0.25' }]);
  assert.strictEqual(body.net_pnl, 8.25);
  assert.strictEqual(body.total_fees, 0.5);
  assert.strictEqual(body.win_rate, 50);
});

test('no losing trade yet is not a profit factor of zero', async () => {
  const { body } = await stats(closed(10, 20));
  assert.strictEqual(body.profit_factor, null);
  assert.strictEqual(body.win_rate, 100);
});
