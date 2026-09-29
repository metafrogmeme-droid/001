'use strict';
/**
 * The candle proxy's cache key names every parameter the fetch sends.
 *
 * `routes/market.js` keyed the 15s candle cache on symbol, granularity and the
 * replay window, and not on `limit`. The Markets chart polls the live candle
 * with limit=2 every 15s and redraws with limit=200 every 20s, so whichever
 * request filled the entry first answered the other: driven, a limit=2 then a
 * limit=200 request both came back with 2 rows, and the chart collapsed to two
 * candles about 20s after load.
 */
process.env.JWT_SECRET = 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { EventEmitter } = require('node:events');
const https = require('node:https');

// Stub https.get BEFORE the router is required. The body carries as many rows
// as the upstream URL asked for, so a response shows which fetch it came from.
const upstream = [];
https.get = (url, _opts, cb) => {
  const u = new URL(String(url));
  upstream.push(u);
  const n = Number(u.searchParams.get('limit'));
  const rows = Array.from({ length: n }, (_, i) => [String(i), '1', '2', '0.5', '1.5', '10']);
  const res = new EventEmitter();
  const req = new EventEmitter();
  setImmediate(() => {
    cb(res);
    res.emit('data', JSON.stringify({ code: '00000', data: rows }));
    res.emit('end');
  });
  req.destroy = () => {};
  return req;
};

const express = require('express');
const { _clearCache } = require('../lib/http_cache');
let server, base;

function get(path) {
  return new Promise((resolve, reject) => {
    http.get(`${base}${path}`, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(d) }));
    }).on('error', reject);
  });
}

function rowsOf(body) {
  // relayBitget answers the rows as the upstream sent them, under `data`.
  return Array.isArray(body) ? body : body.data;
}

test.before(async () => {
  const app = express();
  app.use('/api/market', require('../routes/market'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test.beforeEach(() => { _clearCache(); upstream.length = 0; });

test('a 2-bar poll does not answer a 200-bar redraw inside the TTL', async () => {
  const small = await get('/api/market/candles/BTCUSDT?granularity=1h&limit=2');
  assert.equal(small.status, 200);
  assert.equal(rowsOf(small.body).length, 2);

  const big = await get('/api/market/candles/BTCUSDT?granularity=1h&limit=200');
  assert.equal(big.status, 200);
  assert.equal(rowsOf(big.body).length, 200,
    'the 200-bar request was answered from the 2-bar cache entry');
  assert.equal(upstream.length, 2, 'each limit is its own upstream read');
  assert.deepEqual(upstream.map(u => u.searchParams.get('limit')), ['2', '200']);
});

test('the other order too: a 200-bar entry does not answer a 2-bar poll', async () => {
  await get('/api/market/candles/ETHUSDT?granularity=1h&limit=200');
  const small = await get('/api/market/candles/ETHUSDT?granularity=1h&limit=2');
  assert.equal(rowsOf(small.body).length, 2);
});

test('the same request inside the TTL is still served from the cache', async () => {
  await get('/api/market/candles/SOLUSDT?granularity=1h&limit=200');
  const again = await get('/api/market/candles/SOLUSDT?granularity=1h&limit=200');
  assert.equal(rowsOf(again.body).length, 200);
  assert.equal(upstream.length, 1, 'an identical request must not refetch');
});

test('a limit spelled differently but clamped to the same value shares one entry', async () => {
  // The route clamps to 200, so 500 and 200 send the same upstream request.
  await get('/api/market/candles/XRPUSDT?granularity=1h&limit=500');
  await get('/api/market/candles/XRPUSDT?granularity=1h&limit=200');
  assert.equal(upstream.length, 1);
  assert.equal(upstream[0].searchParams.get('limit'), '200');
});

test('every negative limit is one clamped request, not a key and a fetch each', async () => {
  // Clamped only from above, `-1`, `-2`, `-3` were three cache entries and
  // three upstream reads, in a cache that never evicts.
  for (const n of ['-1', '-2', '-3', '-999999']) {
    await get(`/api/market/candles/ADAUSDT?granularity=1h&limit=${n}`);
  }
  assert.equal(upstream.length, 1, 'each negative limit fetched on its own');
  assert.equal(upstream[0].searchParams.get('limit'), '1');
});
