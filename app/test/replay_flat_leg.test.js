'use strict';
/**
 * The narrow half of `losses = n - wins`.
 *
 * `computeReplay` already excludes legs it cannot price — they increment
 * `skipped`, which is the honest treatment and was right before this change.
 * So `n - wins` here is not "unreadable counted as a loss"; it is
 * **break-even counted as a loss**, on the replay card a member reads to
 * decide whether to copy an agent.
 *
 * Kept as its own file because the distinction is the point: two sites can
 * carry the same expression and mean different things, and the fix for one is
 * not automatically the fix for the other. `lib/agent_record.js` has the same
 * shape and is NOT a defect — it filters on `Number.isFinite` first and
 * counts wins and losses separately, so its `flats = n - wins - losses` is
 * exactly the residue it names.
 */
const test = require('node:test');
const assert = require('node:assert');

const { computeReplay } = require('../lib/replay');

const t = (pnl, size = 100, day = 7) =>
  ({ pnl, size_usd: size, symbol: 'BTC/USDT', closed_at: `2026-07-0${day}T10:00:00Z` });

test('a flat leg is not a loss', () => {
  const r = computeReplay([t(10), t(-5), t(0)], 1000);
  assert.strictEqual(r.trades, 3);
  assert.strictEqual(r.wins, 1);
  assert.strictEqual(r.losses, 1, 'the break-even leg was filed as a defeat');
  assert.strictEqual(r.flat, 1);
  assert.strictEqual(r.wins + r.losses + r.flat, r.trades);
});

test('unpriceable legs are still skipped, not scored either way', () => {
  const r = computeReplay([t(10), t(null), t(5, 0), t(-5)], 1000);
  assert.strictEqual(r.skipped, 2, 'a null pnl and a zero size must both skip');
  assert.strictEqual(r.trades, 2);
  assert.strictEqual(r.wins, 1);
  assert.strictEqual(r.losses, 1);
});

test('an all-flat replay reports 0% and zero losses, not a wipeout', () => {
  const r = computeReplay([t(0), t(0)], 1000);
  assert.strictEqual(r.win_rate_pct, 0, 'two legs, none won — that IS 0%');
  assert.strictEqual(r.losses, 0, 'two flat legs is not two losses');
  assert.strictEqual(r.flat, 2);
});

test('an empty replay has no win rate at all', () => {
  const r = computeReplay([], 1000);
  assert.strictEqual(r.trades, 0);
  assert.strictEqual(r.win_rate_pct, null, 'this one was already right');
  assert.strictEqual(r.losses, 0);
});
