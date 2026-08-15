'use strict';
/**
 * The leaderboard ranked people on trades it could not price.
 *
 * NOT AN AUDIT FINDING. M11 named `routes/portfolio.js` and its remediation
 * said to reuse the scored-denominator pattern from sync.js. Re-running the
 * grep AFTER writing M11's test — the practice CLAUDE.md says found the bugs
 * the rule alone did not — turned up `routes/leaderboard.js:29` holding the
 * same query, byte for byte:
 *
 *     SELECT COALESCE(SUM(pnl), 0) as net_pnl, ... COUNT(*) as total_trades
 *     SELECT COUNT(*) as wins ... AND pnl > 0
 *     → return_pct = net / PAPER_BASE,  win_rate = wins / total_trades
 *
 * Being a RANKING makes both shapes worse than they are on a private panel:
 *
 *   - `parseFloat(net) || 0` scores an unpriceable book as a measured 0.00%
 *     return, and `s.trades > 0` then ADMITS that member to a public board —
 *     a handle published at a flat 0.00% with a depressed win rate, which is
 *     the "public track record showing 12 (7W/4L)" defect in CLAUDE.md;
 *   - and because the board is sorted, one member's fabricated 0.00% reorders
 *     everybody else against it.
 *
 * The board is opt-in and shows percent/count only (§4), so nothing here is a
 * dollar leak. It is a correctness and fairness defect on a public surface.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const APP = path.join(__dirname, '..');

/**
 * `books` maps user id → the CLOSED aggregate MySQL would return.
 * Every listed user is opted in with handle `u<id>`.
 */
function server(books) {
  const pool = {
    execute: async (sql, params) => {
      if (/leaderboard_handle IS NOT NULL/.test(sql)) {
        return [Object.keys(books).map((id) => (
          { id: Number(id), leaderboard_handle: `u${id}` }))];
      }
      if (/SELECT leaderboard_handle FROM users WHERE id/.test(sql)) {
        return [[{ leaderboard_handle: 'u1' }]];
      }
      if (/FROM trades/.test(sql)) return [[books[params[0]]]];
      return [[]];
    },
  };
  const dbPath = require.resolve(path.join(APP, 'db.js'));
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true,
                            exports: { pool } };
  const authPath = require.resolve(path.join(APP, 'auth.js'));
  require.cache[authPath] = { id: authPath, filename: authPath, loaded: true,
    exports: { authMiddleware: (req, _res, next) => {
      req.user = { user_id: 1 }; next();
    } } };
  delete require.cache[require.resolve(path.join(APP, 'routes', 'leaderboard.js'))];
  const app = express();
  app.use(express.json());
  app.use('/api/leaderboard', require(path.join(APP, 'routes', 'leaderboard.js')));
  return http.createServer(app);
}

function board(books) {
  return new Promise((resolve, reject) => {
    const s = server(books);
    s.listen(0, '127.0.0.1', () => {
      http.get({ port: s.address().port, path: '/api/leaderboard' }, (res) => {
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

test('a member whose closes cannot be priced is not ranked at 0.00%', async () => {
  const { body } = await board({
    1: { total: 3, scored: 3, wins: 2, net_pnl: 900 },   // real record
    2: { total: 4, scored: 0, wins: 0, net_pnl: null },  // nothing priceable
  });
  const handles = body.rows.map((r) => r.handle);
  assert.deepStrictEqual(handles, ['u1'],
    'u2 was published on a public board at a flat 0.00% return');
  assert.strictEqual(body.ranked_total, 1,
    'the unscorable member must not inflate the ranked population either');
});

test('an unpriced close leaves the win rate and the return', async () => {
  // Six closes, three priced, two of those won. The old arithmetic said
  // 2/6 = 33.3%; the honest answer is 2/3 = 66.7% over three priced closes.
  const { body } = await board({
    1: { total: 6, scored: 3, wins: 2, net_pnl: 600 },
  });
  const me = body.rows[0];
  assert.strictEqual(me.win_rate, 66.7, 'wins / COUNT(*) reported 33.3%');
  assert.strictEqual(me.return_pct, 6, '600 / 10000 stake');
  assert.strictEqual(me.trades, 6);
  assert.strictEqual(me.scored, 3);
  assert.strictEqual(me.unpriced, 3,
    'the coverage must travel with the rate, as it does on every other surface');
});

test('unpriced rows cannot reorder the board', async () => {
  // u2's real record is the best of the three. Under COALESCE it scored 0.00%
  // and ranked LAST behind a genuinely flat book.
  const { body } = await board({
    1: { total: 2, scored: 2, wins: 1, net_pnl: 100 },   // +1.00%
    2: { total: 5, scored: 1, wins: 1, net_pnl: 500 },   // +5.00%, 4 unpriced
    3: { total: 2, scored: 2, wins: 0, net_pnl: 0 },     // a real flat book
  });
  assert.deepStrictEqual(body.rows.map((r) => r.handle), ['u2', 'u1', 'u3']);
  assert.deepStrictEqual(body.rows.map((r) => r.rank), [1, 2, 3]);
});

test('a genuinely flat book is still ranked, at a real 0.00%', async () => {
  // The fix must not erase a measured zero: 0.00% over two priced closes is a
  // real result and belongs on the board.
  const { body } = await board({
    1: { total: 2, scored: 2, wins: 0, net_pnl: 0 },
  });
  assert.strictEqual(body.rows.length, 1, 'a measured flat book was dropped');
  assert.strictEqual(body.rows[0].return_pct, 0);
  assert.strictEqual(body.rows[0].win_rate, 0, 'two scored, none won — that IS 0%');
  assert.strictEqual(body.rows[0].unpriced, 0);
});
