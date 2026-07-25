'use strict';
// The Arena account must answer before the browser gives up on it.
//
// The client allows /api/arena/account 14s and turns any failure — including
// its own abort — into "Couldn't load your account". getTickers() may spend
// 10s on a cold cache, so one slow upstream fetch could eat the whole budget
// and the panel would report a load failure for a request the server was
// about to answer correctly. An abort and a 500 look identical from the page,
// which is why this went unreproduced for so long: there was never a 500.
//
// The handler already degrades when marks are missing (a position renders with
// a null mark). It just has to degrade IN TIME.

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const { setTickerFetcher, getTickersWithin } = require('../lib/tickers');

// What the browser gives the request, from public/arena.html.
const CLIENT_BUDGET_MS = 14000;

let server, base;
let ticker = async () => ({ BTCUSDT: { price: 64386.8, change: 0, volume: 1 } });

test.before(async () => {
  setTickerFetcher((...a) => ticker(...a));
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/arena', require('../routes/arena'));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); setTickerFetcher(null); });

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, { method, headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(payload ? { 'Content-Type': 'application/json' } : {}) } }, (res) => {
      let d = ''; res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject); if (payload) r.write(payload); r.end();
  });
}

async function newUser() {
  const em = `slow${Date.now()}${Math.floor(process.hrtime()[1] / 1000)}@x.io`;
  const reg = await req('POST', '/api/auth/register', { body: { email: em, password: 'p'.repeat(12) } });
  assert.ok(reg.data.token, 'registered');
  return reg.data.token;
}

test('a hanging ticker feed does not cost the client its whole budget', async () => {
  const token = await newUser();
  const open = await req('POST', '/api/arena/open', { token,
    body: { symbol: 'BTCUSDT', direction: 'long', margin: 500, leverage: 3 } });
  assert.equal(open.status, 200, 'opened a position');

  // Upstream hangs far past anything the page will wait for.
  let released;
  ticker = () => new Promise((r) => { released = r; });

  const t0 = Date.now();
  const r = await req('GET', '/api/arena/account', { token });
  const elapsed = Date.now() - t0;
  if (released) released({});

  assert.equal(r.status, 200, 'the account still answers');
  assert.ok(elapsed < CLIENT_BUDGET_MS,
    `took ${elapsed}ms — the client gives up at ${CLIENT_BUDGET_MS}ms`);
  // The position is still reported; only its live mark is unknown.
  assert.equal((r.data.positions || []).length, 1, 'the position is not hidden');
  // The open above warmed the cache seconds ago, so a recent mark is reused —
  // the normal compromise. That it must be RECENT is asserted separately below.
  assert.ok(r.data.positions[0].mark > 0, 'a recent mark is reused rather than blanked');
});

test('a slow feed falls back to the last known marks, not to nothing', async () => {
  // Warm the cache with a good fetch, then hang. Stale marks beat no marks.
  ticker = async () => ({ ETHUSDT: { price: 3000, change: 0, volume: 1 } });
  await getTickersWithin(2000);
  ticker = () => new Promise(() => {});
  const marks = await getTickersWithin(150);
  assert.deepStrictEqual(marks, { ETHUSDT: { price: 3000, change: 0, volume: 1 } },
    'the last successful map is reused rather than discarded');
});

test('a mark too old to defend is dropped, not shown as current', async () => {
  const { lastKnownTickers } = require('../lib/tickers');
  ticker = async () => ({ BTCUSDT: { price: 111, change: 0, volume: 1 } });
  await getTickersWithin(2000);
  assert.ok(lastKnownTickers(), 'a fresh map is offered');

  // Age the cached map past the bound without waiting for it.
  const realNow = Date.now;
  Date.now = () => realNow() + 130000;
  try {
    assert.equal(lastKnownTickers(), null,
      'a map older than the bound is withheld — a stale price is not a current price');
    ticker = () => new Promise(() => {});
    assert.deepStrictEqual(await getTickersWithin(120), {},
      'and the caller gets empty marks, so the UI blanks instead of inventing');
  } finally { Date.now = realNow; }
});

test('a failing feed degrades to empty marks, never throws', async () => {
  setTickerFetcher(null);          // clear the warmed cache
  setTickerFetcher(async () => { throw new Error('gateway down'); });
  assert.deepStrictEqual(await getTickersWithin(500), {},
    'a rejected fetch resolves to empty marks');
  setTickerFetcher((...a) => ticker(...a));
});

test('order paths still refuse a stale price rather than degrade', () => {
  // A read may show a stale mark; a FILL may not be priced off one.
  const src = require('node:fs').readFileSync(require('node:path')
    .join(__dirname, '..', 'routes', 'arena.js'), 'utf8');
  const opens = src.split('\n')
    .map((l, i) => ({ l, n: i + 1 }))
    .filter(({ l }) => /getTickersWithin\(/.test(l));
  assert.ok(opens.length >= 3, 'the read paths are bounded');
  // The two order handlers keep the strict call and their 503 refusal.
  assert.equal((src.match(/try \{ marks = await getTickers\(\); \} catch \(e\) \{/g) || []).length, 2,
    'exactly the two order paths still use the strict fetch');
  assert.match(src, /Market data unavailable/, 'and refuse the fill when it is missing');
});
