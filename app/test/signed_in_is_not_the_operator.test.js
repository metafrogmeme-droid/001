'use strict';
/**
 * SIGNED IN IS NOT THE OPERATOR.
 *
 * Registration is open and issues a session at once, and four surfaces read
 * `req.user` as "the operator may see this": the flight record (every sealed
 * decision, other accounts included, in dollars), the weekly letter (the
 * operator's net P&L), the scan payload (the operator's equity and daily P&L)
 * and the portfolio summary. A free signup read all four. The summary's
 * database fallback was worse: unscoped, it answered the newest snapshot of
 * whichever account wrote last beside a P&L summed across every account, and
 * cached that for every later reader. And the live stream, which has no auth
 * at all, sent the dollar P&L of every close to anyone listening.
 *
 * The answer is `lib/operator_view.isOperator`: the plan, re-read from the
 * database, never the JWT -- the rule the operator-only controls already use.
 * Everything here is DRIVEN through the routes; the flight record's half is in
 * `guardian_redaction.test.js`.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const APP = path.join(__dirname, '..');
const { pool } = require('../db');

const OPERATOR = 1;          // BOT_USER_ID's default: the account the bot syncs as
const STRANGER = 777;
pool.users.push({ id: OPERATOR, email: 'op@example.com', plan: 'admin' });
pool.users.push({ id: STRANGER, email: 'free@example.com', plan: 'free' });
const OP_TOKEN = jwt.sign({ user_id: OPERATOR, email: 'op@example.com' }, process.env.JWT_SECRET);
const STRANGER_TOKEN = jwt.sign({ user_id: STRANGER, email: 'free@example.com' }, process.env.JWT_SECRET);

/** A fresh sync router (its caches start empty), with the stream stubbed. */
function freshSync(broadcasts) {
  const streamPath = require.resolve(path.join(APP, 'routes', 'stream.js'));
  const real = require(streamPath);
  require.cache[streamPath].exports = {
    ...real, broadcast: (type, data) => broadcasts.push({ type, data }),
  };
  delete require.cache[require.resolve(path.join(APP, 'routes', 'sync.js'))];
  const router = require(path.join(APP, 'routes', 'sync.js'));
  require.cache[streamPath].exports = real;
  return router;
}

async function serve(router, mount) {
  const app = express();
  app.use(express.json());
  app.use(mount, router);
  const server = await new Promise((r) => { const s = app.listen(0, '127.0.0.1', () => r(s)); });
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

function call(base, method, p, { token, body, secret } = {}) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? null : JSON.stringify(body);
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(secret ? { 'X-Bot-Secret': process.env.BOT_SYNC_SECRET } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end(data);
  });
}

const CB = { equity: 884.31, net_pnl: 812.4, daily_pnl: -12.5, win_rate: 55.0,
             total_trades: 20, open_count: 1, live_mode: true };

// FIRST: every later test persists a scan, and a persisted scan is answered
// before the database fallback is reached.
test('the summary\'s database fallback reads the OPERATOR\'s rows, not whoever wrote last', async () => {
  const older = new Date(Date.now() - 3600_000), newer = new Date();
  await pool.execute('INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
    [OPERATOR, 500, older]);
  await pool.execute('INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
    [STRANGER, 99_999, newer]);
  pool.trades = pool.trades || [];
  pool.trades.push({ id: 9001, user_id: OPERATOR, status: 'CLOSED', pnl: 10 },
                   { id: 9002, user_id: STRANGER, status: 'CLOSED', pnl: 5000 });
  const { server, base } = await serve(freshSync([]), '/api/bot/sync');
  try {
    const op = await call(base, 'GET', '/api/bot/sync/portfolio-summary', { token: OP_TOKEN });
    assert.equal(op.data.portfolio.equity, 500, 'another account\'s newer snapshot was served as the agent\'s');
    assert.equal(op.data.portfolio.net_pnl, 10, 'a P&L summed across accounts was served as the agent\'s');
    assert.equal(op.data.portfolio.total_trades, 1);
  } finally { server.close(); }
});

test('the scan: the operator reads the account, a signed-in stranger does not', async () => {
  const { server, base } = await serve(freshSync([]), '/api/bot/sync');
  try {
    assert.equal((await call(base, 'POST', '/api/bot/sync/scan',
      { secret: true, body: { regime: 'CHOP', circuit_breaker: CB } })).status, 200);

    const op = await call(base, 'GET', '/api/bot/sync/scan', { token: OP_TOKEN });
    assert.equal(op.data.scan.circuit_breaker.equity, 884.31);
    assert.equal(op.data.scan.disclosure, undefined);

    const st = await call(base, 'GET', '/api/bot/sync/scan', { token: STRANGER_TOKEN });
    assert.equal(st.data.scan.circuit_breaker.equity, undefined, 'a free signup read the operator\'s equity');
    assert.equal(st.data.scan.circuit_breaker.net_pnl, undefined);
    assert.equal(st.data.scan.circuit_breaker.win_rate, 55.0, 'the rates are the public half');
    assert.equal(st.data.scan.regime, 'CHOP');
    assert.doesNotMatch(st.data.scan.disclosure, /sign in/i,
      'told a signed-in caller to sign in');
  } finally { server.close(); }
});

