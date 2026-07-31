'use strict';
// "we need % whit the leverage now its not good" — the operator, looking at a
// decision picture that read:
//
//     entry 100.660      tp 100.914      sl 100.252
//
// Three prices and no percentages, so the reader does the arithmetic. Do it
// and you get +0.25% to target, which looks like nothing worth taking. At the
// 10x this account actually runs, it is +2.5% of margin — ten times the
// number the chart implied, on the only axis the account feels.
//
// Two numbers live here and they are not interchangeable:
//
//   the PRICE move    (entry -> level) is a market fact;
//   the POSITION move (that times leverage) is what the balance does.
//
// Leverage is applied ONLY where it is known. A signal row carries none —
// nobody has sized that trade yet — so it gets the price move alone, and the
// label says which one you are looking at by carrying the multiplier or not.
// An assumed 1x printed as though it were measured would be the invented
// number this repo keeps removing from its surfaces.
//
// And the sign is relative to the DIRECTION, not the price axis: a short's
// take-profit sits BELOW entry and is still a gain. Rendering that negative
// would invert the meaning of every short on the board.

const test = require('node:test');
const assert = require('node:assert');

const CR = require('../public/js/chartread.js');
const { levelMove } = CR;

test('the price move is reported when no leverage is known', () => {
  const mv = levelMove(100.660, 100.914, 'LONG', null);
  assert.strictEqual(mv.leverage, null);
  assert.strictEqual(mv.leveraged, null);
  assert.match(mv.text, /^\+0\.25%$/,
    'no multiplier in the label, because none was supplied');
});

test('leverage multiplies the move when it IS known', () => {
  const mv = levelMove(100.660, 100.914, 'LONG', 10);
  assert.ok(Math.abs(mv.pct - 0.2524) < 0.001, 'price move is unchanged');
  assert.ok(Math.abs(mv.leveraged - 2.524) < 0.01, 'position move is 10x it');
  assert.match(mv.text, /@10×/,
    'the multiplier is part of the label — without it a 2.5% price move and '
    + 'a 0.25% move at 10x render identically');
});

test('an unknown leverage is never defaulted to 1x', () => {
  // The two must be distinguishable. "1x" is a claim about position sizing
  // that nobody made.
  const unknown = levelMove(100, 101, 'LONG', null);
  const explicit = levelMove(100, 101, 'LONG', 1);
  assert.strictEqual(unknown.leverage, null);
  assert.strictEqual(explicit.leverage, 1);
  assert.notStrictEqual(unknown.text, explicit.text);
});

test('junk leverage is treated as unknown, not as zero', () => {
  for (const bad of [0, -3, NaN, 'ten', undefined, null]) {
    const mv = levelMove(100, 101, 'LONG', bad);
    assert.strictEqual(mv.leverage, null, `leverage: ${String(bad)}`);
    assert.ok(Math.abs(mv.pct - 1) < 1e-9,
      'the price move survives a bad leverage — it does not depend on it');
  }
});

test("a short's take-profit is a GAIN", () => {
  const mv = levelMove(100.660, 100.252, 'SHORT', 10);
  assert.ok(mv.pct > 0, 'below entry on a short is profit, not loss');
  assert.match(mv.text, /^\+/);
});

test("a short's stop-loss is a LOSS", () => {
  const mv = levelMove(100.660, 100.914, 'SHORT', 10);
  assert.ok(mv.pct < 0);
  assert.match(mv.text, /^-/);
});

test('long and short read oppositely for the same two prices', () => {
  const l = levelMove(100, 101, 'LONG', 5);
  const s = levelMove(100, 101, 'SHORT', 5);
  assert.ok(l.pct > 0 && s.pct < 0);
  assert.ok(Math.abs(l.pct + s.pct) < 1e-9, 'equal and opposite');
});

test('an unusable entry or level yields nothing rather than a zero', () => {
  // A "0.00%" beside a level we could not price is the same false claim the
  // position card was just cured of.
  for (const [e, p] of [[0, 100], [100, 0], [-1, 100], [100, NaN], [null, 100]]) {
    assert.strictEqual(levelMove(e, p, 'LONG', 10), null,
      `entry=${String(e)} level=${String(p)}`);
  }
});

test('precision adapts so small and large moves both read at a glance', () => {
  assert.match(levelMove(100, 100.25, 'LONG', null).text, /\+0\.25%/);
  assert.match(levelMove(100, 125, 'LONG', null).text, /\+25\.0%/);
});

test('the chart labels carry the move, and entry does not', () => {
  const src = require('fs').readFileSync(
    require('path').join(__dirname, '..', 'public', 'js', 'chartread.js'), 'utf8');
  // entry is the reference point; "+0.00%" beside it would be noise.
  assert.match(src, /level\(opts\.entry, '#e6b03c', '', 'entry'\);/,
    'entry takes no move argument');
  for (const lvl of ['tp', 'sl', 'liq', 'exit']) {
    const re = new RegExp(`level\\(opts\\.${lvl},[^)]*'${lvl}', true\\)`);
    assert.match(src, re, `${lvl} must show what reaching it does`);
  }
});

test('svgChart renders the leveraged label into the SVG', () => {
  // End-to-end through the real renderer: a seam that is never exercised is
  // a seam that can silently stop being wired.
  const candles = [];
  for (let i = 0; i < 40; i++) candles.push([i * 1000, 100, 101, 99.5, 100.5, 10]);
  const parsed = CR.parseCandles(candles);
  const svg = CR.svgChart(parsed, {
    entry: 100.0, tp: 100.5, sl: 99.6, direction: 'LONG', leverage: 10, width: 400,
  });
  assert.match(svg, /tp .*@10×/, 'the tp label must carry the leveraged move');
  assert.match(svg, /sl .*@10×/);
});
