'use strict';
/**
 * A chart is a claim about price. These are the false ones it must refuse.
 *
 * The signals table published `entry 63,200 · stop 61,900 / target 66,000` and
 * left the reader to imagine where price sat between them. The charts that did
 * exist — the deep-scan pattern minis — were drawn once, marked
 * `data-mini-done`, never updated, and failed like this:
 *
 *     .catch(() => { el.style.display = 'none'; });
 *
 * An unreadable chart DISAPPEARED. A reader cannot tell that from a chart that
 * was never offered, and neither could a test, because the only outcome was a
 * side effect on a DOM node. `buildSignalChart` returns a value for every
 * outcome, so each failure below can be planted and read.
 *
 * The interesting tests are the ones where drawing SOMETHING would have been
 * easy and wrong:
 *
 *   - a zero span (`(hi - lo) || 1`) renders a market that never moved as an
 *     ordinary chart
 *   - a missing `entry` arriving as 0 puts the entry line at zero and rescales
 *     every candle into a stripe at the top
 *   - `progress` defaulting to 0 reads as "sitting exactly on the stop" — the
 *     most alarming reading available — for a signal whose stop is unknown
 *   - a half-parsing feed charted from the numeric half is a partial total
 *     presented as a whole one
 */

const test = require('node:test');
const assert = require('node:assert');

const SC = require('../public/js/signal-chart');

/** t, o, h, l, c — the Bitget row shape the market route returns. */
function bars(n, opts = {}) {
  const base = opts.base ?? 100;
  const step = opts.step ?? 1;
  const out = [];
  for (let i = 0; i < n; i++) {
    const o = base + i * step;
    const c = o + (opts.flat ? 0 : step * 0.5);
    out.push([1700000000000 + i * 3600000, o, Math.max(o, c) + 0.4, Math.min(o, c) - 0.4, c]);
  }
  return out;
}

const GEO = { entry: 105, stop: 95, target: 125, direction: 'LONG' };

// ── it draws when it can ─────────────────────────────────────────────────

test('a normal signal renders a chart with all three levels', () => {
  const r = SC.buildSignalChart(bars(30), GEO, { label: 'BTC' });
  assert.equal(r.ok, true, r.reason);
  assert.equal(r.levels, 3);
  assert.match(r.svg, /sc-lvl--entry/);
  assert.match(r.svg, /sc-lvl--stop/);
  assert.match(r.svg, /sc-lvl--target/);
  assert.match(r.svg, /sc-mark/);
  assert.equal(r.bars, 30);
});

test('the live mark is the last close, not the first or the mean', () => {
  const rows = bars(10);
  const r = SC.buildSignalChart(rows, GEO);
  assert.equal(r.mark, rows[rows.length - 1][4]);
});

test('rows are sorted, so an out-of-order feed does not invert the chart', () => {
  const rows = bars(10);
  const shuffled = [rows[4], rows[0], rows[9], rows[2], rows[7], rows[1], rows[3], rows[5], rows[6], rows[8]];
  const r = SC.buildSignalChart(shuffled, GEO);
  assert.equal(r.ok, true);
  assert.equal(r.mark, rows[9][4], 'the newest bar by TIMESTAMP must be the mark');
});

// ── the failures that used to be invisible ───────────────────────────────

test('no candles at all is NO_CANDLES, not an empty chart', () => {
  for (const empty of [null, undefined, []]) {
    const r = SC.buildSignalChart(empty, GEO);
    assert.equal(r.ok, false);
    assert.equal(r.reason, SC.REASONS.NO_CANDLES);
  }
});

test('rows that are all unparseable is UNREADABLE, not NO_CANDLES', () => {
  // The distinction matters to the operator: "the venue returned nothing" and
  // "the venue returned something we cannot read" are different faults.
  const junk = [['a', 'b', 'c', 'd', 'e'], [null, null, null, null, null], [1]];
  const r = SC.buildSignalChart(junk, GEO);
  assert.equal(r.ok, false);
  assert.equal(r.reason, SC.REASONS.UNREADABLE);
  assert.equal(r.dropped, 3);
});

test('too few bars is its own reason rather than a two-point line', () => {
  const r = SC.buildSignalChart(bars(2), GEO);
  assert.equal(r.ok, false);
  assert.equal(r.reason, SC.REASONS.TOO_FEW);
});

