'use strict';
/**
 * Daily Duel settlement — the read is addressed by TIME, not by when we asked.
 *
 * This is what makes lazy settlement safe. A round is settled on the close of
 * the 1h candle containing its horizon, so a round settled six hours late gets
 * the same price as one settled on the minute, and anyone can re-derive it from
 * the public API. A spot mark could not do that: it would quietly record "the
 * price at the moment somebody happened to load the page" and call it a result.
 *
 * The other half is that every failure answers null. A settlement that falls
 * back to any number invents a measurement, and every downstream honesty rule
 * in lib/duel.js is built on being able to tell "unreadable" from "flat".
 */

const test = require('node:test');
const assert = require('node:assert');
const { closeAt, hourStart, setCandleFetcher, HOUR_MS } = require('../lib/candles');

const HORIZON = Date.parse('2026-08-09T00:00:00.000Z');

/** A Bitget-shaped row: [ ts, open, high, low, close, baseVol, quoteVol ]. */
const row = (ts, close) => [String(ts), '1', '2', '0', String(close), '10', '10'];

test.afterEach(() => setCandleFetcher(null));

test('closeAt returns the close of the candle CONTAINING the moment', () => {
  setCandleFetcher(async () => [
    row(HORIZON - HOUR_MS, 100),   // the hour before
    row(HORIZON, 111),             // the hour that contains the horizon
    row(HORIZON + HOUR_MS, 222),   // the hour after
  ]);
  return closeAt('BTCUSDT', HORIZON).then((c) => assert.equal(c, 111));
});

test('a moment mid-bucket resolves to that bucket, not the nearest edge', async () => {
  setCandleFetcher(async () => [row(HORIZON, 111), row(HORIZON + HOUR_MS, 222)]);
  assert.equal(await closeAt('BTCUSDT', HORIZON + 59 * 60000), 111);
  assert.equal(await closeAt('BTCUSDT', HORIZON + HOUR_MS + 1), 222);
});

test('the answer does not depend on when settlement runs', async () => {
  // The same fixed upstream, queried as if hours apart. A spot-mark
  // implementation would return a different number on the second call.
  let calls = 0;
  setCandleFetcher(async () => { calls++; return [row(HORIZON, 111)]; });
  const onTime = await closeAt('BTCUSDT', HORIZON);
  const sixHoursLate = await closeAt('BTCUSDT', HORIZON);
  assert.equal(onTime, sixHoursLate);
  assert.equal(calls, 2, 'both really went to the source');
});

test('an unreadable read answers null — never a zero, never a fallback', async () => {
  setCandleFetcher(async () => { throw new Error('ECONNRESET'); });
  assert.strictEqual(await closeAt('BTCUSDT', HORIZON), null);
});

test('a business error inside an HTTP 200 is a failure, not an empty market', async () => {
  // Bitget's documented trap (see routes/market.js): a 200 whose body carries
  // an error code. Treating the missing array as "no candles" would settle the
  // round as unresolved-forever on a transient upstream complaint at best, and
  // as a price of zero at worst.
  setCandleFetcher(async () => null);
  assert.strictEqual(await closeAt('BTCUSDT', HORIZON), null);
  setCandleFetcher(async () => ({ code: '400171', msg: 'granularity' }));
  assert.strictEqual(await closeAt('BTCUSDT', HORIZON), null);
});

test('a bucket that is simply absent answers null', async () => {
  setCandleFetcher(async () => [row(HORIZON - HOUR_MS, 100), row(HORIZON + HOUR_MS, 222)]);
  assert.strictEqual(await closeAt('BTCUSDT', HORIZON), null,
    'the neighbouring hours must not stand in for the missing one');
});

test('a close of zero or nonsense is a failed read wearing a number', async () => {
  for (const bad of [0, -1, 'abc', null, '']) {
    setCandleFetcher(async () => [row(HORIZON, bad)]);
    assert.strictEqual(await closeAt('BTCUSDT', HORIZON), null,
      `close ${String(bad)} must not settle a round`);
  }
});

test('malformed rows are skipped without throwing', async () => {
  setCandleFetcher(async () => [null, 'nope', [], [HORIZON], row(HORIZON, 111)]);
  assert.equal(await closeAt('BTCUSDT', HORIZON), 111);
});

test('a missing symbol or unreadable timestamp answers null without a request', async () => {
  let called = false;
  setCandleFetcher(async () => { called = true; return [row(HORIZON, 111)]; });
  assert.strictEqual(await closeAt('', HORIZON), null);
  assert.strictEqual(await closeAt('BTCUSDT', NaN), null);
  assert.strictEqual(await closeAt('BTCUSDT', null), null);
  assert.equal(called, false, 'nothing worth asking should reach the network');
});

test('hourStart floors to the UTC hour', () => {
  assert.equal(hourStart(HORIZON), HORIZON);
  assert.equal(hourStart(HORIZON + 1), HORIZON);
  assert.equal(hourStart(HORIZON + HOUR_MS - 1), HORIZON);
  assert.equal(hourStart(HORIZON + HOUR_MS), HORIZON + HOUR_MS);
});

test('the symbol is normalised before it reaches the source', async () => {
  let seen = null;
  setCandleFetcher(async (sym) => { seen = sym; return [row(HORIZON, 111)]; });
  await closeAt(' btcusdt ', HORIZON);
  assert.equal(seen, 'BTCUSDT');
});
