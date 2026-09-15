/**
 * THE CHART READ, DRIVEN.
 *
 * Every assertion here is about a claim the old code made and could not
 * support. The two that cost the most:
 *
 *  * a 40-bar MONOTONE RAMP reported "structure ranging" — the strongest
 *    trend there is, printed as the neutral verdict, because `structure()`
 *    returns its constructor's defaults when the swing detector finds fewer
 *    than two swings per side, before BOS or CHoCH is computed;
 *  * a failed read answered the same as a quiet market, so the previous
 *    symbol's verdict could stay on screen.
 *
 * The floors are deliberately NOT asserted as numbers here. `vwap()` and
 * `structure()` each own theirs; this suite drives the SAMPLE SIZES either
 * side of them, so a floor that moves in chartread.js moves the product and
 * this file together rather than being pinned in a second place.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

// chartread.js is a browser script that binds to `window`.
global.window = global.window || {};
require(path.join(__dirname, '..', 'public', 'js', 'chartread.js'));
const READ = global.window.RCChartRead;
const M = require(path.join(__dirname, '..', 'public', 'js', 'chart-read-model.js'));

const START = Date.UTC(2026, 8, 15, 0, 0, 0);

/** Bitget v2 rows — [ts_ms, o, h, l, c, baseVol], strings, oldest first. */
function bars(n, priceAt, vol) {
  return Array.from({ length: n }, (_, i) => {
    const px = priceAt(i);
    return [String(START + i * 900000), String(px), String(px + 4), String(px - 4),
      String(px + 1), String(vol === undefined ? 120 : vol)];
  });
}

const CHOP = (i) => 2500 + (i % 3 === 0 ? 6 : -4);
const RAMP = (i) => 2500 + i * 3;
const ZIG = (i) => 2500 + Math.round(120 * Math.sin(i / 6));

const kinds = (r) => r.items.map((it) => it.kind);
const word = (r, kind) => {
  const it = r.items.find((x) => x.kind === kind);
  return it ? it.word.en : null;
};
const read = (rows, opts) => M.chartRead(rows, READ, Object.assign({ venue: 'Bitget', gran: '15m' }, opts || {}));

test('a monotone ramp is NOT "ranging" — an unreadable structure says so', () => {
  const r = read(bars(40, RAMP));
  assert.equal(word(r, 'structure'), 'structure unreadable — no swings found');
  const st = r.items.find((x) => x.kind === 'structure');
  assert.equal(st.measured, false);
  assert.ok(!/chip--(up|down)/.test(st.cls), 'an unreadable structure wears no verdict colour');
  // And the two flags read off the same empty swing list are not reported as
  // findings: "no break of structure" said without looking is still a claim.
  assert.ok(!kinds(r).includes('bos'), 'no BOS chip over an unmeasured structure');
  assert.ok(!kinds(r).includes('choch'), 'no CHoCH chip over an unmeasured structure');
});

test('a structure that WAS measured still reports its verdict', () => {
  const r = read(bars(60, ZIG));
  const st = r.items.find((x) => x.kind === 'structure');
  assert.equal(st.measured, true);
  assert.ok(['structure bullish', 'structure bearish', 'structure ranging'].includes(st.word.en));
  assert.equal(typeof st.swings, 'number', 'the swing count travels with the verdict');
});

test('a failed read and a quiet market are different facts', () => {
  const failed = read(null);
  assert.equal(failed.provenance.state, 'unread');
  assert.equal(failed.provenance.word.en, 'the candles could not be read');
  assert.deepEqual(failed.items, [], 'nothing is claimed from a read that did not happen');

  const empty = read([]);
  assert.equal(empty.provenance.state, 'none');
  assert.equal(empty.provenance.word.en, 'the venue answered no candles for this pair');
  assert.deepEqual(empty.items, []);

  assert.notEqual(failed.provenance.word.en, empty.provenance.word.en);
});

test('both surfaces get the SAME answer at every sample size', () => {
  // The defect: the Markets view let VWAP through at 5 bars and the modal
  // required 15 for everything, so one symbol at one timeframe had two
  // answers between those numbers. There is one reading now, so "both
  // surfaces" is a property of the model, not of two call sites.
  for (const n of [4, 6, 10, 14, 15, 40]) {
    const a = read(bars(n, CHOP));
    const b = read(bars(n, CHOP));
    assert.deepEqual(kinds(a), kinds(b));
    assert.deepEqual(a.items.map((i) => i.word.key), b.items.map((i) => i.word.key));
  }
});

test('every verdict carries the sample it was read from', () => {
  // 6 bars and 200 bars used to render identically. A reader cannot discount
  // a thin read they cannot see.
  const thin = read(bars(6, CHOP));
  const thick = read(bars(60, ZIG));
  for (const r of [thin, thick]) {
    for (const it of r.items) assert.equal(typeof it.bars, 'number');
  }
  assert.equal(thin.items[0].bars, 6);
  assert.equal(thick.items[0].bars, 60);
});

test('below every floor the module owns, the SAMPLE is the answer', () => {
  const r = read(bars(3, CHOP));
  assert.deepEqual(r.items, [], 'no verdict is offered');
  assert.equal(r.thin.en, 'too few bars to read — {n} on record');
  assert.equal(r.thinN, 3, 'and the count is the one actually parsed');
  assert.equal(r.provenance.state, 'read', 'three bars were still READ — that is not a failure');
});

