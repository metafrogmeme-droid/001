'use strict';
/**
 * A crowd that misses a cache together fetches once.
 *
 * `lib/http_cache.cached` said so in its own comment: "this does NOT dedupe
 * concurrent misses: two callers that miss together both fetch." So a crowd
 * opening a page at one moment sent the venue one request per person for one
 * snapshot. And the strategy board's "Scan now" route cached a finished board
 * but not one in flight, so a crowd pressing it together sent one bot call
 * each. Both now share a fetch in flight, and neither keeps a failure.
 *
 * Driven through the real modules: the cache with a counting fetcher, and the
 * route through express with the bot gateway stubbed.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');

const httpCache = require('../lib/http_cache');

test.beforeEach(() => { httpCache._clearCache(); });

test('callers who miss together share one fetch', async () => {
  let calls = 0;
  const read = httpCache.cached('crowd', 60_000, async () => {
    calls += 1;
    await new Promise((r) => setTimeout(r, 20));
    return { n: calls };
  });
  const out = await Promise.all(Array.from({ length: 20 }, () => read()));
  assert.equal(calls, 1);
  assert.ok(out.every((o) => o === out[0]));
  await read();
  assert.equal(calls, 1, 'a finished fetch inside its TTL was fetched again');
});

test('a fetch that failed reaches every waiter and is not kept', async () => {
  let calls = 0;
  const read = httpCache.cached('down', 60_000, async () => {
    calls += 1;
    await new Promise((r) => setTimeout(r, 10));
    throw new Error('Timeout');
  });
  const out = await Promise.allSettled(Array.from({ length: 5 }, () => read()));
  assert.ok(out.every((o) => o.status === 'rejected'));
  assert.equal(calls, 1);
  await assert.rejects(read());
  assert.equal(calls, 2, 'a failure was served to the next caller');
});

// ── the strategy board route ─────────────────────────────────────────────

let gatewayCalls = 0;
let gatewayStatus = 200;
const gatewayPath = require.resolve(path.join(__dirname, '..', 'lib', 'gateway'));
require.cache[gatewayPath] = {
  id: gatewayPath, filename: gatewayPath, loaded: true,
  exports: {
    isConfigured: () => true,
    relay: (res, r) => res.status(r.status >= 500 ? 502 : r.status).json(r.data),
    getGateway: async () => {
      gatewayCalls += 1;
      await new Promise((r) => setTimeout(r, 30));
      return gatewayStatus === 200
        ? { status: 200, data: { id: 'limit-entry', rows: [] } }
        : { status: 503, data: { error: 'starting' } };
    },
    postGateway: async () => ({ status: 200, data: {} }),
  },
};

let server;
let base;
test.before(async () => {
  const express = require('express');
  const app = express();
  app.use('/api/public/strategies', require('../routes/public_strategies'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

function get(p, ip) {
  return new Promise((resolve, reject) => {
    http.get(`${base}${p}`, { headers: { 'X-Forwarded-For': ip } }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, body: d }));
    }).on('error', reject);
  });
}

test('a crowd pressing "Scan now" together makes one bot call', async () => {
  gatewayCalls = 0;
  gatewayStatus = 200;
  // Four presses: the route allows six a minute from one address, and every
  // call in this file comes from the same one.
  const out = await Promise.all(Array.from({ length: 4 }, (_, i) =>
    get('/api/public/strategies/scan?id=limit-entry', `10.0.0.${i + 1}`)));
  assert.ok(out.every((o) => o.status === 200), JSON.stringify(out.map((o) => o.status)));
  assert.equal(gatewayCalls, 1);
});

test('a failed bot call is not kept: the next press asks again', async () => {
  gatewayCalls = 0;
  gatewayStatus = 503;
  const a = await get('/api/public/strategies/scan?id=multi-trend', '10.0.1.1');
  const b = await get('/api/public/strategies/scan?id=multi-trend', '10.0.1.2');
  assert.ok(a.status !== 429 && b.status !== 429, 'the limiter, not the cache, answered');
  assert.equal(gatewayCalls, 2);
});
