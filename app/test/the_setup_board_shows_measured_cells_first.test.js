'use strict';
/**
 * The setup board's position is decided by the sample floor, not by win rate.
 *
 * #513 took the established colour off every setup cell, and the board's sort
 * put coloured ("rated") rows first, then ordered by win rate. With no row
 * coloured, the public setup board became one band ordered by win rate alone,
 * sliced to six rows: six one-trade 100% cells filled it and every cell with
 * a sample was cut. #519 labelled those rows "too thin to say" and left the
 * order. The file's own comment calls that ordering "exactly the ranking the
 * sample floor exists to refuse".
 *
 * Driven from resolved rows through the real analytics and the real board.
 */
const test = require('node:test');
const assert = require('node:assert/strict');

const { computeAnalytics } = require('../lib/signal_analytics');
const WR = require('../public/js/winrate-bar');

function book(setup, n, wins) {
  const rows = [];
  for (let i = 0; i < n; i++) {
    rows.push({
      pnl: i < wins ? 1 : -1, signal_type: setup, regime: 'TREND', timeframe: '1h',
      source: 'rules', direction: 'LONG', pattern: 'breakout', symbol: 'BTC/USDT', confidence: 0.7,
    });
  }
  return rows;
}

/** The rendered rows, top to bottom: setup name, sample, percent. */
function board(rows) {
  const a = computeAnalytics(rows, { registrations: [] });
  const html = WR.setupScoreboard(a.by_setup);
  const out = [];
  const re = /<span class="wr-label">([^<]*)<\/span>(?:<span class="wr-n">×(\d+)<\/span>)?<b class="wr-val[^"]*">([^<]*)<\/b>/g;
  let m;
  while ((m = re.exec(html))) out.push({ setup: m[1].split(' · ')[0], n: Number(m[2]), pct: m[3] });
  return { cells: a.by_setup, rows: out };
}

test('seven one-trade wins do not push three measured cells off the board', () => {
  const rows = [
    ...book('meas_a', 20, 11), ...book('meas_b', 15, 9), ...book('meas_c', 12, 7),
  ];
  for (let i = 0; i < 7; i++) rows.push(...book('thin_' + i, 1, 1));
  const { cells, rows: shown } = board(rows);
  assert.equal(cells.length, 10, 'the analytics published every cell');
  assert.equal(shown.length, 6);
  assert.deepEqual(shown.slice(0, 3).map((r) => r.setup), ['meas_b', 'meas_c', 'meas_a'],
    'the measured cells lead, by rate among themselves');
  for (const r of shown.slice(3)) assert.equal(r.n, 1);
});

test('under the floor a rate is not a ranking: the larger sample stands higher', () => {
  const rows = [
    ...book('big50', 48, 24), ...book('mid73', 11, 8),
    ...book('thin89', 9, 8), ...book('two100', 2, 2), ...book('one100', 1, 1),
  ];
  const order = board(rows).rows.map((r) => r.setup + '×' + r.n);
  assert.deepEqual(order, ['mid73×11', 'big50×48', 'thin89×9', 'two100×2', 'one100×1']);
});

test('a pattern column keeps its order: coloured rows first, by rate', () => {
  // Pattern groups have no setup mark; the sample floor is their whole rule
  // and over it they are coloured. The floor and the colour agree there, so
  // the band is the same one it was.
  const groups = [
    { pattern: 'flag', n: 2, win_rate: 100 },
    { pattern: 'wedge', n: 30, win_rate: 40 },
    { pattern: 'cup', n: 12, win_rate: 75 },
    { pattern: 'doji', n: 5, win_rate: 80 },
  ];
  const out = WR.buildRows(groups, 'pattern');
  const labels = [...out.matchAll(/<span class="wr-label">([^<]*)<\/span>/g)].map((m) => m[1]);
  assert.deepEqual(labels, ['cup', 'wedge', 'doji', 'flag']);
  assert.match(out, /wr-val wr-pos">75%/);
});

test('the board is the same whatever order the cells arrive in', () => {
  // The analytics hand cells over by sample size; the board must not depend
  // on that. A comparator that orders the floor band one way and the thin
  // band another would answer differently per arrival order.
  const rows = [
    ...book('meas_a', 20, 11), ...book('meas_b', 15, 9), ...book('meas_c', 12, 7),
    ...book('thin_a', 3, 3), ...book('thin_b', 1, 1), ...book('thin_c', 9, 5),
  ];
  const cells = computeAnalytics(rows, { registrations: [] }).by_setup;
  const labels = (list) => [...WR.setupScoreboard(list).matchAll(/<span class="wr-label">([^ <]*)/g)].map((m) => m[1]);
  const want = ['meas_b', 'meas_c', 'meas_a', 'thin_c', 'thin_a', 'thin_b'];
  const orders = [cells, [...cells].reverse()];
  for (let k = 1; k < cells.length; k++) orders.push([...cells.slice(k), ...cells.slice(0, k)]);
  for (const list of orders) assert.deepEqual(labels(list), want, list.map((c) => c.setup).join(','));
});