test('the summary: the same line, on the same payload', async () => {
  const { server, base } = await serve(freshSync([]), '/api/bot/sync');
  try {
    await call(base, 'POST', '/api/bot/sync/scan', { secret: true, body: { circuit_breaker: CB } });
    const op = await call(base, 'GET', '/api/bot/sync/portfolio-summary', { token: OP_TOKEN });
    assert.equal(op.data.portfolio.equity, 884.31);
    const st = await call(base, 'GET', '/api/bot/sync/portfolio-summary', { token: STRANGER_TOKEN });
    assert.equal(st.data.portfolio.equity, undefined);
    assert.equal(st.data.portfolio.net_pnl, undefined);
    assert.equal(st.data.portfolio.total_trades, 20);
    assert.match(st.data.portfolio.disclosure, /shown to the operator only/);
  } finally { server.close(); }
});

test('the live stream carries no dollar figure on a close', async () => {
  const sent = [];
  const { server, base } = await serve(freshSync(sent), '/api/bot/sync');
  try {
    const r = await call(base, 'POST', '/api/bot/sync', { secret: true, body: {
      equity: 1000, positions: [],
      closed_trades: [{ symbol: 'PENDLE/USDT', direction: 'LONG', entry_price: 2.3, exit_price: 2.1,
                        size_usd: 100, pnl: -137.42, fees: 0.1, pattern: 'x',
                        opened_at: '2026-09-20T00:00:00Z', closed_at: '2026-09-21T00:00:00Z' }],
    } });
    assert.equal(r.status, 200, JSON.stringify(r.data));
    const trade = sent.filter((e) => e.type === 'trade' && e.data);
    assert.equal(trade.length, 1, `expected one trade nudge, got ${JSON.stringify(sent)}`);
    assert.equal(trade[0].data.symbol, 'PENDLE/USDT');
    assert.equal('pnl' in trade[0].data, false, 'the unauthenticated stream carried the close\'s P&L');
    assert.doesNotMatch(JSON.stringify(trade[0].data), /137/);
  } finally { server.close(); }
});

// ── the letter ────────────────────────────────────────────────────────────

async function letterServer() {
  const authModule = require('../auth');
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/letter', require('../routes/letter'));
  const server = await new Promise((r) => { const s = app.listen(0, '127.0.0.1', () => r(s)); });
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function plantOperatorWeek() {
  const letter = require('../lib/letter');
  const week = letter.lastCompletedWeek();
  const mid = new Date(week.start.getTime() + 2 * 86_400_000);
  pool.trades = pool.trades || [];
  pool.trades.push({ id: 9101, user_id: OPERATOR, symbol: 'BTC/USDT', direction: 'LONG',
                     entry_price: 100, exit_price: 90, size_usd: 1000, pnl: -1249.5,
                     fees: 1, status: 'CLOSED', opened_at: mid, closed_at: mid });
  return week;
}

test('the letter: the operator reads their dollars, a signed-in stranger reads the public letter', async () => {
  const week = await plantOperatorWeek();
  const { server, base } = await letterServer();
  try {
    const op = await call(base, 'GET', '/api/letter/latest', { token: OP_TOKEN });
    assert.equal(op.status, 200);
    assert.equal(op.data.public, undefined);
    assert.match(JSON.stringify(op.data.letter), /\$1,?249/, 'the operator\'s own letter lost its figure');

    const st = await call(base, 'GET', '/api/letter/latest', { token: STRANGER_TOKEN });
    assert.equal(st.status, 200);
    assert.equal(st.data.public, true);
    assert.equal(st.data.letter.week_key, week.key);
    assert.doesNotMatch(JSON.stringify(st.data.letter), /\$\s?[-+]?\d/,
      'a free signup read the operator\'s dollar figures in the weekly letter');

    const byKey = await call(base, 'GET', `/api/letter/${week.key}`, { token: STRANGER_TOKEN });
    assert.equal(byKey.status, 200);
    assert.equal(byKey.data.public, true);
    assert.doesNotMatch(JSON.stringify(byKey.data.letter), /\$\s?[-+]?\d/);

    const missing = await call(base, 'GET', '/api/letter/1999-W01', { token: STRANGER_TOKEN });
    assert.equal(missing.status, 404, 'a week nobody stored is not composed for a stranger either');
  } finally { server.close(); }
});

test('the chat card answers everyone with the public letter', async () => {
  await plantOperatorWeek();
  const { letterChatCard } = require('../lib/letter');
  const card = await letterChatCard();
  assert.match(card.reply_html, /The Agent Letter/);
  assert.doesNotMatch(card.reply_html, /\$\s?[-+]?\d/,
    'the chat card (web chat and Telegram /letter) served the operator\'s dollars');
});

test('the operator check: the plan from the database, and a failed read is not the operator', async () => {
  const { isOperator } = require('../lib/operator_view');
  assert.equal(await isOperator({ user: { user_id: OPERATOR } }), true);
  assert.equal(await isOperator({ user: { user_id: STRANGER } }), false);
  assert.equal(await isOperator({ user: { user_id: 424242 } }), false, 'no row is not the operator');
  assert.equal(await isOperator({}), false);
  assert.equal(await isOperator({ user: { user_id: OPERATOR, plan: 'admin' } }), true);
  // The JWT's own claims are never read: a token claiming admin is still a
  // stranger's token.
  assert.equal(await isOperator({ user: { user_id: STRANGER, plan: 'admin' } }), false);
  const real = pool.execute;
  pool.execute = async () => { throw new Error('db down'); };
  try {
    assert.equal(await isOperator({ user: { user_id: OPERATOR } }), false);
  } finally { pool.execute = real; }
});

test('the scrubbers open only on the check\'s own answer, never on a request object', () => {
  const { scanFor, summaryFor } = require('../routes/sync');
  const req = { user: { user_id: OPERATOR } };
  assert.equal(scanFor(req, { circuit_breaker: { equity: 1 } }).circuit_breaker.equity, undefined);
  assert.equal(summaryFor(req, { equity: 1 }).equity, undefined);
});
