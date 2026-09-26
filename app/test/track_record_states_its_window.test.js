'use strict';
/**
 * The public track record states the window it covers.
 *
 * `/api/bot/sync` REPLACES the operator's closed trades with the list the bot
 * sends, and the bot sends its newest closes, not every close it keeps. Each
 * sync that carries an equity reading also REPLACES the equity curve. So the
 * public count, win rate, profit factor, monthly buckets, drawdown and return
 * describe a window, and the payload said nothing about it: a reader took the
 * newest closes for the agent's whole history. The payload carries a
 * `coverage` block now, and so does the MCP tool that publishes the same rows.
 *
 * Driven through the real sync route and the real track route: an older
 * equity snapshot is planted, a sync replaces it, and the payload has to say
 * where its curve starts.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');
const { pool } = require('../db');

const SECRET = process.env.BOT_SYNC_SECRET;
const OPERATOR = parseInt(process.env.BOT_USER_ID) || 1;
let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/public', require('../routes/track'));
  server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => server && server.close());

function call(method, path, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : '';
    const r = http.request(`${base}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json',
                 'Content-Length': Buffer.byteLength(data),
                 'X-Bot-Secret': SECRET },
    }, (res) => {
      let s = '';
      res.on('data', (c) => { s += c; });
      res.on('end', () => resolve({ status: res.statusCode, body: s ? JSON.parse(s) : null }));
    });
    r.on('error', reject);
    r.end(data);
  });
}

const close = (i, pnl) => ({
  symbol: 'SOL/USDT', direction: 'LONG', entry_price: 100, exit_price: 101,
  size_usd: 100, pnl, fees: 0.1, pattern: 'm',
  opened_at: `2026-09-2${i}T00:00:00Z`, closed_at: `2026-09-2${i}T05:00:00Z`,
});

test('the track record says it covers the latest sync, and where its curve starts', async () => {
  const old = new Date(Date.now() - 30 * 86400e3);
  await pool.execute(
    'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
    [OPERATOR, 500, old]);
  const before = Date.now();
  const r = await call('POST', '/api/bot/sync',
    { equity: 812.5, positions: [], closed_trades: [close(1, 2), close(2, -1), close(3, 3)] });
  assert.equal(r.status, 200, JSON.stringify(r.body));

  // A later reading (the portfolio route writes these through between
  // syncs): the curve still starts at the sync, not at the newest point.
  const later = new Date(Date.now() + 3600e3);
  await pool.execute(
    'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
    [OPERATOR, 820, later]);
  const t = await call('GET', '/api/public/track-record');
  assert.equal(t.status, 200);
  const c = t.body.coverage;
  assert.ok(c, 'the payload does not say what window it covers');
  assert.equal(c.basis, 'latest_bot_sync');
  assert.equal(c.closes, 3);
  assert.equal(c.closes, t.body.stats.trades, 'two counts of one list');
  assert.match(c.note, /not necessarily its whole history/);
  assert.match(c.note, /since equity_since/);
  // The sync deleted the month-old snapshot: the curve starts at the sync.
  assert.ok(Date.parse(c.equity_since) >= before - 1000,
    `equity_since ${c.equity_since} predates the sync that replaced the curve`);
  assert.ok(Date.parse(c.equity_since) < later.getTime(),
    'equity_since names the newest reading, not where the curve starts');
  assert.doesNotMatch(JSON.stringify(c), /\$/);
});

test('an empty curve has no start, and says so with null', () => {
  const { recordCoverage } = require('../routes/track');
  assert.equal(recordCoverage([], []).equity_since, null);
  assert.equal(recordCoverage([], []).closes, 0);
});

test('a caller that publishes no equity says nothing about the curve', () => {
  const { recordCoverage } = require('../routes/track');
  const c = recordCoverage([{ closed_at: 'x' }]);
  assert.equal('equity_since' in c, false);
  assert.doesNotMatch(c.note, /equity/);
  assert.equal(c.closes, 1);
});

test('the MCP tool that publishes the same rows states the same window', async () => {
  const { handleRpc } = require('../routes/mcp');
  const out = await handleRpc({ jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: { name: 'get_track_record', arguments: {} } });
  const text = out.result.content[0].text;
  const payload = JSON.parse(text);
  assert.ok(payload.coverage, 'the machine-readable record omits its window');
  assert.equal(payload.coverage.basis, 'latest_bot_sync');
  assert.equal(payload.coverage.closes, payload.trades);
  assert.equal('equity_since' in payload.coverage, false);
});
