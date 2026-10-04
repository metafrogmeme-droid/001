'use strict';

process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');
const http = require('node:http');
const { pool } = require('../db');

let server;
let base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/signals', require('../routes/signals'));
  await new Promise((resolve) => { server = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

function request(method, path, body) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const headers = { 'X-Bot-Secret': process.env.BOT_SYNC_SECRET };
    if (payload) headers['Content-Type'] = 'application/json';
    const req = http.request(base + path, { method, headers }, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(data) }));
    });
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function missingColumn() {
  const err = new Error('unknown setup column');
  err.code = 'ER_BAD_FIELD_ERROR';
  err.errno = 1054;
  return err;
}

test('signal sync preserves the stream while optional setup columns roll out', async () => {
  const original = pool.execute.bind(pool);
  let rejected = false;
  pool.execute = async (sql, params) => {
    if (!rejected && /INSERT INTO signals/i.test(sql) && /signal_type/i.test(sql)) {
      rejected = true;
      throw missingColumn();
    }
    return original(sql, params);
  };
  try {
    const response = await request('POST', '/api/bot/sync/signals', { signals: [{
      signal_key: 'schema-rollout-sync', symbol: 'BTC/USDT', direction: 'LONG',
      confidence: 0.7, entry_price: 100, stop_loss: 95, take_profit: 110,
      status: 'NEW', created_at: new Date().toISOString(),
      signal_type: 'breakout', timeframe: '1h', source: 'rules',
    }] });
    assert.equal(response.status, 200);
    assert.equal(response.body.upserted, 1);
    assert.ok(pool.signals.some(row => row.signal_key === 'schema-rollout-sync'));
  } finally {
    pool.execute = original;
  }
});

test('analytics preserves established groups while optional setup columns roll out', async () => {
  const original = pool.execute.bind(pool);
  let rejected = false;
  pool.execute = async (sql, params) => {
    if (!rejected && /SELECT[\s\S]*signal_type[\s\S]*FROM signals/i.test(sql)) {
      rejected = true;
      throw missingColumn();
    }
    return original(sql, params);
  };
  try {
    const response = await request('GET', '/api/signals/analytics');
    assert.equal(response.status, 200);
    assert.ok(response.body.overall);
    assert.deepEqual(response.body.by_setup, []);
  } finally {
    pool.execute = original;
  }
});
