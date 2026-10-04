'use strict';
/**
 * The insights panel painted an unmeasured group RED at 0%.
 *
 * `computeAnalytics` skips unresolved signals, so `n` counts only priced
 * rows — which makes `losses: n - wins` losses PLUS break-evens, per group
 * and overall. Narrower than the nullable-column case, and still a claim
 * about rows that did not lose.
 *
 * The rendering made it worse. `dashboard.js` drew each group as
 *
 *     const wr = g.n ? Math.round(g.wins / g.n * 100) : 0;
 *     <b class="${wr >= 50 ? 'pos' : 'neg'}">${wr}%</b>
 *
 * so a group with nothing resolved rendered **0% in red** — the worst
 * reading available, for the absence of a reading. Colour is a claim, and
 * unknown gets a muted one.
 */
const test = require('node:test');
const assert = require('node:assert');

const { computeAnalytics } = require('../lib/signal_analytics');

const sig = (pnl, pattern = 'breakout', symbol = 'BTC/USDT') =>
  ({ pnl, pattern, symbol, direction: 'LONG', confidence: 0.7 });

test('a break-even signal is not a loss, overall or per group', () => {
  const a = computeAnalytics([sig(10), sig(-5), sig(0)]);
  assert.strictEqual(a.overall.resolved, 3);
  assert.strictEqual(a.overall.wins, 1);
  assert.strictEqual(a.overall.losses, 1, '0.00 was filed as a defeat');
  assert.strictEqual(a.overall.flat, 1);

  const g = a.by_pattern.find((r) => r.key === 'breakout');
  assert.strictEqual(g.losses, 1, 'same defect one level down, per group');
  assert.strictEqual(g.flat, 1);
  assert.strictEqual(g.wins + g.losses + g.flat, g.n);
});

test('unresolved signals leave both sides of every ratio', () => {
  const a = computeAnalytics([sig(10), sig(null), sig(undefined), sig(NaN)]);
  assert.strictEqual(a.overall.resolved, 1, 'an unresolved signal was counted');
  assert.strictEqual(a.overall.win_rate, 100);
});

test('nothing resolved is null, never 0%', () => {
  const a = computeAnalytics([sig(null), sig(null)]);
  assert.strictEqual(a.overall.resolved, 0);
  assert.strictEqual(a.overall.win_rate, null,
    '0% claims every signal lost; nothing resolved claims nothing');
  assert.strictEqual(a.overall.net_r, null,
    'a sum of 0 over an empty set prints a measured flat book');
  assert.strictEqual(a.overall.mean_r, null,
    'a mean over nothing resolved is not 0R');
  assert.ok(!('net_pnl' in a.overall), 'the R sum is no longer named as dollars');
});

test('a measured zero survives', () => {
  const a = computeAnalytics([sig(0), sig(0)]);
  assert.strictEqual(a.overall.win_rate, 0, 'two resolved, none won — that IS 0%');
  assert.strictEqual(a.overall.net_r, 0, 'two resolved flats sum to a measured 0R');
  assert.strictEqual(a.overall.mean_r, 0, 'the mean of two flats is 0R, not absent');
  assert.strictEqual(a.overall.flat, 2);
  const g = a.by_pattern[0];
  assert.strictEqual(g.net_r, 0);
  assert.strictEqual(g.mean_r, 0);
});

test('an unreadable R is left out of the sum, not booked as 0R', () => {
  // 2R beside a missing row and a word. Counting either as 0R would publish
  // a mean of 2/3 or 1, which is a different book.
  const a = computeAnalytics([sig(2), sig(null), sig('n/a'), sig(undefined)]);
  assert.strictEqual(a.overall.resolved, 1);
  assert.strictEqual(a.overall.net_r, 2);
  assert.strictEqual(a.overall.mean_r, 2);
  const g = a.by_pattern.find((row) => row.key === 'breakout');
  assert.strictEqual(g.n, 1);
  assert.strictEqual(g.net_r, 2);
  assert.strictEqual(g.mean_r, 2);
});

test('each group carries its own R, and a flat group stays 0', () => {
  // Overall mean is 0.33. Breakout's mean is 0.50. Sweep is a measured 0.
  // One figure copied onto every group would pass a single-group fixture.
  const a = computeAnalytics([
    sig(2, 'breakout', 'BTC/USDT'),
    sig(-1, 'breakout', 'BTC/USDT'),
    sig(0, 'sweep', 'ETH/USDT'),
  ]);
  assert.strictEqual(a.r_basis, 'gross');
  assert.strictEqual(a.overall.net_r, 1);
  assert.strictEqual(a.overall.mean_r, 0.33);
  const breakout = a.by_pattern.find((row) => row.key === 'breakout');
  const sweep = a.by_pattern.find((row) => row.key === 'sweep');
  assert.strictEqual(breakout.net_r, 1);
  assert.strictEqual(breakout.mean_r, 0.5);
  assert.strictEqual(sweep.net_r, 0);
  assert.strictEqual(sweep.mean_r, 0);
  assert.notStrictEqual(breakout.mean_r, a.overall.mean_r);
});

