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
  assert.strictEqual(a.overall.net_pnl, null,
    'round2(0) over an empty set prints a measured flat book');
});

test('a measured zero survives', () => {
  const a = computeAnalytics([sig(0), sig(0)]);
  assert.strictEqual(a.overall.win_rate, 0, 'two resolved, none won — that IS 0%');
  assert.strictEqual(a.overall.net_pnl, 0);
  assert.strictEqual(a.overall.flat, 2);
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
