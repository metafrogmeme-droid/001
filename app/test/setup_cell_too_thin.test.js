'use strict';
/**
 * A setup cell under the sample floor reads "too thin to say", with no
 * colour and no bar.
 *
 * The floor is RCWinRate.MIN_RATED, the number setup_expectancy already
 * uses. A cell at that floor, and one above it, does not get the label:
 * it stays exploratory, or survives when it was pre-registered and
 * replicated. A missing cell is omitted. An unreadable n is unavailable,
 * not a sample of 0 and not a 0%.
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

function rows(n, pnl = 1) {
  return Array.from({ length: n }, () => cell({ pnl }));
}

function registration(over = {}) {
  return {
    setup: 'vwap_reversion',
    regime: 'TREND',
    timeframe: '1h',
    source: 'rules',
    direction: 'LONG',
    preregistered: true,
    prospective_lo: 0.4,
    prospective_hi: 1.2,
    ...over,
  };
}

function boardCell(over = {}) {
  return {
    setup: 'vwap_reversion',
    regime: 'TREND',
    timeframe: '1h',
    source: 'rules',
    direction: 'LONG',
    win_rate: 100,
    mean_r: 1,
    hit_rate: 1,
    wilson_lo: 0.7,
    wilson_hi: 1,
    mean_r_lo: 0.2,
    mean_r_hi: 1.8,
    q_value: 0.01,
    ...over,
  };
}

function noColour(html) {
  assert.equal(html.includes('wr-pos'), false);
  assert.equal(html.includes('wr-neg'), false);
  assert.equal(html.includes('wr-fill'), false);
}

test('a cell under the floor reads too thin to say, and the floor does not', () => {
  const underN = WR.MIN_RATED - 1;
  const under = computeAnalytics(rows(underN));
  assert.equal(under.by_setup.length, 1);
  assert.equal(under.by_setup[0].n, underN);
  assert.equal(under.by_setup[0].reading, 'too thin to say');
  const underHtml = WR.setupScoreboard(under.by_setup);
  assert.match(underHtml, /too thin to say/);
  assert.match(underHtml, /100%/);
  assert.equal(underHtml.includes('exploratory'), false);
  assert.equal(underHtml.includes('survives'), false);
  noColour(underHtml);

  const at = computeAnalytics(rows(WR.MIN_RATED));
  assert.equal(at.by_setup[0].n, WR.MIN_RATED);
  assert.equal(at.by_setup[0].reading, 'exploratory');
  const atHtml = WR.setupScoreboard(at.by_setup);
  assert.match(atHtml, /exploratory/);
  assert.equal(atHtml.includes('too thin to say'), false);
  noColour(atHtml);

  const above = computeAnalytics(rows(WR.MIN_RATED + 1));
  assert.equal(above.by_setup[0].n, WR.MIN_RATED + 1);
  assert.equal(above.by_setup[0].reading, 'exploratory');
  const aboveHtml = WR.setupScoreboard(above.by_setup);
  assert.match(aboveHtml, /exploratory/);
  assert.equal(aboveHtml.includes('too thin to say'), false);
  noColour(aboveHtml);

  const pub = publicAnalytics(under);
  assert.equal(pub.by_setup[0].reading, 'too thin to say');
  assert.deepEqual(dollarKeys(pub), []);
});

test('a thin cell that was pre-registered still does not read survives', () => {
  const under = computeAnalytics(rows(WR.MIN_RATED - 1), {
    registrations: [registration()],
  });
  assert.equal(under.by_setup[0].reading, 'too thin to say');
  assert.equal(under.by_setup[0].registrations.length, 1);
  const html = WR.setupScoreboard(under.by_setup);
  assert.match(html, /too thin to say/);
  assert.equal(html.includes('survives'), false);
  assert.equal(html.includes('exploratory'), false);
  noColour(html);

  const at = computeAnalytics(rows(WR.MIN_RATED), {
    registrations: [registration()],
  });
  assert.equal(at.by_setup[0].n, WR.MIN_RATED);
  assert.equal(at.by_setup[0].reading, 'survives');
  const atHtml = WR.setupScoreboard(at.by_setup);
  assert.match(atHtml, /survives/);
  assert.equal(atHtml.includes('too thin to say'), false);
  assert.equal(atHtml.includes('exploratory'), false);
  noColour(atHtml);
});

test('a planted word does not decide the floor', () => {
  const thin = WR.setupScoreboard([boardCell({
    n: WR.MIN_RATED - 1,
    reading: 'exploratory',
    registrations: [registration()],
  })]);
  assert.match(thin, /too thin to say/);
  assert.equal(thin.includes('exploratory'), false);
  assert.equal(thin.includes('survives'), false);
  noColour(thin);

  const thick = WR.setupScoreboard([boardCell({
    n: WR.MIN_RATED,
    reading: 'too thin to say',
  })]);
  assert.match(thick, /exploratory/);
  assert.equal(thick.includes('too thin to say'), false);
});

test('a missing cell is omitted, and an unreadable n is not thin and not 0%', () => {
  const missing = WR.setupScoreboard([boardCell({
    timeframe: null, n: 0, win_rate: 0, mean_r: 0,
  })]);
  assert.equal(missing, '');
  assert.equal(missing.includes('too thin to say'), false);
  assert.equal(missing.includes('0'), false);

  const empty = computeAnalytics([cell({ pnl: null })]);
  assert.deepEqual(empty.by_setup, []);

  for (const n of [null, undefined, '', 'n/a', true, false, -1, Infinity]) {
    const html = WR.setupScoreboard([boardCell({
      n, win_rate: 0, mean_r: null, hit_rate: null,
      wilson_lo: null, wilson_hi: null, mean_r_lo: null, mean_r_hi: null,
      q_value: null,
    })]);
    assert.match(html, /<span class="wr-why">unavailable<\/span>/, `n ${String(n)} was not unavailable`);
    assert.equal(html.includes('too thin to say'), false, `n ${String(n)} was called thin`);
    assert.equal(html.includes('0%'), false, `n ${String(n)} painted 0%`);
    assert.equal(html.includes('exploratory'), false);
    assert.equal(html.includes('survives'), false);
    noColour(html);
  }

  // A measured count of 0 is under the floor. It is not omitted, and the
  // 0% is the rate that was stored, not a stand-in for a missing n.
  const zero = WR.setupScoreboard([boardCell({
    n: 0, win_rate: 0, mean_r: 0, hit_rate: 0,
  })]);
  assert.match(zero, /too thin to say/);
  assert.match(zero, /0%/);
  assert.equal(zero.includes('unavailable'), false);
  noColour(zero);

  const pattern = WR.buildRows(
    [{ pattern: 'flag', win_rate: 100, n: WR.MIN_RATED - 1 }],
    'pattern',
  );
  assert.equal(pattern.includes('too thin to say'), false);
  assert.equal(pattern.includes('wr-pos'), false);
});
