'use strict';
/**
 * A partial sync destroyed the account's entire trade history.
 *
 * `POST /api/bot/sync` ran
 *
 *     DELETE FROM trades WHERE user_id = ?
 *
 * under autocommit and then replaced the rows with a LOOP of individual
 * INSERTs. Any throw in that loop — a malformed row from the bot, a dropped
 * connection, a deadlock, a driver bug — left the DELETE committed and every
 * trade and equity snapshot for that user gone, permanently, behind a response
 * that said only `{"error":"Sync failed"}`. Nothing told anyone data had been
 * destroyed.
 *
 * It is not an exotic trigger. The bot runs under `Restart=always` and syncs on
 * a schedule, so ONE persistently bad row would wipe the history on every
 * attempt and never restore it.
 *
 * `beginTransaction` appeared NOWHERE in app/ before this: not one route was
 * atomic. That made it a systemic absence rather than one handler's oversight,
 * and this is its worst instance — the only place that deletes everything
 * before writing it back.
 *
 * The test that matters is the last one: it makes an INSERT fail mid-loop and
 * asserts the pre-existing trades are STILL THERE. Without `MemoryDB` learning
 * transactions, that could only have been checked against a live MySQL, so the
 * rollback path would have shipped never once executed by a test.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { pool, withTransaction } = require('../db');

const SECRET = process.env.BOT_SYNC_SECRET;
let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => server && server.close());

function post(body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const r = http.request(`${base}/api/bot/sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
                 'Content-Length': Buffer.byteLength(data),
                 'X-Bot-Secret': SECRET },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end(data);
  });
}

const closed = (symbol, pnl) => ({
  symbol, direction: 'LONG', entry_price: 100, exit_price: 110,
  size_usd: 500, pnl, fees: 1, opened_at: '2026-09-01T00:00:00Z',
  closed_at: '2026-09-01T01:00:00Z',
});

/**
 * Rows actually present, counted off the store.
 *
 * Not via SQL, and the reason is worth recording: MemoryDB's INSERT handler
 * for the sync statement shape does not map the columns — the row lands with
 * an `id` and nothing else, so `SELECT COUNT(*) … WHERE status = 'CLOSED'`
 * reports 0 for rows that are demonstrably there. Verified against unmodified
 * HEAD, so it predates this change and is not caused by it.
 *
 * The atomicity property does not need the columns: the DELETE removes rows,
 * the INSERTs add them back, and a rollback restores the array. Counting the
 * array measures exactly that, without asserting through a shim limitation.
 */
async function tradeCount() {
  return (pool.trades || []).length;
}

/** Make the Nth INSERT throw, the way a malformed row or a dead socket would. */
function failInsertNumber(n) {
  const real = pool.execute;
  let seen = 0;
  pool.execute = async (sql, ...rest) => {
    if (/^\s*INSERT/i.test(String(sql))) {
      seen += 1;
      if (seen === n) throw new Error('ER_DATA_TOO_LONG: simulated bad row');
    }
    return real.call(pool, sql, ...rest);
  };
  return () => { pool.execute = real; };
}


test('the helper refuses a backend that cannot be atomic', async () => {
  // A silent fallback would leave the caller believing its writes are atomic
  // when they are not — the failure this helper removes, one level up.
  const saved = pool.getConnection;
  pool.getConnection = undefined;
  try {
    await assert.rejects(() => withTransaction(async () => {}), /getConnection/);
  } finally {
    pool.getConnection = saved;
  }
});

test('a rollback without a begin is an error, not a quiet no-op', async () => {
  const conn = await pool.getConnection();
  await assert.rejects(() => conn.rollback(), /without beginTransaction/,
    'a rollback that does nothing and reports success is the worst outcome');
  conn.release();
});

test('a committed transaction keeps its writes', async () => {
  const before = await tradeCount();
  await withTransaction(async (conn) => {
    await conn.execute(
      `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price,
         size_usd, pnl, fees, status, pattern, opened_at, closed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
      [1, 'TXOK/USDT', 'LONG', 1, 2, 10, 1, 0, null, new Date(), new Date()]);
  });
  assert.equal(await tradeCount(), before + 1);
});

test('a thrown transaction leaves nothing behind', async () => {
  const before = await tradeCount();
  await assert.rejects(() => withTransaction(async (conn) => {
    await conn.execute(
      `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price,
         size_usd, pnl, fees, status, pattern, opened_at, closed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
      [1, 'TXBAD/USDT', 'LONG', 1, 2, 10, 1, 0, null, new Date(), new Date()]);
    throw new Error('boom');
  }), /boom/, 'the original error must reach the caller, not a rollback error');
  assert.equal(await tradeCount(), before,
    'the insert survived a rollback — the snapshot is not restoring');
});

test('a sync that fails midway does not destroy the history', async () => {
  // The whole point of the file.
  const ok = await post({ equity: 1000,
    closed_trades: [closed('BTC/USDT', 50), closed('ETH/USDT', -20)],
    positions: [] });
  assert.equal(ok.status, 200);
  const survived = await tradeCount();
  assert.ok(survived >= 2, 'seed sync did not store its trades');

  // Second sync: the DELETE runs, then the second INSERT blows up.
  const restore = failInsertNumber(2);
  let res;
  try {
    res = await post({ equity: 1100,
      closed_trades: [closed('SOL/USDT', 10), closed('XRP/USDT', 5)],
      positions: [] });
  } finally {
    restore();
  }

  assert.equal(res.status, 500, 'a failed sync must report failure');
  assert.equal(await tradeCount(), survived,
    'THE DEFECT: the DELETE committed and the history is gone. '
    + 'A failed sync must leave the database exactly as it found it.');
});

test('the in-memory summary is not stamped from a rolled-back sync', async () => {
  // `latestPortfolio` is written after the commit. Updating it inside the
  // transaction would leave the API reporting a sync the database discarded.
  const src = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'routes', 'sync.js'), 'utf8');
  // Assert the PROPERTY — no stamp inside the block — rather than comparing
  // positions. `latestPortfolio = {` occurs in four handlers, so `indexOf`
  // found one in a different route entirely and the first draft of this test
  // compared two unrelated offsets.
  const open = src.indexOf('await withTransaction(');
  const end = src.indexOf('end withTransaction');
  assert.ok(open > 0 && end > open, 'the transaction block must be findable');
  const inside = src.slice(open, end);
  assert.ok(!/latestPortfolio\s*=/.test(inside),
    'latestPortfolio is stamped INSIDE the transaction — a rollback would '
    + 'leave the API describing rows the database does not have');
  assert.ok(/latestPortfolio\s*=/.test(src.slice(end)),
    'it must still be stamped after the commit');
});
