'use strict';
/**
 * A bot push that NAMES another account is refused, never applied to the
 * agent's record.
 *
 * `POST /api/bot/sync` and `/trade-event` write the OPERATOR's rows -- the
 * agent's record, which the public track record and the portfolio summary
 * publish. The id was "server-enforced" by IGNORING the one a payload carried,
 * so a push that was about somebody else landed on the agent's rows anyway.
 * Driven: the bot's /link sent `{user_id: 77, equity: 10000, positions: [],
 * closed_trades: []}` for a freshly linked website user, and this route ran
 * `DELETE FROM trades WHERE user_id = 1`, `DELETE FROM equity_snapshots WHERE
 * user_id = 1` and inserted a $10,000 snapshot as the agent's equity.
 *
 * The contract, each arm driven: no id is the agent's record (what the bot
 * sends now); the operator's own id, as a number or a numeric string, is the
 * agent's record (what older bots sent); anything else is a 409 and NO row is
 * touched.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');
const { pool } = require('../db');

const SECRET = process.env.BOT_SYNC_SECRET;
const OPERATOR = parseInt(process.env.BOT_USER_ID) || 1;
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

function post(path, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const r = http.request(`${base}/api/bot/sync${path}`, {
      method: 'POST',
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

const close = (symbol, pnl) => ({
  symbol, direction: 'LONG', entry_price: 100, exit_price: 110, size_usd: 500,
  pnl, fees: 1, opened_at: '2026-09-01T00:00:00Z', closed_at: '2026-09-01T01:00:00Z',
});

const agentRows = () => pool.trades.filter((t) => t.user_id === OPERATOR);
const agentSnaps = () => pool.snapshots.filter((s) => s.user_id === OPERATOR);

async function seedAgentRecord() {
  const r = await post('', { equity: 250, positions: [],
    closed_trades: [close('BTC/USDT', 5), close('ETH/USDT', -2), close('SOL/USDT', 7)] });
  assert.equal(r.status, 200);
  assert.equal(agentRows().length, 3);
}

function quietly(fn) {
  const warn = console.warn;
  const said = [];
  console.warn = (...a) => { said.push(a.join(' ')); };
  return fn().finally(() => { console.warn = warn; }).then((v) => [v, said]);
}

test('a push with no account named is the agent record, and replaces it', async () => {
  await seedAgentRecord();
  const r = await post('', { equity: 300, positions: [], closed_trades: [close('BTC/USDT', 5)] });
  assert.equal(r.status, 200);
  assert.equal(r.body.ok, true);
  assert.equal(agentRows().length, 1);
  assert.equal(Number(agentSnaps().at(-1).equity), 300);
});

for (const named of [OPERATOR, String(OPERATOR)]) {
  test(`a push naming the operator (${JSON.stringify(named)}) is the agent record`, async () => {
    await seedAgentRecord();
    const r = await post('', { user_id: named, equity: 320, positions: [],
      closed_trades: [close('ETH/USDT', 4), close('SOL/USDT', 1)] });
    assert.equal(r.status, 200);
    assert.equal(agentRows().length, 2);
    assert.equal(Number(agentSnaps().at(-1).equity), 320);
  });
}

// The /link push that wiped the record, and the junk ids that must not read
// as the operator: `true` is `1` to Number(), and so is `[1]`.
for (const named of [77, '77', OPERATOR + 1, true, [OPERATOR], { id: OPERATOR }, '']) {
  test(`a push naming ${JSON.stringify(named)} is refused and touches no row`, async () => {
    await seedAgentRecord();
    const snapsBefore = agentSnaps().length;
    const lastBefore = agentSnaps().at(-1);
    const [r, said] = await quietly(() => post('', {
      user_id: named, equity: 10000, positions: [], closed_trades: [] }));
    assert.equal(r.status, 409);
    assert.deepEqual(r.body, { ok: false, error: 'not_the_agent_record' });
    assert.equal(agentRows().length, 3, 'the agent record must survive a refused push');
    assert.equal(agentSnaps().length, snapsBefore);
    assert.equal(agentSnaps().at(-1), lastBefore);
    assert.equal(said.length, 1);
    assert.match(said[0], /Nothing was written/);
  });
}

test('the refusal log quotes the named id and bounds it', async () => {
  const [r, said] = await quietly(() => post('', {
    user_id: '9'.repeat(200) + '\n<forged>', equity: 1, positions: [], closed_trades: [] }));
  assert.equal(r.status, 409);
  assert.ok(!said[0].includes('\n'), 'a newline in the id must not split the log line');
  assert.ok(!said[0].includes('<forged>'), 'the id is truncated before it is logged');
  assert.ok(said[0].includes('"' + '9'.repeat(40) + '"'));
});

test('a trade event naming another account is refused and opens nothing', async () => {
  const before = pool.trades.length;
  const [r] = await quietly(() => post('/trade-event', {
    user_id: 77, event: 'open', event_id: `ev-refused-${++n}`, equity: 10000,
    trade: { symbol: 'REFUSED/USDT', direction: 'LONG', entry_price: 1, size_usd: 100, fees: 0 } }));
  assert.equal(r.status, 409);
  assert.equal(r.body.error, 'not_the_agent_record');
  assert.equal(pool.trades.length, before);
  assert.ok(!pool.trades.some((t) => t.symbol === 'REFUSED/USDT'));
});

test('a trade event with no account named is the agent record', async () => {
  const r = await post('/trade-event', {
    event: 'open', event_id: `ev-agent-${++n}`, equity: 1000,
    trade: { symbol: 'AGENT/USDT', direction: 'LONG', entry_price: 1, size_usd: 100, fees: 0 } });
  assert.equal(r.status, 200);
  assert.ok(agentRows().some((t) => t.symbol === 'AGENT/USDT' && t.status === 'OPEN'));
});
