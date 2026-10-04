'use strict';
/**
 * A setup cell at or above the sample floor reads "exploratory".
 *
 * A q-value under 0.05 and an interval clear of zero do not promote the
 * cell to a verdict, a "survives" label, or a follow offer. A missing
 * dimension and an empty payload are omitted: they are not labelled, and
 * they are not given a 0. A measured flat book stays 0. Under the floor
 * that 0 reads "too thin to say"; the floor itself is the other test.
 * An unreadable interval stays unavailable.
 */
const test = require('node:test');
const assert = require('node:assert/strict');

const { computeAnalytics } = require('../lib/signal_analytics');
const { publicAnalytics, dollarKeys } = require('../lib/public_signal');
const WR = require('../public/js/winrate-bar');

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

/** n rows whose mean and standard error realise a two-sided z. */
function rowsAtZ(setup, n, mean, z) {
  const se = Math.abs(mean) / z;
  const variance = se * se * n;
  const d = Math.sqrt(variance * (n - 1) / n);
  const rows = [];
  for (let i = 0; i < n; i++) {
    const pnl = i < n / 2 ? mean + d : mean - d;
    rows.push(cell({
      pnl,
      signal_type: setup,
      regime: 'TREND',
      timeframe: '1h',
      source: 'rules',
      direction: 'LONG',
    }));
  }
  return rows;
}

test('a strong cell still reads exploratory, and a missing cell is omitted', () => {
  const book = computeAnalytics(rowsAtZ('alpha', 40, 1, 2.58).concat([
    cell({ pnl: 4, signal_type: 'omitted', timeframe: null }),
    cell({ pnl: null, signal_type: 'unread' }),
  ]));
  assert.equal(book.by_setup.length, 1);
  const g = book.by_setup[0];
  assert.equal(g.setup, 'alpha');
  assert.ok(g.q_value != null && g.q_value < 0.05, `q was ${g.q_value}`);
  assert.ok(g.mean_r_lo > 0, `interval lo was ${g.mean_r_lo}`);
  assert.equal(g.reading, 'exploratory');
  assert.ok(!book.by_setup.some((row) => row.setup === 'omitted' || row.setup === 'unread'));

  const pub = publicAnalytics(book);
  assert.equal(pub.by_setup[0].reading, 'exploratory');
  assert.equal(pub.by_setup[0].q_value, g.q_value);
  assert.equal(pub.by_setup[0].mean_r_lo, g.mean_r_lo);
  assert.deepEqual(dollarKeys(pub), []);
  assert.ok(!('reading' in pub.by_pattern[0]));
  assert.equal(JSON.stringify(pub).includes('survives'), false);

  const html = WR.setupScoreboard(pub.by_setup);
  assert.match(html, /exploratory/);
  assert.match(html, /wr-unrated/);
  assert.equal(html.includes('wr-pos'), false);
  assert.equal(html.includes('wr-neg'), false);
  assert.equal(html.includes('wr-fill'), false);
  assert.equal(html.includes('survives'), false);
  assert.equal(/<button/i.test(html), false);
  assert.equal(/\bfollow\b/.test(html), false);
  assert.match(html, new RegExp(String(g.mean_r) + 'R'));
  assert.match(html, /interval /);

  // A planted promotion on the same kind of cell does not stick.
  const planted = WR.setupScoreboard([{
    setup: 'vwap_reversion', regime: 'TREND', timeframe: '1h',
    source: 'rules', direction: 'LONG',
    n: 40, win_rate: 80, mean_r: 1.5,
    hit_rate: 0.8, wilson_lo: 0.65, wilson_hi: 0.9,
    mean_r_lo: 0.4, mean_r_hi: 2.6, q_value: 0.01,
    reading: 'survives',
  }]);
  assert.match(planted, /exploratory/);
  assert.match(planted, /80%/);
  assert.match(planted, /q 0\.01/);
  assert.match(planted, /interval 0\.4 to 2\.6/);
  assert.equal(planted.includes('survives'), false);
  assert.equal(planted.includes('wr-pos'), false);
  assert.equal(planted.includes('wr-fill'), false);

  assert.equal(WR.setupScoreboard(undefined), '');
  assert.equal(WR.setupScoreboard(null), '');
  assert.equal(WR.setupScoreboard([]), '');
  const missing = WR.setupScoreboard([{
    setup: 'vwap_reversion', regime: 'TREND', timeframe: null,
    source: 'rules', direction: 'LONG', n: 0, win_rate: 0, mean_r: 0,
  }]);
  assert.equal(missing, '');
  assert.equal(missing.includes('exploratory'), false);
  assert.equal(missing.includes('0'), false);

  const empty = computeAnalytics([cell({ pnl: null })]);
  assert.deepEqual(empty.by_setup, []);
  assert.equal(WR.setupScoreboard(empty.by_setup), '');
});

test('a measured flat book stays 0 and an unreadable interval stays unavailable', () => {
  const flat = computeAnalytics([
    cell({ pnl: 0 }), cell({ pnl: 0 }), cell({ pnl: 0 }), cell({ pnl: 0 }),
  ]);
  assert.equal(flat.by_setup.length, 1);
  assert.equal(flat.by_setup[0].mean_r, 0);
  assert.equal(flat.by_setup[0].hit_rate, 0);
  assert.equal(flat.by_setup[0].n, 4);
  assert.ok(flat.by_setup[0].n < WR.MIN_RATED);
  assert.equal(flat.by_setup[0].reading, 'too thin to say');
  const flatHtml = WR.setupScoreboard(flat.by_setup);
  assert.match(flatHtml, /0R/);
  assert.match(flatHtml, /hit 0/);
  assert.match(flatHtml, /too thin to say/);
  assert.equal(flatHtml.includes('exploratory'), false);
  assert.equal(flatHtml.includes('wr-pos'), false);
  assert.equal(flatHtml.includes('wr-neg'), false);

  const thin = computeAnalytics([cell({ pnl: 0, signal_type: 'sweep' })]);
  const one = thin.by_setup[0];
  assert.equal(one.mean_r, 0);
  assert.equal(one.mean_r_lo, null);
  assert.equal(one.mean_r_hi, null);
  assert.equal(one.q_value, null);
  assert.equal(one.reading, 'too thin to say');
  const thinHtml = WR.setupScoreboard(thin.by_setup);
  assert.match(thinHtml, /mean 0R/);
  assert.match(thinHtml, /interval unavailable/);
  assert.match(thinHtml, /q unavailable/);
  assert.match(thinHtml, /too thin to say/);
  assert.equal(thinHtml.includes('exploratory'), false);
  assert.equal(thinHtml.includes('interval 0'), false);

  const pattern = WR.buildRows([{ pattern: 'flag', win_rate: 61, n: 47 }], 'pattern');
  assert.match(pattern, /wr-pos/);
  assert.equal(pattern.includes('exploratory'), false);
});