test('a ZERO-SPAN market is refused, not rendered with a fabricated axis', () => {
  // `(hi - lo) || 1` is the banned shape. With every OHLC identical and no
  // levels, it invents a one-unit range and paints a perfectly ordinary chart
  // of a market that did not move.
  const flat = [];
  for (let i = 0; i < 12; i++) flat.push([1700000000000 + i * 60000, 50, 50, 50, 50]);
  const r = SC.buildSignalChart(flat, {});
  assert.equal(r.ok, false);
  assert.equal(r.reason, SC.REASONS.FLAT);
});

test('a partial parse is REPORTED, not silently charted from what survived', () => {
  const rows = bars(20);
  rows[3] = ['x', 'x', 'x', 'x', 'x'];
  rows[11] = [1700000000000, 5, 1, 9, 5];      // high below low: not a bar
  const r = SC.buildSignalChart(rows, GEO);
  assert.equal(r.ok, true);
  assert.equal(r.dropped, 2, 'the caller cannot warn about what it is not told');
  assert.equal(r.bars, 18);
});

// ── absent levels are absent, not zero ───────────────────────────────────

test('a missing level is OMITTED rather than drawn at zero', () => {
  // Drawn at 0, the entry line lands at the bottom of the axis and rescales
  // every candle into a stripe at the top — a chart that is wrong about the
  // market because it was wrong about a level nobody published.
  const r = SC.buildSignalChart(bars(20), { stop: 95, target: 125 });
  assert.equal(r.ok, true);
  assert.equal(r.levels, 2);
  assert.doesNotMatch(r.svg, /sc-lvl--entry/);
});

test('no levels at all still charts the price', () => {
  const r = SC.buildSignalChart(bars(20), {});
  assert.equal(r.ok, true);
  assert.equal(r.levels, 0);
  assert.match(r.svg, /sc-mark/);
});

test('a level far outside the candles widens the axis instead of clipping', () => {
  const withTarget = SC.buildSignalChart(bars(20), { target: 100000 });
  assert.equal(withTarget.ok, true);
  assert.match(withTarget.svg, /sc-lvl--target/,
    'a target far above the range must still be visible — clipping it hides '
    + 'exactly how far away it is');
});

// ── progress: the number that must not default ───────────────────────────

test('progress is null when ANY leg is missing, never 0', () => {
  // 0 renders as "sitting exactly on the stop". For a signal whose stop we do
  // not know, that is the most alarming reading available, invented.
  assert.equal(SC.progress({ entry: 105, target: 125 }, 110), null);
  assert.equal(SC.progress({ entry: 105, stop: 95 }, 110), null);
  assert.equal(SC.progress({ stop: 95, target: 125 }, null), null);
  assert.equal(SC.progress({}, 110), null);
});

test('progress is null when stop and target are the same price', () => {
  assert.equal(SC.progress({ entry: 100, stop: 100, target: 100 }, 100), null);
});

test('progress is a real 0 when the mark is genuinely AT the stop', () => {
  // The other half of the rule: a measured 0 is a measurement. It must not be
  // suppressed just because absent also wanted to be 0.
  assert.equal(SC.progress({ entry: 105, stop: 95, target: 125 }, 95), 0);
  assert.equal(SC.progress({ entry: 105, stop: 95, target: 125 }, 125), 1);
  assert.equal(SC.progress({ entry: 105, stop: 95, target: 125 }, 105), 1 / 3);
});

test('progress is clamped, so a mark past target does not read as 140%', () => {
  assert.equal(SC.progress({ entry: 105, stop: 95, target: 125 }, 200), 1);
  assert.equal(SC.progress({ entry: 105, stop: 95, target: 125 }, 10), 0);
});

// ── what a reader is told when there is no chart ─────────────────────────

test('every reason has its own sentence', () => {
  const seen = new Set();
  for (const reason of Object.values(SC.REASONS)) {
    const msg = SC.messageFor(reason);
    assert.ok(msg && msg.length > 8, `${reason} has no message`);
    assert.ok(!seen.has(msg), `${reason} reuses another reason's sentence: ${msg}`);
    seen.add(msg);
  }
});

test('the placeholder occupies the slot instead of hiding it', () => {
  // The defect this file exists for. `display:none` removes the evidence that
  // a chart was ever meant to be there.
  const html = SC.placeholderHtml(SC.REASONS.UNREADABLE);
  assert.doesNotMatch(html, /display\s*:\s*none/);
  assert.match(html, /data-sc-reason="unreadable"/);
  assert.match(html, /could not be read/);
  assert.notEqual(html.trim(), '');
});

