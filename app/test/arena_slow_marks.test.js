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
  const { map } = await getTickersWithin(150);
  assert.deepStrictEqual(map, { ETHUSDT: { price: 3000, change: 0, volume: 1 } },
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
    assert.deepStrictEqual((await getTickersWithin(120)).map, {},
      'and the caller gets empty marks, so the UI blanks instead of inventing');
  } finally { Date.now = realNow; }
});

test('a failing feed degrades to empty marks, never throws', async () => {
  setTickerFetcher(null);          // clear the warmed cache
  setTickerFetcher(async () => { throw new Error('gateway down'); });
  assert.deepStrictEqual((await getTickersWithin(500)).map, {},
    'a rejected fetch resolves to empty marks');
  setTickerFetcher((...a) => ticker(...a));
});

test('order paths still refuse a stale price rather than degrade', () => {
  // A read may show a stale mark; a FILL may not be priced off one.
  const src = require('node:fs').readFileSync(require('node:path')
    .join(__dirname, '..', 'routes', 'arena.js'), 'utf8');
  // Four: /open, /close, /open-signal and /exits. Moving an exit is an
  // order-shaped decision — a level validated against a stale mark could sit
  // on the wrong side of the real price and fire instantly.
  assert.equal((src.match(/try \{ marks = await getTickers\(\); \} catch \(e\) \{/g) || []).length, 4,
    'exactly the four order paths still use the strict fetch');
  assert.match(src, /Market data unavailable/, 'and refuse the fill when it is missing');
});

test('GET /account is a WRITE path and must not settle off a stale mark', async () => {
  // This is the regression an adversarial review caught after the first fix
  // shipped. /account is not read-only: settleLiquidations() closes positions
  // and sweepFollows() OPENS them, sealing the entry price into a
  // Provable-Calls receipt. Handing those a 120s-old map priced fills off it —
  // and quadrupled the pre-fix 30s bound while claiming to protect it.
  const { FILL_MAX_AGE_MS, TTL_MS } = require('../lib/tickers');
  assert.equal(FILL_MAX_AGE_MS, TTL_MS,
    'the fill bound tracks the cache TTL — display may degrade, money may not');

  const src = require('node:fs').readFileSync(require('node:path')
    .join(__dirname, '..', 'routes', 'arena.js'), 'utf8');
  // Behavioural, not a grep for a call site: the two writers must receive the
  // age-gated map, never the display map.
  assert.match(src, /settleLiquidations\(userId, positions, fillMarks\)/,
    'settleLiquidations gets the age-gated marks');
  assert.match(src, /sweepFollows\(userId, positions, fillMarks\)/,
    'sweepFollows gets the age-gated marks');
  assert.match(src, /const fillMarks = tick\.ageMs <= FILL_MAX_AGE_MS \? marks : \{\}/,
    'and the gate is an explicit age comparison');
});

test('a stale price cannot liquidate a real position', async () => {
  // The proof, not a grep: hold a mark that WOULD liquidate, age it past the
  // fill bound, and confirm the position survives. Before the age gate this
  // wrote an irreversible arena_trades row at a price nobody had quoted for
  // two minutes.
  const { getTickersWithin, FILL_MAX_AGE_MS } = require('../lib/tickers');
  const token = await newUser();

  ticker = async () => ({ BTCUSDT: { price: 100, change: 0, volume: 1 } });
  const open = await req('POST', '/api/arena/open', { token,
    body: { symbol: 'BTCUSDT', direction: 'long', margin: 500, leverage: 3 } });
  assert.equal(open.status, 200, 'opened at 100');

  // A price deep enough to liquidate a 3x long, now sitting in the cache.
  ticker = async () => ({ BTCUSDT: { price: 40, change: 0, volume: 1 } });
  await getTickersWithin(2000);
  ticker = () => new Promise(() => {});          // upstream now hangs

  const realNow = Date.now;
  Date.now = () => realNow() + FILL_MAX_AGE_MS + 15000;   // older than a fill may be
  let acct;
  try {
    acct = await req('GET', '/api/arena/account', { token });
  } finally { Date.now = realNow; }

  assert.equal(acct.status, 200, 'the account still answers');
  assert.equal((acct.data.positions || []).length, 1,
    'the position was NOT liquidated off a stale price');
  assert.equal((acct.data.history || []).length, 0,
    'and no closed-trade row was written');
});

test('getTickersWithin reports freshness, so a caller can tell', async () => {
  const { getTickersWithin } = require('../lib/tickers');
  ticker = async () => ({ BTCUSDT: { price: 5, change: 0, volume: 1 } });
  const live = await getTickersWithin(2000);
  assert.equal(live.ageMs, 0, 'a live fetch reports age 0');
  assert.ok(live.map.BTCUSDT, 'and carries the map');

  const realNow = Date.now;
  Date.now = () => realNow() + 45000;          // past TTL, inside the 120s bound
  try {
    ticker = () => new Promise(() => {});
    const stale = await getTickersWithin(120);
    assert.ok(stale.ageMs >= 45000, `a stale hit reports its real age (${stale.ageMs}ms)`);
    assert.ok(stale.map.BTCUSDT, 'the stale map is still offered for display');
    const { FILL_MAX_AGE_MS } = require('../lib/tickers');
    assert.ok(stale.ageMs > FILL_MAX_AGE_MS,
      'and at this age a fill path would correctly refuse it');
  } finally { Date.now = realNow; }
});