test('a DECIMAL string zero is a measured flat, and a missing pattern still sums', () => {
  const a = computeAnalytics([
    { pnl: '0.00', pattern: null, symbol: 'ETH/USDT', direction: 'SHORT', confidence: 0.2 },
  ]);
  assert.strictEqual(a.overall.resolved, 1);
  assert.strictEqual(a.overall.net_r, 0);
  assert.strictEqual(a.overall.mean_r, 0);
  assert.strictEqual(a.overall.flat, 1);
  const g = a.by_pattern[0];
  assert.strictEqual(g.key, '(none)');
  assert.strictEqual(g.net_r, 0);
  assert.strictEqual(g.mean_r, 0);
});

test('the buckets partition every resolved signal', () => {
  for (const set of [[sig(1), sig(-1), sig(0)], [sig(null)], [],
                     [sig(3), sig(2)], [sig(-3), sig(0), sig(null)]]) {
    const o = computeAnalytics(set).overall;
    assert.strictEqual(o.wins + o.losses + o.flat, o.resolved,
      `buckets do not partition ${JSON.stringify(set.map((s) => s.pnl))}`);
  }
});

test('the dashboard reads win_rate rather than recomputing it', () => {
  // The panel used to divide wins by n itself and fall back to 0. Recomputing
  // a ratio the payload already carries is how the two came apart: the server
  // learned to say null and the browser kept saying 0.
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.ok(!/g\.wins \/ g\.n/.test(src),
    'the dashboard recomputes the rate somewhere instead of reading win_rate');

  // AND the property itself, asked of the renderer that now owns it. The rows
  // moved into public/js/winrate-bar.js when the sample floor was added, and
  // this assertion went with them — but it stopped being a source scan on the
  // way, because the property is reachable by calling the function. A scan can
  // only see that the right characters are present; the question is what the
  // code DOES with a null.
  const WR = require('../public/js/winrate-bar');

  // wins/n would be 50%. win_rate is what the server published.
  assert.strictEqual(
    WR.classify({ pattern: 'p', win_rate: 61, wins: 10, n: 20 }, 'pattern').rate, 61,
    'the panel recomputed the rate from wins/n instead of reading win_rate');

  // And an absent rate stays absent rather than becoming 0.
  assert.strictEqual(
    WR.classify({ pattern: 'p', win_rate: null, wins: 0, n: 0 }, 'pattern').rate, null);
});

function cell(over = {}) {
  return {
    pnl: 1,
    signal_type: 'vwap_reversion',
    regime: 'TREND',
    timeframe: '1h',
    source: 'rules',
    direction: 'LONG',
    pattern: 'breakout',
    symbol: 'BTC/USDT',
    confidence: 0.7,
    ...over,
  };
}

test('a setup cell is the cross of the five recorded dimensions', () => {
  // Two cells, different means. One figure copied onto every cell would
  // pass a single-cell fixture. The flat cell stays 0.
  const a = computeAnalytics([
    cell({ pnl: 2, signal_type: 'vwap_reversion' }),
    cell({ pnl: -1, signal_type: 'vwap_reversion' }),
    cell({ pnl: 0, signal_type: 'sweep', regime: 'RANGE', timeframe: '4h', source: 'llm' }),
  ]);
  assert.strictEqual(a.r_basis, 'gross');
  assert.strictEqual(a.by_setup.length, 2);
  const vwap = a.by_setup.find((g) => g.setup === 'vwap_reversion');
  const sweep = a.by_setup.find((g) => g.setup === 'sweep');
  assert.deepStrictEqual(
    [vwap.setup, vwap.regime, vwap.timeframe, vwap.source, vwap.direction],
    ['vwap_reversion', 'TREND', '1h', 'rules', 'LONG']);
  assert.strictEqual(vwap.n, 2);
  assert.strictEqual(vwap.net_r, 1);
  assert.strictEqual(vwap.mean_r, 0.5);
  assert.strictEqual(sweep.net_r, 0);
  assert.strictEqual(sweep.mean_r, 0);
  assert.strictEqual(sweep.flat, 1);
  assert.notStrictEqual(vwap.mean_r, sweep.mean_r);
  assert.strictEqual(vwap.wins + vwap.losses + vwap.flat, vwap.n);
});