test('an unknown reason still says something rather than rendering blank', () => {
  const html = SC.placeholderHtml('something_new');
  assert.match(html, /Chart unavailable/);
  assert.notEqual(html.trim(), '');
});

// ── the accessible label is the chart, for some readers ──────────────────

test('the aria label states progress only when it is known', () => {
  const known = SC.buildSignalChart(bars(20), GEO, { label: 'BTC' });
  assert.match(known.svg, /% of the way from stop to target/);

  const partial = SC.buildSignalChart(bars(20), { entry: 105 }, { label: 'BTC' });
  assert.doesNotMatch(partial.svg, /% of the way/,
    'a progress percentage was announced for a signal with no stop or target');
  assert.match(partial.svg, /progress unknown/);
});

test('the aria label names only the levels that exist', () => {
  const r = SC.buildSignalChart(bars(20), { stop: 95 }, { label: 'ETH' });
  assert.match(r.svg, /stop 95/);
  assert.doesNotMatch(r.svg, /entry \d/);
  assert.doesNotMatch(r.svg, /target \d/);
});

test('the label is escaped, so a symbol cannot inject markup', () => {
  const r = SC.buildSignalChart(bars(20), GEO, { label: '<img src=x onerror=alert(1)>' });
  assert.doesNotMatch(r.svg, /<img/);
  assert.match(r.svg, /&lt;img/);
});

// ── the wiring, asked of the source ──────────────────────────────────────
//
// #999: a card was built, source-scanned, shipped, and rendered ZERO times in
// production because the callback that fed it passed prose where the lookup
// expected symbols. Code being PRESENT and code being REACHED are different
// facts and no unit test of a pure function can tell them apart. These four
// check the connection, which is the part a renderer's own tests cannot.

const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only');

// COMMENTS BLANKED. This file's own comments name the strings it forbids —
// `display:none` appears three times in prose above — and a scan that reads
// them cannot tell the warning from the defect. Four false failures in this
// repo came from exactly that.
const DASH = codeOnly(
  fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));
const HTML = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('the dashboard actually mounts the signal charts', () => {
  assert.match(DASH, /\.then\(mountSignalCharts\)/,
    'the signal stream renders without mounting charts — the slots would sit '
    + 'as skeletons forever');
  assert.match(DASH, /data-sc-sym=/, 'no signal row emits a chart slot');
  assert.match(DASH, /RCSignalChart/, 'nothing in the dashboard calls the renderer');
});

test('the page loads the renderer before the dashboard that uses it', () => {
  const chart = HTML.indexOf('/js/signal-chart.js');
  const dash = HTML.indexOf('/js/dashboard.js');
  assert.ok(chart > -1, 'signal-chart.js is never loaded — window.RCSignalChart is undefined');
  assert.ok(dash > -1);
  assert.ok(chart < dash,
    'signal-chart.js loads after dashboard.js; both are `defer`, which preserves '
    + 'document order, so the renderer must come first');
});

test('a failed chart is never hidden at the call site either', () => {
  // The renderer refuses to emit `display:none` (asserted above). That is only
  // half of it: the CALLER could still hide the slot, which is precisely what
  // the deep-scan minis do — `.catch(() => { el.style.display = 'none'; })`.
  const drawFn = DASH.slice(DASH.indexOf('async function _drawSignalChart'));
  const body = drawFn.slice(0, drawFn.indexOf('\n  }\n') + 4);
  assert.ok(body.length > 100, 'the draw function moved; this scan is reading nothing');
  assert.doesNotMatch(body, /style\.display\s*=\s*['"]none['"]/,
    'the signal chart hides its slot on failure — the exact defect this '
    + 'renderer exists to replace');
  assert.match(body, /placeholderHtml/,
    'the failure path does not render the honest placeholder');
});

test('the candle cache is keyed by timeframe, not by symbol alone', () => {
  // Two callers now ask for different granularities. Keyed on the symbol only,
  // the first 4h answer is served to every later 1h request for its whole TTL
  // — a chart of the wrong timeframe, silently. Same shape as a cooldown
  // written under one spelling of a symbol and read under another.
  assert.doesNotMatch(DASH, /_miniCandles\.get\(sym\)/,
    'the candle cache is keyed on the bare symbol again');
  assert.match(DASH, /_miniCandles\.get\(key\)/);
});
