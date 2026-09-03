'use strict';
/**
 * A close closes THIS position, atomically -- not an arbitrary one, not half.
 *
 * Two defects in `POST /api/bot/sync/trade-event` for `event: 'close'`:
 *
 * 1. INSERT-then-DELETE ran as two autocommit statements. The comment beside
 *    them declined a transaction because "the in-memory pool this app falls
 *    back to without DATABASE_URL has no transaction support, and a safety
 *    property that only holds on one of two backends is not one." Right, and
 *    stale since MemoryDB learned begin/commit/rollback. The window it left
 *    -- an open row lingering beside its close until the next replace-all
 *    sync -- was a phantom position on /positions, on a money surface.
 *
 * 2. `DELETE ... WHERE user_id = ? AND symbol = ? AND status = 'OPEN' LIMIT 1`
 *    matched symbol alone. Two open positions on one symbol is a real state
 *    (the bot's own website_sync.py: "two opens of the same symbol at the
 *    same price would look like one"; dedupe_duplicate_positions exists for
 *    the adoption case), and the close deleted whichever row came first.
 *    The event always carries direction and entry_price, so it matches on
 *    them now and falls back to symbol-only ONLY when the tight key hit
 *    nothing.
 *
 * MemoryDB needed a matching fix: its LIMIT 1 branch ignored the tighter key
 * AND always answered affectedRows: 0 -- which would have turned the
 * fallback into a second delete on that backend. The shim now reports the
 * real count, like MySQL does.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { pool } = require('../db');

const SECRET = process.env.BOT_SYNC_SECRET;
let server, base, n = 0;

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
    const r = http.request(`${base}/api/bot/sync/trade-event`, {
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

const open = (symbol, direction, entry_price) =>
  post({ event: 'open', event_id: `ev-${++n}`, equity: 1000,
         trade: { symbol, direction, entry_price, size_usd: 100, fees: 0 } });
const close = (symbol, direction, entry_price, pnl = 5) =>
  post({ event: 'close', event_id: `ev-${++n}`, equity: 1000,
         trade: { symbol, direction, entry_price, exit_price: entry_price + 1,
                  size_usd: 100, pnl, fees: 0,
                  opened_at: '2026-09-01T00:00:00Z', closed_at: '2026-09-01T01:00:00Z' } });

const openRows = (symbol) =>
  pool.trades.filter((t) => t.user_id === 1 && t.symbol === symbol && t.status === 'OPEN');
const closedRows = (symbol) =>
  pool.trades.filter((t) => t.user_id === 1 && t.symbol === symbol && t.status === 'CLOSED');


test('a close removes the position it names, not another on the same symbol', async () => {
  const sym = 'SPEC/USDT';
  assert.equal((await open(sym, 'LONG', 100)).status, 200);
  assert.equal((await open(sym, 'LONG', 200)).status, 200);
  assert.equal(openRows(sym).length, 2, 'two same-symbol opens must both stand');

  assert.equal((await close(sym, 'LONG', 200)).status, 200);

  const left = openRows(sym);
  assert.equal(left.length, 1, 'exactly one open row must remain');
  assert.equal(Number(left[0].entry_price), 100,
    'THE DEFECT: the close at 200 removed the position opened at 100');
  assert.equal(closedRows(sym).length, 1);
});

test('a close whose key matches nothing still closes one row, never two', async () => {
  const sym = 'FALLB/USDT';
  await open(sym, 'LONG', 100);
  // entry_price rounded differently on the way over -- the tight key misses.
  assert.equal((await close(sym, 'LONG', 100.004)).status, 200);
  assert.equal(openRows(sym).length, 0, 'the fallback must close the lone open row');
  assert.equal(closedRows(sym).length, 1);
});

test('the fallback fires once, not once per branch', async () => {
  // Two opens, tight key misses both: the symbol-only fallback must remove ONE.
  const sym = 'ONCE/USDT';
  await open(sym, 'LONG', 100);
  await open(sym, 'LONG', 200);
  assert.equal((await close(sym, 'LONG', 999)).status, 200);
  assert.equal(openRows(sym).length, 1,
    'a miss on the tight key must not delete both same-symbol rows');
});

test('a failure after the INSERT leaves no half-close behind', async () => {
  const sym = 'ATOM/USDT';
  await open(sym, 'LONG', 100);
  const real = pool.execute;
  pool.execute = async (sql, ...rest) => {
    if (/^\s*DELETE FROM trades/i.test(String(sql))) throw new Error('ER_LOCK_DEADLOCK');
    return real.call(pool, sql, ...rest);
  };
  let res;
  try { res = await close(sym, 'LONG', 100); } finally { pool.execute = real; }

  assert.notEqual(res.status, 200, 'a close that did not complete must not report ok');
  assert.equal(openRows(sym).length, 1, 'the open row must survive the rollback');
  assert.equal(closedRows(sym).length, 0,
    'THE DEFECT: the CLOSED row was committed and the OPEN row stayed -- a phantom position');
});

test('MemoryDB reports the real affectedRows for a LIMIT 1 trades delete', async () => {
  const sym = 'COUNT/USDT';
  await open(sym, 'SHORT', 50);
  const [hit] = await pool.execute(
    "DELETE FROM trades WHERE user_id = ? AND symbol = ? AND status = 'OPEN' "
    + "AND direction = ? AND entry_price = ? LIMIT 1", [1, sym, 'SHORT', 50]);
  assert.equal(hit.affectedRows, 1, 'a matched delete must say so');
  const [miss] = await pool.execute(
    "DELETE FROM trades WHERE user_id = ? AND symbol = ? AND status = 'OPEN' "
    + "AND direction = ? AND entry_price = ? LIMIT 1", [1, sym, 'SHORT', 50]);
  assert.equal(miss.affectedRows, 0, 'a miss must not claim a row -- the old branch always said 0 and hid the difference');
});

// ── MemoryDB parses the statement, not the parameter count ──────────────────

test('a full-sync OPEN insert (eleven columns, with venue) is stored OPEN, as written', async () => {
  // Eleven params used to select the CLOSED branch: this row came back as a
  // closed trade whose exit_price was its size.
  await pool.execute(
    `INSERT INTO trades (user_id, symbol, direction, entry_price, size_usd, fees, status, pattern, stop_loss, take_profit, opened_at, venue)
     VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?)`,
    [1, 'ELEVEN/USDT', 'LONG', 10, 250, 0.1, null, 9, 12, new Date('2026-09-01T00:00:00Z'), 'hyperliquid']);
  const row = pool.trades.find((t) => t.symbol === 'ELEVEN/USDT');
  assert.equal(row.status, 'OPEN');
  assert.equal(Number(row.size_usd), 250, 'size must land in size_usd, not exit_price');
  assert.equal(row.exit_price, undefined);
  assert.equal(row.venue, 'hyperliquid');
});

test('a column the statement omits takes the schema default', async () => {
  await pool.execute(
    'INSERT INTO trades (user_id, symbol, direction, entry_price, size_usd) VALUES (?, ?, ?, ?, ?)',
    [1, 'DEFAULTS/USDT', 'SHORT', 5, 50]);
  const row = pool.trades.find((t) => t.symbol === 'DEFAULTS/USDT');
  assert.equal(row.status, 'OPEN', "schema: status VARCHAR(10) DEFAULT 'OPEN'");
  assert.equal(row.venue, 'bitget', "schema: venue DEFAULT 'bitget'");
  assert.equal(row.fees, 0);
  assert.ok(row.opened_at instanceof Date, 'schema: opened_at DEFAULT CURRENT_TIMESTAMP');
});

test('a statement whose columns and values disagree is refused, not guessed at', async () => {
  await assert.rejects(
    pool.execute('INSERT INTO trades (user_id, symbol, direction) VALUES (?, ?)', [1, 'X/USDT']),
    /columns.*values/);
  await assert.rejects(
    pool.execute('INSERT INTO trades (user_id, symbol) VALUES (?, ?)', [1]),
    /placeholders.*params/);
});
