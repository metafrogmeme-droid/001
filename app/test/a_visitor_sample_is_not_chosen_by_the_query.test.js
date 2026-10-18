'use strict';
/**
 * The visitor's sample of live calls is chosen from the newest calls overall,
 * never from the rows a query returned.
 *
 * A visitor sees the newest TEASER_COUNT live calls in full
 * (lib/signal_access.js). Picked from the filtered rows, `?symbol=X` would
 * put X's live call at the head of its own list and open it: any live call
 * could be read by naming its market. The route reads the sample with no
 * filter applied; this drives it against a pool that applies the filter (the
 * shared in-memory database does not), so the difference is visible.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const A = require('../lib/signal_access');

const now = Date.now();
const row = (key, sym, ageS, extra = {}) => ({
  signal_key: key, symbol: sym, direction: 'LONG', confidence: 0.6, score: 0.6,
  entry_price: 10 + ageS, stop_loss: 9, take_profit: 12, rr: 2, net_rr: null, rr_fee_losing: null,
  pattern: '', regime: '', thesis: '', status: 'NEW', pnl: null,
  created_at: new Date(now - ageS * 1000).toISOString(), resolved_at: null, seal: null,
  expires_at: new Date(now + 3600e3).toISOString(), ...extra,
});
const ROWS = [
  row('k1', 'AAA/USDT', 1), row('k2', 'BBB/USDT', 2),
  row('k3', 'CCC/USDT', 3, { status: 'TARGET', pnl: 1.5 }),   // resolved: not in the sample
  row('k4', 'DDD/USDT', 4), row('k5', 'EEE/USDT', 5),
];

// A pool that applies `symbol = ?` and the LIMIT, newest first.
const dbPath = require.resolve('../db');
require.cache[dbPath] = { id: dbPath, filename: dbPath, loaded: true, exports: { pool: {
  async execute(sql, params = []) {
    let rows = [...ROWS].sort((a, b) => b.created_at.localeCompare(a.created_at));
    if (/symbol = \?/.test(sql)) rows = rows.filter((r) => r.symbol === params[0]);
    const lim = /LIMIT (\d+)/.exec(sql);
    if (lim) rows = rows.slice(0, Number(lim[1]));
    return [rows];
  },
} } };

let server, base;
test.before(async () => {
  const app = express();
  app.use('/api/signals', require('../routes/signals'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => server && server.close());

const get = (p) => new Promise((resolve, reject) => {
  http.get(base + p, (res) => {
    let d = ''; res.on('data', (c) => { d += c; });
    res.on('end', () => resolve(JSON.parse(d)));
  }).on('error', reject);
});

test('the sample is the newest live calls: resolved calls are skipped, not counted', () => {
  assert.deepEqual([...A.teaserKeys(ROWS)], ['k1', 'k2', 'k4']);
  assert.equal(A.teaserKeys([]).size, 0);
  assert.equal(A.teaserKeys(null).size, 0);
});

test('unfiltered: the three newest live calls are open and the fourth is locked', async () => {
  const d = await get('/api/signals?limit=50');
  const by = Object.fromEntries(d.signals.map((r) => [r.signal_key, r]));
  for (const k of ['k1', 'k2', 'k4']) assert.equal(by[k].teaser, true, k);
  assert.equal(by.k5.locked, true);
  assert.equal(by.k3.teaser, undefined, 'a resolved call is public, not a sample');
});

test('naming the fourth call\'s market does not open it', async () => {
  const d = await get('/api/signals?symbol=' + encodeURIComponent('EEE/USDT'));
  assert.equal(d.signals.length, 1, 'the pool applied the filter');
  assert.equal(d.signals[0].signal_key, 'k5');
  assert.equal(d.signals[0].locked, true, 'the filter chose the sample');
  assert.equal(d.signals[0].entry_price, undefined);
});

test('naming a sampled call\'s market shows it, as the unfiltered list does', async () => {
  const d = await get('/api/signals?symbol=' + encodeURIComponent('BBB/USDT'));
  assert.equal(d.signals[0].teaser, true);
});
