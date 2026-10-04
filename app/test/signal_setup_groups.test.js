'use strict';
/**
 * The setup scoreboard groups resolved signals by the five dimensions the
 * row recorded: signal_type (published as setup), regime, timeframe, source,
 * direction.
 *
 * A missing dimension is not a cell. An unreadable R is not in the count or
 * the sum. The dimensions sit beside the seal — a re-sync can move the
 * outcome and cannot move the cell — and the analytics SELECT has to name
 * them, because MySQL returns only the columns it was asked for.
 */
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const express = require('express');

const { pool } = require('../db');
const { codeOnly } = require('./helpers/code_only');
const { dollarKeys } = require('../lib/public_signal');

let server, base;
const BOT = { 'X-Bot-Secret': process.env.BOT_SYNC_SECRET };

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/signals', require('../routes/signals'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

function request(method, p, body) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const headers = { ...BOT };
    if (payload) headers['Content-Type'] = 'application/json';
    const r = http.request(base + p, { method, headers }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({
        status: res.statusCode,
        data: d ? JSON.parse(d) : {},
      }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

const row = (k, extra = {}) => ({
  signal_key: k, symbol: 'BTC/USDT', direction: 'LONG', confidence: 0.8, score: 0.8,
  pattern: 'breakout', regime: 'TREND', signal_type: 'vwap_reversion',
  timeframe: '1h', source: 'rules',
  entry_price: 100, stop_loss: 95, take_profit: 110,
  rr: 2, thesis: 't', status: 'RESOLVED', pnl: 1,
  created_at: new Date().toISOString(),
  resolved_at: new Date().toISOString(),
  ...extra,
});

const stored = (k) => pool.signals.find((s) => s.signal_key === k);

test('the analytics query names every setup dimension', () => {
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'routes', 'signals.js'), 'utf8'));
  const a = src.indexOf("router.get('/analytics'");
  const b = src.indexOf('signal_analytics_unavailable', a);
  const q = src.slice(a, b);
  for (const col of ['signal_type', 'regime', 'timeframe', 'source', 'direction', 'pnl']) {
    assert.ok(q.includes(col), `${col} is not in the analytics SELECT`);
  }
});

test('a resolved book groups by the five dimensions, and a gap is not a cell', async () => {
  const posted = await request('POST', '/api/bot/sync/signals', { signals: [
    row('cell-a1', { pnl: 2 }),
    row('cell-a2', { pnl: -1 }),
    row('cell-b', {
      pnl: 0, signal_type: 'sweep', regime: 'RANGE', timeframe: '4h', source: 'llm',
      direction: 'SHORT',
    }),
    // 4R with no timeframe. Counting it in the vwap cell would publish net 5.
    row('cell-gap', { pnl: 4, timeframe: null }),
    // Unreadable R. Number('nope') is NaN; that must not become a 0R.
    row('cell-bad', { pnl: 'nope' }),
  ] });
  assert.equal(posted.status, 200, JSON.stringify(posted.data));

  assert.equal(stored('cell-a1').signal_type, 'vwap_reversion');
  assert.equal(stored('cell-a1').timeframe, '1h');
  assert.equal(stored('cell-a1').source, 'rules');
  assert.equal(stored('cell-gap').timeframe, null);
  assert.equal(stored('cell-gap').signal_type, 'vwap_reversion');
  assert.ok(!String(stored('cell-a1').seal_payload).includes('signal_type'),
    'the setup dimension was sealed');
  assert.ok(!String(stored('cell-a1').seal_payload).includes('vwap_reversion'));

  // A resolution re-sync may change pnl and must not move the cell.
  const again = await request('POST', '/api/bot/sync/signals', { signals: [
    row('cell-a1', { pnl: 2, signal_type: 'momentum_confluence', timeframe: '1d', source: 'other' }),
  ] });
  assert.equal(again.status, 200);
  assert.equal(stored('cell-a1').signal_type, 'vwap_reversion');
  assert.equal(stored('cell-a1').timeframe, '1h');
  assert.equal(stored('cell-a1').source, 'rules');
  assert.equal(stored('cell-a1').pnl, 2);

  const res = await request('GET', '/api/signals/analytics');
  assert.equal(res.status, 200);
  const body = res.data;
  assert.deepEqual(dollarKeys(body), []);
  assert.equal(body.r_basis, 'gross');
  // 2, -1, 0, 4. The word is not a fifth row and not a 0R.
  assert.equal(body.overall.resolved, 4);
  assert.equal(body.overall.net_r, 5);
  assert.equal(body.overall.mean_r, 1.25);
  assert.equal(body.by_setup.length, 2);

  const vwap = body.by_setup.find((g) => g.setup === 'vwap_reversion');
  const sweep = body.by_setup.find((g) => g.setup === 'sweep');
  assert.ok(vwap && sweep);
  assert.deepEqual(
    [vwap.regime, vwap.timeframe, vwap.source, vwap.direction],
    ['TREND', '1h', 'rules', 'LONG']);
  assert.equal(vwap.n, 2);
  assert.equal(vwap.net_r, 1);
  assert.equal(vwap.mean_r, 0.5);
  assert.deepEqual(
    [sweep.regime, sweep.timeframe, sweep.source, sweep.direction],
    ['RANGE', '4h', 'llm', 'SHORT']);
  assert.equal(sweep.n, 1);
  assert.equal(sweep.net_r, 0);
  assert.equal(sweep.mean_r, 0);
  assert.ok(!body.by_setup.some((g) => g.timeframe == null || g.setup == null || g.setup === '(none)'));
});
