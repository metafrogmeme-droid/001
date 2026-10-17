'use strict';
/**
 * The Arena's price reader (lib/tickers.js) answers, or says why it cannot.
 *
 * THE FINDING (live, 2026-10-17). In the Pi app every open position read
 * "mark unavailable" and every close came back "The position could not be
 * closed." Every Arena request that read marks took its full 6s budget,
 * while /api/market/ticker, which reads the same Bitget endpoint through
 * lib/http_cache.js's https.get, answered in about a second from the same
 * host. The reader swallowed every failure, so nothing said why.
 *
 * These tests drive the production getTickers through its transport seam
 * (setTickerTransport), so the cache, the in-flight join, the parse and the
 * logging under test are the ones that run live:
 *
 *   It reads through https.get, as the market route does.
 *   An in-flight fetch that has stalled is abandoned, not joined forever.
 *   An answer with no tickers is unreadable, never a fresh empty map.
 *   A failure is logged with its stack, once a minute, not on every request.
 *   The status page reads the same reader and says fresh, stale or unreadable.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');

const tickers = require('../lib/tickers');

const BODY = { code: '00000', data: [
  { symbol: 'ETHUSDT', lastPr: '2405.68', change24h: '-0.01191', usdtVolume: '1050000000' },
  { symbol: 'BTCUSDT', lastPr: '64012.5', change24h: '0.002', usdtVolume: '9000000000' },
] };

test.afterEach(() => tickers.setTickerTransport(null));

test('it reads through https.get, the transport the market route answers with', () => {
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'lib', 'tickers.js'), 'utf8'));
  assert.match(src, /require\('\.\/http_cache'\)/);
  assert.match(src, /fetchJSON\(TICKERS_URL\)/);
  assert.doesNotMatch(src, /\bfetch\(/, 'the global fetch is back in the Arena\'s price reader');
});

test('a Bitget answer becomes the map; one read serves the next 30 seconds', async () => {
  let calls = 0;
  tickers.setTickerTransport(async () => { calls++; return BODY; });
  const m = await tickers.getTickers();
  assert.deepEqual(m.ETHUSDT, { price: 2405.68, change: -1.191, volume: 1050000000 });
  await tickers.getTickers();
  assert.equal(calls, 1);
});

test('an answer with no tickers is unreadable, never a fresh empty map', async () => {
  assert.throws(() => tickers.parseTickers({ code: '40018', msg: 'Invalid IP', data: null }), /without a list \(code 40018\)/);
  assert.throws(() => tickers.parseTickers({ code: '00000', data: [] }), /no priced symbol/);
  assert.throws(() => tickers.parseTickers({ data: [{ symbol: 'X', lastPr: 'n/a' }] }), /no priced symbol/);
  assert.throws(() => tickers.parseTickers(null), /without a list/);

  let calls = 0;
  const orig = console.error;
  console.error = () => {};
  try {
    tickers.setTickerTransport(async () => { calls++; return calls === 1 ? { code: '429', data: null } : BODY; });
    await assert.rejects(tickers.getTickers());
    const m = await tickers.getTickers();
    assert.equal(calls, 2, 'a refused read was cached as if it were prices');
    assert.equal(m.BTCUSDT.price, 64012.5);
  } finally {
    console.error = orig;
  }
});

// A time limit: a reader that joins a stalled fetch forever makes this test
// wait forever, and a hang must fail in seconds, not stall the suite.
test('a stalled in-flight fetch is joined for a while, then abandoned', { timeout: 5000 }, async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: 1_000_000 });
  let calls = 0;
  tickers.setTickerTransport(() => {
    calls++;
    return calls === 1 ? new Promise(() => {}) : Promise.resolve(BODY);   // the first never answers
  });
  const stuck = tickers.getTickers();
  stuck.catch(() => {});
  t.mock.timers.tick(5_000);
  tickers.getTickers().catch(() => {});
  assert.equal(calls, 1, 'a fetch five seconds old is joined, not duplicated');
  t.mock.timers.tick(tickers.STALLED_MS);
  const m = await tickers.getTickers();
  assert.equal(calls, 2, 'a fetch past STALLED_MS was joined instead of replaced');
  assert.equal(m.ETHUSDT.price, 2405.68);
});

test('a request waits its own budget, then shows the last good map, then nothing', async () => {
  tickers.setTickerTransport(() => new Promise(() => {}));
  const none = await tickers.getTickersWithin(50);
  assert.deepEqual(none, { map: {}, ageMs: Infinity }, 'no prices is no prices, not a guess');
});

test('a failure is logged with its stack, once a minute, not on every request', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: 5_000_000 });
  const lines = [];
  const orig = console.error;
  console.error = (...a) => lines.push(a.map(String).join(' '));
  try {
    tickers.setTickerTransport(async () => { throw new Error('connect ETIMEDOUT 104.18.14.166:443'); });
    await assert.rejects(tickers.getTickers());
    await assert.rejects(tickers.getTickers());
    t.mock.timers.tick(30_000);
    await assert.rejects(tickers.getTickers());
    assert.equal(lines.length, 1, lines.join('\n'));
    assert.match(lines[0], /^Arena tickers unreadable: Error: connect ETIMEDOUT/);
    assert.match(lines[0], /\n\s+at /, 'the log line carries a stack');
    t.mock.timers.tick(31_000);
    await assert.rejects(tickers.getTickers());
    assert.equal(lines.length, 2, 'a minute later the failure is logged again');
  } finally {
    console.error = orig;
  }
});

// ── the status page asks the same reader ──────────────────────────────────

test('prices read a minute ago are stale on the status page, not fresh', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: 9_000_000 });
  const status = require('../lib/status');
  let up = true;
  tickers.setTickerTransport(async () => { if (up) return BODY; throw new Error('feed down'); });
  await tickers.getTickers();                     // one good read
  up = false;
  t.mock.timers.tick(60_000);                     // past the 30s fill bound, inside the 120s display bound
  const orig = console.error;
  console.error = () => {};
  try {
    const probe = status.defaultProbes().readPrices;
    assert.deepEqual(await probe(), { state: 'stale', age_s: 60, symbols: 2 },
      'a minute-old map was reported as live prices');
  } finally {
    console.error = orig;
  }
});

test('the status page reads the Arena\'s prices: fresh, or unreadable and not ok', async () => {
  const status = require('../lib/status');
  const real = status.defaultProbes();
  const fixed = {
    ...real,
    // A minute old: buildStatus reads its clock before the probes run, so a
    // stamp taken inside a probe is younger than "now" and reads as no data.
    getScan: async () => ({ received_at: new Date(Date.now() - 60_000).toISOString() }),
    getReports: async () => ({ received_at: new Date(Date.now() - 60_000).toISOString() }),
    pingGateway: async () => ({ state: 'reachable' }),
    pingBridge: async () => ({ state: 'reachable' }),
    pingDatabase: async () => ({ state: 'ok' }),
    latestLetter: async () => null,
  };
  try {
    tickers.setTickerTransport(async () => BODY);
    status.setProbes(fixed);
    const ok = await status.buildStatus();
    assert.deepEqual(ok.components.arena_prices,
      { state: 'fresh', age_s: 0, symbols: 2, note: ok.components.arena_prices.note });
    assert.equal(ok.status, 'ok');

    const orig = console.error;
    console.error = () => {};
    try {
      tickers.setTickerTransport(async () => ({ code: '40018', data: null }));
      const bad = await status.buildStatus();
      assert.equal(bad.components.arena_prices.state, 'unreadable');
      assert.notEqual(bad.status, 'ok', 'an Arena with no prices was reported healthy');
    } finally {
      console.error = orig;
    }
  } finally {
    status.setProbes(null);
  }
});
