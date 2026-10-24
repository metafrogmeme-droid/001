'use strict';
/**
 * Time-to-cash: the flag, the operator page, and a coming-soon card
 * with no date and no public admin link.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);
delete process.env.DATABASE_URL;
delete process.env.T2C_ENABLED;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const bridge = require('../lib/t2c_bridge');
const realLoad = bridge.loadReading;
const { flag_on, pageGate, sendT2C } = require('../lib/t2c_flag');
const catalog = require('../lib/tools_catalog');

const OPERATOR = 4242;
const STRANGER = 4243;
pool.users.push({ id: OPERATOR, email: 't2c-op@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: STRANGER, email: 't2c-free@example.com', plan: 'free', token_epoch: 0 });
const OP = jwt.sign({ user_id: OPERATOR, email: 't2c-op@example.com', epoch: 0 }, process.env.JWT_SECRET);
const FREE = jwt.sign({ user_id: STRANGER, email: 't2c-free@example.com', epoch: 0 }, process.env.JWT_SECRET);

let calls = 0;
bridge.loadReading = async () => {
  calls += 1;
  return {
    public: {
      kind: 't2c_cap_signal',
      liquidation_price: false,
      flag_on: false,
      price: { p: null, y_stake: '0', y_stake_source: 'lido_rule', t_p95_days: null, r_borrow: null },
      backtest: { verdict: 'not_established', reason: 'not read', widest_steth_discount_bp: '39.12', widest_day: '2025-07-21', weeth_note: '' },
      duration: { p50_days: null, p95_days: null },
      etherfi: { as_of: '2026-10-23', source: 'recorded_snapshot', eth: { instant_eth: '0', instant_source: 'below_floor', pool_eth: '2456', floor_eth: '22647' }, steth: { limit_eth: '5000', issuer_fee_bps: '10' } },
      observation: { queue_eth: '12950', quoted_wait: '1 day 7 hours', sweep_days: '7.1', sweep_source: 'observation', scenario: { sum_hours: '32.7', matches_quoted_wait: false } },
      cap: { ratio: null, applied: false },
    },
    admin: {
      kind: 't2c_cap_signal',
      liquidation_price: false,
      research: { as_of: '2026-10-23', supplied_usd: [{ asset: 'weETH', usd: '3640000000' }] },
      price: { p: null },
    },
  };
};

const t2cRouter = require('../routes/t2c');
const toolsRouter = require('../routes/tools');

function app() {
  const server = express();
  server.use(express.json());
  server.use(pageGate);
  server.use('/api/t2c', t2cRouter);
  server.use('/api/tools', toolsRouter);
  server.get('/t2c', (req, res) => sendT2C(req, res));
  return server;
}

function call(base, method, p, token) {
  return new Promise((resolve, reject) => {
    const headers = token ? { Authorization: 'Bearer ' + token } : {};
    const req = http.request(base + p, { method, headers }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => {
        let json = null;
        try { json = d ? JSON.parse(d) : null; } catch (err) { json = null; }
        resolve({ status: res.statusCode, text: d, json });
      });
    });
    req.on('error', reject);
    req.end();
  });
}

async function withServer(fn) {
  const expressApp = app();
  const server = await new Promise((resolve) => {
    const httpServer = expressApp.listen(0, '127.0.0.1', () => resolve(httpServer));
  });
  const base = 'http://127.0.0.1:' + server.address().port;
  try {
    return await fn(base);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

test('the flag is off for yes and on for true', () => {
  assert.equal(flag_on({ T2C_ENABLED: 'yes' }), false);
  assert.equal(flag_on({ T2C_ENABLED: 'true' }), true);
  assert.equal(flag_on({}), false);
});

test('flag off is 404 for the public and does not read the signal', async () => {
  const before = calls;
  delete process.env.T2C_ENABLED;
  await withServer(async (base) => {
    const anon = await call(base, 'GET', '/api/t2c/signal');
    assert.equal(anon.status, 404);
    const free = await call(base, 'GET', '/api/t2c/signal', FREE);
    assert.equal(free.status, 404);
    assert.equal(calls, before);
  });
});

test('flag on serves the public feed without the research note', async () => {
  process.env.T2C_ENABLED = '1';
  try {
    await withServer(async (base) => {
      const anon = await call(base, 'GET', '/api/t2c/signal');
      assert.equal(anon.status, 200);
      assert.equal(anon.json.price.p, null);
      assert.equal(anon.json.liquidation_price, false);
      assert.equal(anon.json.research, undefined);
      assert.equal(JSON.stringify(anon.json).includes('3640000000'), false);
      assert.equal(JSON.stringify(anon.json).includes('$'), false);
    });
  } finally {
    delete process.env.T2C_ENABLED;
  }
});

test('an operator can read the signal while the flag is off', async () => {
  delete process.env.T2C_ENABLED;
  await withServer(async (base) => {
    const op = await call(base, 'GET', '/api/t2c/signal', OP);
    assert.equal(op.status, 200);
    assert.equal(op.json.research.supplied_usd[0].usd, '3640000000');
  });
});

test('the page is 404 for a signed-in non-operator even with the flag on', async () => {
  process.env.T2C_ENABLED = '1';
  try {
    await withServer(async (base) => {
      const anon = await call(base, 'GET', '/t2c');
      assert.equal(anon.status, 404);
      const free = await call(base, 'GET', '/t2c', FREE);
      assert.equal(free.status, 404);
      const htmlFile = await call(base, 'GET', '/t2c.html');
      assert.equal(htmlFile.status, 404);
      const op = await call(base, 'GET', '/t2c', OP);
      assert.equal(op.status, 200);
      assert.equal(op.text.includes('not a liquidation price'), true);
      assert.equal(op.text.includes('0.00'), false);
    });
  } finally {
    delete process.env.T2C_ENABLED;
  }
});

test('the coming-soon card has no date and no public admin link', async () => {
  const file = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'data', 'tools.json'), 'utf8'));
  const card = file.find((row) => row && row.id === 't2c');
  assert.ok(card);
  assert.equal(card.status, 'runtime');
  assert.equal(Object.prototype.hasOwnProperty.call(card, 'date'), false);
  assert.equal(Object.prototype.hasOwnProperty.call(card, 'href'), false);
  assert.equal(card.route, null);
  assert.equal(JSON.stringify(file).includes('$'), false);
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'tools.html'), 'utf8');
  assert.equal(html.includes('/t2c'), false);
  for (const env of [{}, { T2C_ENABLED: '1' }, { T2C_ENABLED: 'true' }, { T2C_ENABLED: 'yes' }]) {
    assert.deepEqual(catalog.presentation(card, env), { label: 'Coming soon', href: null });
  }
  const rendered = catalog.renderFile({ T2C_ENABLED: '1' });
  const at = rendered.indexOf('data-tool="t2c"');
  const cardHtml = rendered.slice(at - 40, at + 400);
  assert.match(cardHtml, /<article /);
  assert.equal(cardHtml.includes('href='), false);
  assert.match(cardHtml, /Coming soon/);
  await withServer(async (base) => {
    const anon = await call(base, 'GET', '/api/tools');
    assert.equal(anon.status, 200);
    const pub = anon.json.tools.find((row) => row.id === 't2c');
    assert.equal(pub.href, undefined);
    assert.equal(Object.prototype.hasOwnProperty.call(pub, 'date'), false);
    const op = await call(base, 'GET', '/api/tools', OP);
    const admin = op.json.tools.find((row) => row.id === 't2c');
    assert.equal(admin.href, '/t2c');
  });
});

test('the python reading is the public feed and carries no research dollars', async () => {
  const reading = await realLoad();
  assert.equal(reading.public.backtest.verdict, 'not_established');
  assert.equal(reading.public.backtest.widest_day, '2025-07-21');
  assert.equal(reading.public.price.p, null);
  assert.equal(reading.public.research, undefined);
  assert.equal(JSON.stringify(reading.public).includes('3640000000'), false);
  assert.equal(reading.admin.research.as_of, '2026-10-23');
  assert.equal(reading.admin.fork_model.broadcast, false);
  assert.equal(reading.admin.fork_model.chain_id, 31337);
});
