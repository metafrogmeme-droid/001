'use strict';
/**
 * Two surfaces computed profit factor and disagreed; Sharpe collapsed to zero
 * on one unpriced row.
 *
 *   trades.js  grossLosses > 0 ? grossWins / grossLosses
 *                              : grossWins > 0 ? 999 : 0
 *   track.js   grossLoss  > 0 ? round2(grossWin / grossLoss) : null
 *
 * Same account with no losing trades: the dashboard said 999, the public page
 * said "—". And an account with no trades at all reported a profit factor of
 * 0 — the worst reading available — for "nothing has closed yet".
 *
 * The Sharpe one was quieter and worse. `sharpe` was initialised to 0, and a
 * single row with size_usd of 0 or null made the whole series NaN. `NaN > 0`
 * is false, the assignment was skipped, and the page rendered **0.00** — a
 * specific, awful-sounding number — for "one row could not be priced".
 *
 * THE CASE THAT PROVES IT NEEDS A BAD ROW AMONG GOOD ONES.
 *
 * A test with only clean data passes against the broken code, because nothing
 * poisons the arithmetic. A test with only bad data passes too, because both
 * versions end up falsy. The defect only shows with a mixed series — which is
 * exactly the shape production data has.
 */

const test = require('node:test');
const assert = require('node:assert');

const { profitFactor, sharpe, coverage } = require('../public/js/trade-stats');

const t = (pnl, size_usd) => ({ pnl, size_usd });

// A normal book: two winners, one loser, all priced.
const BOOK = [t(100, 1000), t(50, 500), t(-30, 300)];


test('profit factor: gross wins over gross losses', () => {
  assert.equal(profitFactor(BOOK), 150 / 30);
});

test('profit factor is NULL with no losses — not 999', () => {
  // 999 is a sentinel that renders as a measurement. Nothing in the data is
  // 999, and the ratio is undefined rather than enormous.
  const winsOnly = [t(100, 1000), t(50, 500)];
  assert.equal(profitFactor(winsOnly), null);
});

test('profit factor is NULL with no trades — not 0', () => {
  // 0 is the WORST possible profit factor. Reporting it for a fresh account
  // is the same defect as a 0% win rate over zero trades.
  assert.equal(profitFactor([]), null);
  assert.equal(profitFactor(null), null);
});

test('profit factor ignores rows it cannot price', () => {
  const withJunk = [...BOOK, t(null, 100), t('abc', 100), t(undefined, 100)];
  assert.equal(profitFactor(withJunk), 150 / 30, 'an unpriced row changed the ratio');
});

test('a scratch is neither a win nor a loss', () => {
  // 0.0 IS a measurement — it just belongs to neither side of the ratio.
  assert.equal(profitFactor([...BOOK, t(0, 100)]), 150 / 30);
});


test('sharpe: computed over per-trade returns', () => {
  const s = sharpe(BOOK);
  assert.ok(s !== null && Number.isFinite(s), 'sharpe should be a real number here');
});

test('ONE unpriced row must not zero the whole series', () => {
  // The incident. size_usd = 0 makes pnl/size Infinity; parseFloat(null) is
  // NaN. Either poisons mean -> variance -> std, `NaN > 0` is false, and the
  // old code left sharpe at its initial 0.
  const good = sharpe(BOOK);
  for (const poison of [t(10, 0), t(10, null), t(10, undefined), t(10, 'x')]) {
    const s = sharpe([...BOOK, poison]);
    assert.ok(s !== null, `a row with size_usd=${poison.size_usd} nulled the result`);
    assert.ok(Number.isFinite(s), 'NaN or Infinity reached the caller');
    assert.equal(s, good, 'the bad row changed the answer instead of leaving');
  }
});

test('sharpe is NULL when it cannot be computed — never 0', () => {
  // 0 claims "this strategy has no edge". Absent says "we could not measure".
  assert.equal(sharpe([]), null);
  assert.equal(sharpe([t(100, 1000)]), null, 'one return has no variance');
  assert.equal(sharpe(null), null);
  assert.equal(sharpe([t(10, 0), t(20, 0)]), null, 'no priceable rows');
});

test('identical returns have no defined sharpe', () => {
  // Zero variance divides by zero. Infinity is not the answer; null is.
  assert.equal(sharpe([t(10, 100), t(20, 200), t(30, 300)]), null);
});

test('sharpe never returns NaN or Infinity', () => {
  for (const rows of [[], [t(1, 0)], [t(NaN, 1)], [t(1, NaN)], [t(1, -5)]]) {
    const s = sharpe(rows);
    assert.ok(s === null || Number.isFinite(s), `leaked ${s}`);
  }
});


test('coverage says how much of the book each figure covers', () => {
  const c = coverage([...BOOK, t(null, 100), t(10, 0)]);
  assert.equal(c.total, 5);
  assert.equal(c.priced, 4, 'pnl readable');
  assert.equal(c.sizeable, 3, 'pnl AND a positive size');
  assert.equal(c.unpriced, 1);
});

test('coverage on a clean book reports no gap', () => {
  const c = coverage(BOOK);
  assert.equal(c.total, c.sizeable);
  assert.equal(c.unpriced, 0);
});
