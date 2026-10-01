'use strict';
/**
 * Smart venue router (PR QQ) — per-pair cheapest-venue funding reads.
 * Manual-first is the product line: recommendations only, no order routing.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const router = require('../lib/venue_router');
const { pool } = require('../db');

const FUNDING = {
  rows: [
    { base: 'BTC', rates: { bitget: 8.2, bybit: 10.9, bingx: -2.1 }, spread_apr: 13.0 },
    { base: 'ETH', rates: { bitget: 5.0 }, spread_apr: 0 },          // single venue
    { base: 'SOL', rates: { bitget: 'junk', bybit: 4.0 } },          // one bad rate
  ],
};
const DEXCMP = { rows: [{ base: 'BTC', dex_mid: 100050, cex_price: 100000, delta_bps: 5 }] };

// ── Pure scoring ─────────────────────────────────────────────────────────────

test('funding mechanics: lowest APR = cheapest long, highest = best paid short', () => {
  const rows = router.buildRouterTable(FUNDING.rows, DEXCMP.rows);
  const btc = rows.find(r => r.base === 'BTC');
  assert.equal(btc.long_venue, 'bingx');       // -2.1% — you are PAID to be long
  assert.equal(btc.long_apr, -2.1);
  assert.equal(btc.short_venue, 'bybit');      // 10.9% — best paid short
  assert.equal(btc.spread_apr, 13);
  assert.equal(btc.dex_basis_bps, 5);
});

test('single-venue and unusable rows never fabricate a routing choice', () => {
  const rows = router.buildRouterTable(FUNDING.rows, []);
  assert.ok(!rows.some(r => r.base === 'ETH'), 'one venue = nothing to route');
  const sol = rows.find(r => r.base === 'SOL');
  assert.ok(!sol, 'a junk rate leaves only one usable venue -> excluded');
});

test('payload carries the manual-first line and honest staleness', () => {
  const fresh = router.buildRouter(FUNDING, DEXCMP, 30 * 60 * 1000);
  assert.match(fresh.manual_first, /never auto-routes orders/);
  assert.equal(fresh.stale, false);
  assert.equal(fresh.report_age_minutes, 30);
  const old = router.buildRouter(FUNDING, DEXCMP, 4 * 3600 * 1000);
  assert.equal(old.stale, true);
  const unknown = router.buildRouter(null, null, null);
  assert.equal(unknown.rows.length, 0);
  assert.equal(unknown.stale, null);
});

test('the router module contains no order-placement machinery', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'venue_router.js'), 'utf8');
  for (const marker of ['placeOrder', 'createOrder', 'submitOrder', '/api/trade']) {
    assert.ok(!src.includes(marker), `routing must stay read-only: found "${marker}"`);
  }
});

// ── Chat + route (seeded reports_cache) ──────────────────────────────────────

let server, base;

test.before(async () => {
  await pool.execute('REPLACE INTO reports_cache (id, reports_json) VALUES (1, ?)',
    [JSON.stringify({ funding: FUNDING, received_at: new Date().toISOString() })]);
  require('../lib/dex').setTickerFetcher(async () => ({ BTCUSDT: { price: 100000, change: 0, volume: 1e9 } }));
  require('../lib/dex').setMidsFetcher(async () => ({ BTC: '100050' }));
  const app = express();
  app.use(express.json());
  app.use('/api/auth', require('../auth').router);
  app.use('/api/market', require('../routes/market'));
  app.use('/api/chat', require('../routes/chat'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => {
  if (server) server.close();
  require('../lib/dex').setTickerFetcher(null);
  require('../lib/dex').setMidsFetcher(null);
});

test('GET /api/market/venue-router serves the table from the cached scan', async () => {
  const r = await new Promise((resolve, reject) => {
    const q = http.request(`${base}/api/market/venue-router`, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: JSON.parse(d) }));
    });
    q.on('error', reject);
    q.end();
  });
  assert.equal(r.status, 200);
  assert.equal(r.data.rows[0].base, 'BTC');
  assert.equal(r.data.rows[0].long_venue, 'bingx');
});

test('the card names the cheapest long and the manual-first line, and this module no longer matches the sentence', async () => {
  const card = await router.venueRouterChatCard('BTC');
  assert.equal(card.intent, 'venue_router');
  assert.match(card.reply_html, /long on <b>bingx<\/b>/);
  assert.match(card.reply_html, /never auto-routes orders/);
  assert.equal(typeof router.maybeHandleVenueRouterChat, 'undefined');
  assert.equal(router.CHAT_RE, undefined);
});

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

test('chat: "best venue for BTC" waits for the bot; the market route still serves', async () => {
  const reg = await req('POST', '/api/auth/register', {
    body: { email: 'venue-chat@test.io', password: 'x'.repeat(12) },
  });
  const token = reg.data.token;
  for (const text of ['best venue for BTC', 'venue router', 'cheapest exchange to short eth']) {
    const waiting = await req('POST', '/api/chat', { token, body: { text } });
    assert.equal(waiting.status, 503, text);
    assert.equal(waiting.data.intent, undefined, text);
  }
  const pub = await req('GET', '/api/market/venue-router');
  assert.equal(pub.status, 200);
  assert.equal(pub.data.rows[0].base, 'BTC');
  assert.equal(pub.data.rows[0].long_venue, 'bingx');
});
