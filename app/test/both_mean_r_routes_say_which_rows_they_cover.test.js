'use strict';
/**
 * Two public routes publish a mean R over resolved signals under one
 * `r_basis`: /stats (`avg_r`, SQL over every resolved row) and /analytics
 * (`overall.mean_r`, in-process over the newest 2000). #510 named the second
 * "the same way /stats rounds avg_r". Past 2000 resolved signals they answer
 * differently about one quantity, and neither said which rows it covered.
 * Each now carries a `coverage` block, as the public track record does.
 *
 * Driven through the real route over the real in-memory database.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const express = require('express');

const { pool } = require('../db');
const { dollarKeys } = require('../lib/public_signal');

let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/signals', require('../routes/signals'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

const get = (p) => new Promise((resolve, reject) => {
  http.get(base + p, (res) => {
    let b = ''; res.on('data', (d) => { b += d; });
    res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(b || '{}') }));
  }).on('error', reject);
});

const T0 = Date.UTC(2026, 6, 1);
async function plant(from, n, pnl) {
  for (let i = from; i < from + n; i++) {
    const at = new Date(T0 + i * 60000).toISOString();
    await pool.execute(
      `INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern,
         regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
         created_at, resolved_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
      [`w${i}`, 'BTC/USDT', 'LONG', 0.7, 0.7, 'breakout', 'TREND_UP', 100, 95, 110, 2, '',
        pnl > 0 ? 'TARGET' : 'STOP', pnl, at, at, at]);
  }
}

test('under the window both routes cover every resolved signal, and say so', async () => {
  await plant(0, 30, 0.5);
  const stats = (await get('/api/signals/stats')).body;
  const an = (await get('/api/signals/analytics')).body;
  assert.deepEqual(stats.coverage, { basis: 'all_resolved', rows: 30 });
  assert.equal(an.coverage.basis, 'newest_resolved');
  assert.equal(an.coverage.rows, 30);
  assert.equal(an.coverage.resolved_total, 30);
  assert.equal(an.coverage.complete, true);
  assert.equal(an.coverage.note, 'Covers every resolved signal (30).');
  assert.equal(stats.avg_r, an.overall.mean_r);
});

test('past the window the two figures differ, and the analytics says it covers the newest 2000', async () => {
  // 30 planted above at +0.5; 470 more older-looking losses, then 2000 wins.
  await plant(30, 470, -0.4);
  await plant(500, 2000, 0.3);
  const stats = (await get('/api/signals/stats')).body;
  const an = (await get('/api/signals/analytics')).body;
  assert.equal(stats.coverage.rows, 2500);
  assert.equal(an.coverage.rows, 2000);
  assert.equal(an.coverage.window, 2000);
  assert.equal(an.coverage.resolved_total, 2500);
  assert.equal(an.coverage.complete, false);
  assert.equal(an.coverage.note,
    'Covers the newest 2000 of 2500 resolved signals. /api/signals/stats covers all 2500.');
  assert.notEqual(stats.avg_r, an.overall.mean_r, 'two windows, two answers, each labelled');
  assert.equal(an.overall.mean_r, 0.3);
  assert.deepEqual(dollarKeys(an), []);
});

test('a total that cannot be counted is not "complete"', async () => {
  const real = pool.execute.bind(pool);
  pool.execute = async (sql, params) => {
    if (/^SELECT COUNT\(\*\) AS resolved FROM signals WHERE pnl IS NOT NULL$/.test(String(sql).trim())) {
      throw Object.assign(new Error('lock wait'), { name: 'Error', code: 'ER_LOCK_WAIT_TIMEOUT' });
    }
    return real(sql, params);
  };
  try {
    const an = (await get('/api/signals/analytics')).body;
    assert.equal(an.coverage.resolved_total, null);
    assert.equal(an.coverage.complete, null);
    assert.match(an.coverage.note, /could not be read/);
    assert.ok(an.overall, 'the groups still answer');
  } finally {
    pool.execute = real;
  }
});

test('a count that answers with no readable number is not a total of zero', async () => {
  // The query succeeds and the value is not a count: no column, a NULL, a
  // string. Read as 0, every window would be "complete".
  const real = pool.execute.bind(pool);
  for (const row of [{}, { resolved: null }, { resolved: 'many' }, { resolved: -3 }]) {
    pool.execute = async (sql, params) => {
      if (/^SELECT COUNT\(\*\) AS resolved FROM signals WHERE pnl IS NOT NULL$/.test(String(sql).trim())) {
        return [[row], []];
      }
      return real(sql, params);
    };
    try {
      const an = (await get('/api/signals/analytics')).body;
      assert.equal(an.coverage.resolved_total, null, JSON.stringify(row));
      assert.equal(an.coverage.complete, null, JSON.stringify(row));
      assert.match(an.coverage.note, /could not be read/);
    } finally {
      pool.execute = real;
    }
  }
});
