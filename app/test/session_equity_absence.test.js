'use strict';
/**
 * A paper user's session said their equity was exactly $10,000.00 when their
 * book could not be read.
 *
 * `getUserEquity` (app/auth.js), the value `sessionResponse` puts in the JSON
 * the SPA stores after EVERY successful auth — register, login, Telegram,
 * Google, the OAuth callback — and that `GET /auth/me` returns:
 *
 *     'SELECT COALESCE(SUM(pnl), 0) as total_pnl FROM trades ...'
 *     return 10000 + parseFloat(rows[0].total_pnl || 0);
 *
 * Two coercions stacked on one nullable column. `trades.pnl` is
 * `DECIMAL(14,2)` and nullable, and a CLOSED row with no recorded P&L
 * genuinely occurs — routes/sync.js forwards the gateway's `pnl` uncoerced —
 * so a paper user whose closed trades were all unpriced read the paper
 * baseline back: the same number as a brand-new account, and the same number
 * as one traded to precise break-even.
 *
 * The honest reading was already written four lines above it, for the
 * operator: "the honest answer is null ('unavailable'), never a fabricated
 * $10k". It was applied to that branch and not to this one.
 *
 * routes/portfolio.js, routes/leaderboard.js, routes/sync.js and
 * routes/trades.js were each cured of this same query. This was the fourth
 * path and was missed while the trade routes were being audited — the same
 * reason portfolio.js's own comment gives for having been missed itself.
 *
 * Driven through `sessionResponse`, not the arithmetic: the arithmetic
 * (aggregateStats) was already correct and already covered. The WIRING is
 * what shipped.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const APP = path.join(__dirname, '..');
const PAPER_BASE = 10000;

/**
 * Load auth.js against a planted `trades` table for user 7 (a paper user),
 * with no equity snapshot so the P&L fallback is the branch under test.
 * `closedPnls` is the pnl column of each CLOSED row; null means unpriced.
 */
function loadAuth(closedPnls, { userId = 7, snapshotRow } = {}) {
  const priced = closedPnls.filter((p) => p !== null && p !== undefined);
  const pool = {
    execute: async (sql) => {
      if (/FROM equity_snapshots/.test(sql)) {
        // `snapshotRow` undefined means NO ROW; anything else means a row
        // EXISTS carrying that value — including null. Collapsing those two
        // was how the first draft of this file asserted the unparseable-
        // snapshot guard and never reached it: no row meant it fell through
        // to the P&L branch, and the test passed for the wrong reason.
        return [snapshotRow === undefined ? [] : [{ equity: snapshotRow }]];
      }
      if (/FROM trades/.test(sql)) {
        // Answer the scored aggregate the way MySQL does: SUM over an empty
        // set is NULL, not 0, and that distinction is the whole point.
        return [[{
          total: closedPnls.length,
          scored: priced.length,
          wins: priced.filter((p) => p > 0).length,
          net_pnl: priced.length ? priced.reduce((a, b) => a + b, 0) : null,
        }]];
      }
      if (/FROM users/.test(sql)) {
        return [[{ id: userId, email: 'p@test.dev', plan: 'free',
                   telegram_linked: 0, email_verified: 1,
                   referral_code: 'ABC', token_epoch: 0 }]];
      }
      return [[]];
    },
  };
  const dbPath = require.resolve(path.join(APP, 'db.js'));
  require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true,
                            exports: { pool } };
  delete require.cache[require.resolve(path.join(APP, 'auth.js'))];
  return require(path.join(APP, 'auth.js'));
}

const equityFor = async (pnls, opts) => {
  const { sessionResponse } = loadAuth(pnls, opts);
  const s = await sessionResponse({ id: (opts && opts.userId) || 7 });
  return s.equity;
};

test('an all-unpriced closed book does not read as the paper baseline', async () => {
  // THE defect. Three closed trades, none of them priced.
  const equity = await equityFor([null, null, null]);
  assert.strictEqual(equity, null,
    'an unreadable book was reported as a measured $10,000.00');
});

test('a partially priced book does not print a partial total as a whole one', async () => {
  // 5 closed, 2 unpriced. `10000 + sum(the 3 we can read)` is a real number
  // that is not this account's equity.
  const equity = await equityFor([100, -40, 25, null, null]);
  assert.strictEqual(equity, null, 'a partial sum was published as the total');
});

test('a fully priced book still reports a real number', async () => {
  // The fix must not make equity unavailable for the ordinary case.
  const equity = await equityFor([100, -40, 25]);
  assert.strictEqual(equity, PAPER_BASE + 85);
});

test('a break-even book reports the baseline as a MEASURED value', async () => {
  // 0 is falsy and 0 is a real, measured outcome. This is the same defect
  // facing the other way, and it must not be traded for the first one.
  const equity = await equityFor([50, -50]);
  assert.strictEqual(equity, PAPER_BASE,
    'a measured break-even was hidden as an absence');
});

test('no closed trades at all is a reading, not an absence', async () => {
  // A paper account that has closed nothing holds exactly the starting stake.
  const equity = await equityFor([]);
  assert.strictEqual(equity, PAPER_BASE);
});

test('a synced snapshot still wins', async () => {
  // A readable snapshot short-circuits the P&L branch entirely.
  assert.strictEqual(
    await equityFor([null, null], { snapshotRow: '4231.55' }), 4231.55);
});

test('a snapshot row that exists with an unreadable equity is null, not NaN', async () => {
  // The row EXISTS, so the P&L fallback is never reached — this is the
  // snapshot guard itself. parseFloat hands back NaN, which survives
  // arithmetic and only becomes null by accident at JSON.stringify; a
  // consumer doing math on it gets NaN, not an absence.
  // NOT `undefined` — that is this harness's sentinel for NO ROW, which
  // correctly falls through to the P&L branch. A value the test cannot
  // express is not a case the code gets wrong.
  for (const bad of [null, '', 'n/a', {}, 'NaN']) {
    const e = await equityFor([100], { snapshotRow: bad });
    assert.strictEqual(e, null, `snapshot equity ${JSON.stringify(bad)}`);
    assert.ok(!Number.isNaN(e), 'NaN is not a way of saying "unavailable"');
  }
});

test('the operator still gets null rather than a paper baseline', async () => {
  // The branch that was already right must stay right.
  const equity = await equityFor([100, 200], { userId: 1 });
  assert.strictEqual(equity, null);
});
