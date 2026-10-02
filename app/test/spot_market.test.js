'use strict';
/** SPOT-1 — read-only spot market center. No order machinery (source grep). */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const express = require('express');
const spot = require('../lib/spot');
const tickers = require('../lib/tickers');

const RAW = { data: [
  { symbol: 'BTCUSDT', lastPr: '100000', change24h: '0.012', usdtVolume: '2000000000', high24h: '101000', low24h: '98000' },
  { symbol: 'ETHUSDT', lastPr: '4000', change24h: '-0.02', usdtVolume: '900000000' },
  { symbol: 'BTCEUR', lastPr: '90000', change24h: '0', usdtVolume: '1' },
  { symbol: 'JUNKUSDT', lastPr: '0', change24h: '0', usdtVolume: '5' },
] };

test('spot market: USDT pairs only, volume-ranked, junk dropped', async () => {
  spot.setSpotFetcher(async () => RAW);
  const m = await spot.getSpotMarket();
  assert.equal(m.available, true);
  assert.deepEqual(m.pairs.map(p => p.symbol), ['BTCUSDT', 'ETHUSDT']);
  assert.equal(m.pairs[0].change_pct, 1.2);
  assert.equal(m.pairs[0].base, 'BTC');
  assert.match(m.note, /places no spot orders/);
  spot.setSpotFetcher(null);
});

test('spot-perp basis joins both books in bps', async () => {
  spot.setSpotFetcher(async () => RAW);
  tickers.setTickerFetcher(async () => ({ BTCUSDT: { price: 99900, change: 1, volume: 1 } }));
  const b = await spot.getSpotPerpBasis();
  assert.equal(b.available, true);
  assert.equal(b.rows.length, 1);
  assert.ok(Math.abs(b.rows[0].basis_bps - 10.0) < 0.2, String(b.rows[0].basis_bps));
  spot.setSpotFetcher(null); tickers.setTickerFetcher(null);
});

test('unreachable venue reads honestly unavailable', async () => {
  spot.setSpotFetcher(async () => { throw new Error('down'); });
  const m = await spot.getSpotMarket();
  assert.equal(m.available, false);
  assert.equal(m.reason, 'unreachable');
  spot.setSpotFetcher(null);
});

test('the card names the market and the basis, and this module no longer matches the sentence', async () => {
  spot.setSpotFetcher(async () => RAW);
  tickers.setTickerFetcher(async () => ({ BTCUSDT: { price: 99900, change: 1, volume: 1 } }));
  const r = await spot.spotChatCard();
  assert.equal(r.intent, 'spot');
  assert.ok(r.reply_html.includes('Spot market'));
  assert.ok(r.reply_html.includes('basis'));
  assert.match(r.reply_html, /places no spot orders/);
  assert.equal(typeof spot.maybeHandleSpotChat, 'undefined');
  assert.equal(spot.CHAT_RE, undefined);
  spot.setSpotFetcher(null); tickers.setTickerFetcher(null);
});

test('multi-venue: pairs merge across venues with cross-venue spread', async () => {
  spot.setSpotFetcher(async () => RAW, 'bitget');
  spot.setSpotFetcher(async () => ({ result: { list: [
    { symbol: 'BTCUSDT', lastPrice: '100100', price24hPcnt: '0.01', turnover24h: '500000000' },
    { symbol: 'SOLUSDT', lastPrice: '200', price24hPcnt: '0.02', turnover24h: '100000000' },
  ] } }), 'bybit');
  const m = await spot.getSpotMarket();
  assert.equal(m.venues.bitget.ok, true);
  assert.equal(m.venues.bybit.ok, true);
  const btc = m.pairs.find(p => p.symbol === 'BTCUSDT');
  assert.deepEqual(btc.listed_on.sort(), ['bitget', 'bybit']);
  assert.ok(Math.abs(btc.venue_spread_bps - 10.0) < 0.2, String(btc.venue_spread_bps));
  assert.equal(btc.venue, 'bitget', 'primary quote = highest-volume venue');
  assert.ok(m.pairs.some(p => p.symbol === 'SOLUSDT' && p.venue === 'bybit'));
  spot.setSpotFetcher(null);
});

test('one venue down: partial availability reported honestly per venue', async () => {
  spot.setSpotFetcher(async () => RAW, 'bitget');
  spot.setSpotFetcher(async () => { throw new Error('bybit down'); }, 'bybit');
  const m = await spot.getSpotMarket();
  assert.equal(m.available, true, 'one live venue keeps the surface up');
  assert.equal(m.venues.bybit.ok, false);
  spot.setSpotFetcher(null);
});

test('chat: "spot market" waits for the bot; /api/spot/market still serves', async () => {
  spot.setSpotFetcher(async () => RAW);
  tickers.setTickerFetcher(async () => ({ BTCUSDT: { price: 99900, change: 1, volume: 1 } }));
  const app = express();
  app.use(express.json());
  app.use('/api/auth', require('../auth').router);
  app.use('/api/spot', require('../routes/spot'));
  app.use('/api/chat', require('../routes/chat'));
  const server = await new Promise((res) => {
    const s = app.listen(0, '127.0.0.1', () => res(s));
  });
  const base = `http://127.0.0.1:${server.address().port}`;
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
        res.on('data', (c) => { d += c; });
        res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
      });
      r.on('error', reject);
      if (payload) r.write(payload);
      r.end();
    });
  }
  try {
    const reg = await req('POST', '/api/auth/register', {
      body: { email: 'spot-chat@test.io', password: 'x'.repeat(12) },
    });
    const token = reg.data.token;
    for (const text of ['spot market', 'spot pairs', 'spot vs perp', 'spot basis']) {
      const waiting = await req('POST', '/api/chat', { token, body: { text } });
      assert.equal(waiting.status, 503, text);
      assert.equal(waiting.data.intent, undefined, text);
    }
    // "spot prices" is a price question, not this card, on both surfaces.
    const prices = await req('POST', '/api/chat', { token, body: { text: 'spot prices' } });
    assert.equal(prices.status, 503);
    const pub = await req('GET', '/api/spot/market');
    assert.equal(pub.status, 200);
    assert.equal(pub.data.available, true);
    assert.equal(pub.data.pairs[0].symbol, 'BTCUSDT');
    const basis = await req('GET', '/api/spot/basis');
    assert.equal(basis.status, 200);
    assert.equal(basis.data.available, true);
  } finally {
    server.close();
    spot.setSpotFetcher(null);
    tickers.setTickerFetcher(null);
  }
});

test('HARD LINE: no order machinery in the spot surface', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'spot.js'), 'utf8')
    + fs.readFileSync(path.join(__dirname, '..', 'routes', 'spot.js'), 'utf8');
  for (const forbidden of ['placeOrder', 'createOrder', 'submitOrder', '/api/trade',
    'privateKey', 'apiKey', 'API_SECRET', 'signTransaction']) {
    assert.ok(!src.includes(forbidden), `spot surface must never contain ${forbidden}`);
  }
});
