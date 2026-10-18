'use strict';
/**
 * A position's entry and mark are printed with the decimals their size needs.
 *
 * THE FINDING (Pi app, 2026-10-18). A FARTCOINUSDT short read "entry 0.15 ·
 * mark 0.15 · 0.00" while its chart, on the same screen, said 0.14945: the
 * positions list printed prices with vusdt(), an amount's two decimals, so a
 * coin under a dollar looked flat while it moved. The signal cards on the
 * same screen already printed levels with embed-row.js's price(); the list
 * uses the same rule now (miniapp-view.js price()), and this holds the two
 * copies to one answer, since the Farcaster Mini App loads only one of them.
 *
 * Below a cent both take the chart's own places (tv-chart.js precisionFor),
 * so 0.0000123 does not read 0.000012.
 */
const test = require('node:test');
const assert = require('node:assert/strict');

global.window = global.window || {};
const M = require('../public/js/miniapp-view.js');
const R = require('../public/js/embed-row.js');
const TV = require('../public/js/tv-chart.js');

// Fixed places for the size, so an entry and a mark line up digit for digit;
// only an all-zero fraction is dropped (embed-row.js's rule since it began).
const TABLE = [
  [0.14945, '0.149450'], [0.9811, '0.981100'], [7.72, '7.7200'], [2475.93, '2475.93'],
  [64012.5, '64012.50'], [100, '100'], [0.0000123, '0.000012300'], [0.00000001234, '0.0000000123'],
  ['0.14945', '0.149450'], [-0.5, '-0.500000'], [null, '—'], [undefined, '—'], ['n/a', '—'],
];

test('a price is printed to its size, not to an amount\'s two decimals', () => {
  for (const [v, want] of TABLE) assert.equal(M.price(v), want, String(v));
  assert.equal(M.vusdt(0.14945), '0.15', 'the amount formatter is unchanged: amounts keep two decimals');
});

test('the positions list and the signal cards print a price the same way', () => {
  for (const [v] of TABLE) assert.equal(M.price(v), R.price(v), String(v));
  // Hundreds of sizes either side of every boundary.
  for (let e = -9; e <= 6; e++) {
    for (const k of [1, 1.0001, 2.5, 9.99999]) {
      const v = k * Math.pow(10, e);
      assert.equal(M.price(v), R.price(v), String(v));
    }
  }
});

test('below a cent, a price keeps as many places as the chart draws', () => {
  for (const v of [0.009, 0.0000123, 0.00000001234]) {
    const places = (M.price(v).split('.')[1] || '').length;
    assert.ok(places <= TV.precisionFor(v), `${v}: ${places} places, the chart draws ${TV.precisionFor(v)}`);
    assert.ok(places > 6, `${v} kept only six places`);
  }
});

test('the positions list prints entry and mark with the price rule', () => {
  const html = M.positionHtml({ id: 9, symbol: 'FARTCOINUSDT', direction: 'SHORT', leverage: 2,
    entry: 0.14945, mark: 0.1481, margin: 200, pnl: 1.94 });
  assert.match(html, /<span>entry 0\.149450<\/span>/);
  assert.match(html, /<span>mark 0\.148100<\/span>/);
  assert.match(html, /<span>200\.00 margin<\/span>/, 'the margin is an amount and keeps two decimals');
  assert.match(M.positionHtml({ id: 1, symbol: 'X', entry: 1, mark: null, margin: 10 }), /mark unavailable/);
});