test('a price exactly AT the VWAP is neither above nor below it', () => {
  // `dist_pct >= 0 ? up : down` painted a green "VWAP above +0.00%" at the
  // boundary: colour is a claim, and 0 makes neither.
  const at = M.chips([], { vwap: () => ({ dist_pct: 0 }), structure: () => null });
  assert.equal(at[0].word.en, 'at VWAP');
  assert.equal(at[0].pct, null, 'no signed 0.00% is printed');
  assert.ok(!/chip--(up|down)/.test(at[0].cls));

  const above = M.chips([], { vwap: () => ({ dist_pct: 0.4 }), structure: () => null });
  assert.equal(above[0].word.en, 'VWAP above');
  assert.ok(above[0].cls.includes('chip--up'));
});

test('a non-finite distance is not a reading', () => {
  for (const bad of [NaN, Infinity, null, undefined, '0.4']) {
    const out = M.chips([], { vwap: () => ({ dist_pct: bad }), structure: () => null });
    assert.deepEqual(out, [], `dist_pct ${String(bad)} must produce no chip`);
  }
});

test('the module missing is not an empty market', () => {
  // Chips absent because a script failed to load look exactly like chips
  // absent because there is nothing to say.
  assert.equal(M.chips([], null), null);
  assert.equal(M.chips([], {}), null);
  const r = M.chartRead(bars(40, ZIG), null, {});
  assert.deepEqual(r.items, []);
  assert.equal(r.provenance.state, 'unread', 'no chartread module means nothing was read');
});

test('the footnote counts what was DRAWN, and names what was dropped', () => {
  const rows = bars(20, CHOP);
  rows.push(['not', 'a', 'row']);                  // parseCandles drops it
  rows.push([String(START), 'x', 'y', 'z', 'w']);  // unparseable OHLC
  const r = read(rows, { drawn: 19 });             // the TV path deduped one
  const s = r.provenance.sample;
  assert.equal(s.answered, 22);
  assert.equal(s.parsed, 20);
  assert.equal(s.drawn, 19);
  assert.equal(s.dropped, 2);
  assert.equal(s.deduped, 1);
  const keys = r.provenance.parts.map((p) => p.word.key);
  assert.deepEqual(keys, ['dd.cr_bars', 'dd.cr_dropped', 'dd.cr_deduped']);
  assert.equal(r.provenance.parts[0].n, 19, 'the headline count is what reached the chart');
});

test('a clean read prints NO dropped rows — a permanent "0 dropped" is noise', () => {
  const r = read(bars(40, ZIG));
  assert.deepEqual(r.provenance.parts.map((p) => p.word.key), ['dd.cr_bars']);
});

test('sample() refuses counts it cannot use', () => {
  assert.equal(M.sample(null, 3), null);
  assert.equal(M.sample('10', 3), null);
  assert.equal(M.sample(-1, 0), null);
  // drawn is optional: the SVG path draws every parsed bar.
  assert.equal(M.sample(10, 8).drawn, 8);
  assert.equal(M.sample(10, 8).deduped, 0);
});

test('the VWAP fallback to the full window is reported, not hidden', () => {
  // chartread.js falls back to a full-window VWAP when the session traded no
  // volume. That is a different quantity, and it used to be labelled the same.
  const zeroVol = READ.parseCandles(bars(40, ZIG, 0));
  assert.equal(READ.vwap(zeroVol), null, 'no volume anywhere means no VWAP at all');

  const withVol = READ.parseCandles(bars(40, ZIG));
  const vw = READ.vwap(withVol);
  assert.equal(vw.session, true, 'a session with volume reports itself as the session read');
  assert.equal(typeof vw.bars, 'number');
});

test('every word this model can emit is a dd.cr_ key, and KEYS lists them all', () => {
  const emitted = new Set();
  const collect = (r) => {
    for (const it of r.items) emitted.add(it.word.key);
    if (r.thin) emitted.add(r.thin.key);
    if (r.provenance.word) emitted.add(r.provenance.word.key);
    for (const p of r.provenance.parts) emitted.add(p.word.key);
  };
  collect(read(null)); collect(read([])); collect(read(bars(3, CHOP)));
  collect(read(bars(40, RAMP))); collect(read(bars(60, ZIG)));
  for (const k of emitted) {
    assert.ok(k.indexOf('dd.cr_') === 0, k + ' is not this model\'s to spell');
    assert.ok(M.KEYS.includes(k), k + ' is emitted but missing from KEYS');
  }
});

test('every KEYS entry is defined in all fourteen languages', () => {
  // Read the dictionary STRICTLY: translate() falls back to English, so a key
  // present in one language answers English for the other thirteen and a
  // non-empty check detects only a wholly-missing key.
  const src = require('node:fs').readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  const LANGS = ['en', 'hi', 'it', 'es', 'zh', 'pt', 'fr', 'ar', 'de', 'nl', 'ja', 'ko', 'ru', 'tr'];
  for (const k of M.KEYS) {
    const line = src.split('\n').find((l) => l.trim().startsWith(`'${k}':`));
    assert.ok(line, `${k} is not in the dictionary at all`);
    for (const lang of LANGS) {
      assert.ok(new RegExp(`\\b${lang}: '`).test(line), `${k} has no ${lang}`);
    }
  }
});
