'use strict';
/**
 * The public track record has to add up.
 *
 * Live, it published `trades: 21, wins: 9, losses: 6`. A reader doing the
 * arithmetic finds six trades that are neither, with nothing in the payload to
 * account for them — which is word for word the complaint app/routes/track.js
 * already records about an earlier version publishing "12 (7W/4L)". The
 * monthly block was fixed for it; these headline figures, the ones the homepage
 * renders, were not.
 *
 * Underneath sat the shape this repo is named for: `parseFloat(t.pnl) || 0`
 * over a DECIMAL(14,2) column with no NOT NULL. An unrecorded P&L became a
 * BREAK-EVEN — a measured, deliberate-looking flat trade invented out of a
 * missing value — and then sat in the win-rate denominator, quietly pushing the
 * published rate down. Not reachable while every row is priced, which is why it
 * survived; reachable the first time one is not.
 *
 * The rate is now taken over what was scorable, and the counts that make it
 * reconcile are published beside it. Today that changes no number: every one of
 * the 21 rows is priced, so the denominator is identical and the rate stays
 * 42.86%. It changes the first number that would otherwise have been wrong.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'track.js'), 'utf8');

// The ROUTE'S OWN classifier, not a copy of it. The first version of this file
// reimplemented the arithmetic here, and a mutation pass that hardcoded
// `breakeven` to zero in the route sailed straight through — the test was
// describing the behaviour instead of exercising it.
const { classifyPnls } = require('../routes/track.js');

function classify(rawPnls) {
  const c = classifyPnls(rawPnls);
  return {
    trades: c.parsed.length,
    unpriced: c.unpriced,
    wins: c.wins.length,
    losses: c.losses.length,
    breakeven: c.breakeven,
    win_rate_pct: c.priced.length
      ? Math.round(c.wins.length / c.priced.length * 10000) / 100 : null,
  };
}

test('the live shape reconciles: 21 = 9 + 6 + 6 + 0', () => {
  const pnls = [...Array(9).fill('1.50'), ...Array(6).fill('-1.20'), ...Array(6).fill('0.00')];
  const s = classify(pnls);
  assert.strictEqual(s.trades, 21);
  assert.strictEqual(s.wins, 9);
  assert.strictEqual(s.losses, 6);
  assert.strictEqual(s.breakeven, 6);
  assert.strictEqual(s.unpriced, 0);
  assert.strictEqual(s.wins + s.losses + s.breakeven + s.unpriced, s.trades,
    'a reader must be able to account for every trade');
});

test('and the published rate is unchanged by the fix', () => {
  const pnls = [...Array(9).fill('1.50'), ...Array(6).fill('-1.20'), ...Array(6).fill('0.00')];
  assert.strictEqual(classify(pnls).win_rate_pct, 42.86);
});

test('THE LATENT BUG: an unpriced trade is not a break-even', () => {
  const s = classify(['1.50', '-1.20', null]);
  assert.strictEqual(s.unpriced, 1);
  assert.strictEqual(s.breakeven, 0,
    'a missing P&L rendered as a flat trade invents a measurement');
});

test('and an unpriced trade does not dilute the win rate', () => {
  // 1 win, 1 loss, 1 unreadable. Over what was scored: 50%. Over every row it
  // would read 33.33% — a third of the record lost to a value nobody recorded.
  assert.strictEqual(classify(['1.50', '-1.20', null]).win_rate_pct, 50);
  assert.strictEqual(classify(['1.50', '-1.20', undefined]).win_rate_pct, 50);
  assert.strictEqual(classify(['1.50', '-1.20', '']).win_rate_pct, 50);
});

test('a genuine break-even DOES count in the denominator', () => {
  // It is a real, measured outcome — the opposite of an absent one, and the
  // distinction the whole change is about.
  assert.strictEqual(classify(['1.50', '-1.20', '0.00']).win_rate_pct, 33.33);
});

test('nothing scorable yields no rate at all', () => {
  const s = classify([null, undefined, '']);
  assert.strictEqual(s.win_rate_pct, null,
    'a rate over zero measurements is not a rate, and 0% would read as total failure');
  assert.strictEqual(s.unpriced, 3);
});

test('an empty record yields no rate', () => {
  assert.strictEqual(classify([]).win_rate_pct, null);
});

test('the route publishes the reconciling counts', () => {
  assert.match(src, /breakeven: breakeven/);
  assert.match(src, /unpriced: unpriced/);
});

test('the classifier no longer coerces a missing P&L to zero', () => {
  const block = src.slice(src.indexOf('function classifyPnls'),
                          src.indexOf('const router = express.Router()'));
  assert.doesNotMatch(block, /parseFloat\([^)]*\) \|\| 0/,
    'the shape this repo is named for');
  assert.match(block, /Number\.isFinite/);
});

test('the route uses the classifier rather than its own copy', () => {
  // The seam only helps while the caller goes through it. A second inline
  // split here would drift from the one every test above exercises.
  assert.match(src, /classifyPnls\(trades\.map\(t => t\.pnl\)\)/);
});

test('the rate divides by what was scorable, not by every row', () => {
  assert.match(src, /win_rate_pct: priced\.length \? .*wins\.length \/ priced\.length/);
});

test('the net total sums only priced rows', () => {
  assert.match(src, /netPnl = round2\(priced\.reduce/,
    'summing coerced zeros prints a partial total as a whole one');
});