test('a missing dimension is left out of the cell, and an unreadable R is left out of both', () => {
  // 2 and -1 share a cell (net 1, mean 0.5). The 4R row has no timeframe, so
  // filing it as timeframe "(none)" would publish a different book (net 5).
  // The word and the null are unreadable and stay out of the count and the sum.
  // pattern is a different column and does not stand in for signal_type.
  const a = computeAnalytics([
    cell({ pnl: 2 }),
    cell({ pnl: -1 }),
    cell({ pnl: 4, timeframe: null, pattern: 'breakout' }),
    cell({ pnl: 9, signal_type: null, pattern: 'breakout' }),
    cell({ pnl: 'n/a' }),
    cell({ pnl: null }),
  ]);
  assert.strictEqual(a.overall.resolved, 4);
  assert.strictEqual(a.overall.net_r, 14);
  assert.strictEqual(a.by_setup.length, 1);
  const g = a.by_setup[0];
  assert.strictEqual(g.n, 2);
  assert.strictEqual(g.net_r, 1);
  assert.strictEqual(g.mean_r, 0.5);
  assert.ok(a.by_setup.every((row) => row.timeframe && row.setup));
  assert.ok(!a.by_setup.some((row) => row.setup === 'breakout' || row.setup === '(none)'));
});

test('a blank, a number, and a case-different direction are not a new dimension', () => {
  const a = computeAnalytics([
    cell({ pnl: 2, direction: 'long' }),
    cell({ pnl: -1, direction: ' LONG ' }),
    cell({ pnl: 3, source: '   ' }),
    cell({ pnl: 4, signal_type: 1 }),
    cell({ pnl: 5, timeframe: '  4h  ', regime: ' RANGE ' }),
  ]);
  assert.strictEqual(a.overall.resolved, 5);
  assert.strictEqual(a.by_setup.length, 2);
  const vwap = a.by_setup.find((g) => g.timeframe === '1h');
  const other = a.by_setup.find((g) => g.timeframe === '4h');
  assert.strictEqual(vwap.n, 2);
  assert.strictEqual(vwap.direction, 'LONG');
  assert.strictEqual(vwap.net_r, 1);
  assert.strictEqual(vwap.mean_r, 0.5);
  assert.strictEqual(other.regime, 'RANGE');
  assert.strictEqual(other.timeframe, '4h');
  assert.strictEqual(other.net_r, 5);
  assert.strictEqual(other.mean_r, 5);
});

test('nothing resolved publishes no setup cell and a null R', () => {
  const a = computeAnalytics([cell({ pnl: null }), cell({ pnl: undefined })]);
  assert.deepStrictEqual(a.by_setup, []);
  assert.strictEqual(a.overall.net_r, null);
  assert.strictEqual(a.overall.mean_r, null);
});

test('the setup board omits a missing group and does not paint a null mean as 0R', () => {
  const WR = require('../public/js/winrate-bar');
  assert.strictEqual(WR.setupScoreboard(undefined), '');
  assert.strictEqual(WR.setupScoreboard(null), '');
  assert.strictEqual(WR.setupScoreboard([]), '');
  const dropped = WR.setupScoreboard([{
    setup: 'vwap_reversion', regime: 'TREND', timeframe: null,
    source: 'rules', direction: 'LONG', n: 10, win_rate: 60, mean_r: 0, net_r: 0,
  }]);
  assert.strictEqual(dropped, '', 'a missing timeframe was painted');
  assert.ok(!dropped.includes('(none)'));

  const absentMean = WR.setupScoreboard([{
    setup: 'vwap_reversion', regime: 'TREND', timeframe: '1h',
    source: 'rules', direction: 'LONG', n: 2, win_rate: 0, mean_r: null, net_r: null,
  }]);
  assert.ok(absentMean.includes('vwap_reversion'));
  assert.ok(!absentMean.includes('0R'), 'a null mean was painted as 0R');

  const flat = WR.setupScoreboard([{
    setup: 'sweep', regime: 'RANGE', timeframe: '4h',
    source: 'llm', direction: 'SHORT', n: 2, win_rate: 0, mean_r: 0, net_r: 0,
  }]);
  assert.ok(flat.includes('0R'), 'a measured 0R was omitted');
  assert.ok(flat.includes('wr-unrated'), 'two trades were ranked');
  assert.ok(!flat.includes('wr-pos'));

  const rated = WR.setupScoreboard([{
    setup: 'vwap_reversion', regime: 'TREND', timeframe: '1h',
    source: 'rules', direction: 'LONG', n: 10, win_rate: 60, mean_r: 0.5, net_r: 5,
  }]);
  assert.ok(rated.includes('wr-pos'));
  assert.ok(rated.includes('0.5R'));

  const fs = require('node:fs');
  const path = require('node:path');
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));
  assert.match(src, /setupScoreboard\(a\.by_setup\)/);
  assert.ok(!/by_setup \|\| \[\]/.test(src),
    'a missing setup group is filled with an empty list and then painted');
});
