/**
 * ONE CHART READ, TWO CHARTS — driven where reachability is the claim, and
 * scanned only where a drive cannot cheaply reach.
 *
 * The Markets view and the symbol modal each built the same four chips with
 * their own sample gates. What a scan CAN prove is that neither holds a
 * private copy any more; what only a DRIVE can prove is the half that cost
 * the most — that a failed read now CLEARS the box instead of leaving the
 * previous symbol's "VWAP above +0.31% · structure bullish · BOS ↑" standing
 * beside the new symbol's error panel.
 *
 * `#chartRead` had exactly two touchers in the whole tree: the container and
 * that one writer, whose write sat inside `if (rows && rows.length)`. So
 * nothing else could ever have cleared it.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const { codeOnly } = require(path.join(__dirname, 'helpers', 'code_only.js'));

global.window = global.window || {};
require(path.join(__dirname, '..', 'public', 'js', 'chartread.js'));
const READ = global.window.RCChartRead;
const M = require(path.join(__dirname, '..', 'public', 'js', 'chart-read-model.js'));

const DASH = path.join(__dirname, '..', 'public', 'js', 'dashboard.js');
const RAW = fs.readFileSync(DASH, 'utf8');
const src = () => codeOnly(RAW);

/** The renderer block, executed with the helpers it reads. */
function renderer(over) {
  const a = RAW.indexOf('// ── the chart read: one renderer, both charts ─');
  const b = RAW.indexOf('// ── the chart read: renderer end ─');
  assert.ok(a > 0 && b > a,
    'the chart-read renderer lost its markers; this harness slices between them');
  const boxes = {};
  const ctx = Object.assign({
    window: { ChartReadModel: M, RCChartRead: READ },
    document: {
      getElementById: (id) => (boxes[id] === undefined ? null : boxes[id]),
    },
    Object, String, Number, Array, isFinite, Math,
    T: (k, en) => en,
    esc: (s) => String(s).replace(/[&<>"]/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])),
  }, over || {});
  vm.runInNewContext(RAW.slice(a, b)
    + '\n;globalThis.__x = { paintChartRead, crSay, crWords };', ctx);
  return { fn: ctx.__x, boxes, ctx };
}

const START = Date.UTC(2026, 8, 15, 0, 0, 0);
const bars = (n, f) => Array.from({ length: n }, (_, i) => {
  const px = f(i);
  return [String(START + i * 900000), String(px), String(px + 4), String(px - 4), String(px + 1), '120'];
});
const ZIG = (i) => 2500 + Math.round(120 * Math.sin(i / 6));
const RAMP = (i) => 2500 + i * 3;

test('a FAILED read clears the previous symbol\'s verdict', () => {
  const r = renderer();
  r.boxes.chartRead = { innerHTML: '' };
  // Symbol A reads fine and paints a verdict.
  r.fn.paintChartRead('chartRead', bars(60, ZIG), { venue: 'Bitget', gran: '15m' });
  const painted = r.boxes.chartRead.innerHTML;
  assert.ok(/structure/.test(painted), 'symbol A painted a structure verdict: ' + painted);

  // Symbol B's read FAILS. Nothing about A may survive.
  r.fn.paintChartRead('chartRead', null, { venue: 'Bitget', gran: '15m' });
  const after = r.boxes.chartRead.innerHTML;
  assert.ok(!/structure/.test(after), 'A\'s verdict is still on screen: ' + after);
  assert.ok(!/VWAP/.test(after), 'A\'s VWAP is still on screen: ' + after);
  assert.ok(/could not be read/.test(after), 'and B says what happened: ' + after);
});

test('a read that THREW clears it too — a crash is not a quiet market', () => {
  const r = renderer();
  r.boxes.chartRead = { innerHTML: '<span class="chip chip--up">VWAP above +9.99%</span>' };
  r.ctx.window.ChartReadModel = { W: M.W, chartRead: () => { throw new Error('boom'); } };
  r.fn.paintChartRead('chartRead', bars(60, ZIG), {});
  assert.equal(r.boxes.chartRead.innerHTML, '', 'nothing stale, and nothing claimed');
});

test('an absent box is not an error, and an absent model clears rather than keeps', () => {
  const r = renderer();
  assert.doesNotThrow(() => r.fn.paintChartRead('nope', bars(20, ZIG), {}));
  r.boxes.chartRead = { innerHTML: '<span>stale</span>' };
  r.ctx.window.ChartReadModel = null;
  r.fn.paintChartRead('chartRead', bars(20, ZIG), {});
  assert.equal(r.boxes.chartRead.innerHTML, '');
});

test('the footnote states the sample, and the ramp is not called "ranging"', () => {
  const r = renderer();
  r.boxes.chartRead = { innerHTML: '' };
  r.fn.paintChartRead('chartRead', bars(40, RAMP), { venue: 'Bitget', gran: '15m' });
  const html = r.boxes.chartRead.innerHTML;
  assert.ok(/40 bars/.test(html), 'the bar count is on the card: ' + html);
  assert.ok(/15m/.test(html), 'and the timeframe');
  assert.ok(/no swings found/.test(html), 'an unreadable structure says so: ' + html);
  assert.ok(!/structure ranging/.test(html), 'and is never the neutral verdict');
});

test('the modal asks for its own extra footnote rather than spelling it', () => {
  const r = renderer();
  r.boxes.symReadChips = { innerHTML: '' };
  r.fn.paintChartRead('symReadChips', bars(60, ZIG), { gran: '4h', levelsFrom4h: true });
  assert.ok(/levels &amp; waves from the 4h read/.test(r.boxes.symReadChips.innerHTML));
  // and the Markets view, which has no such second read, does not claim one
  r.boxes.chartRead = { innerHTML: '' };
  r.fn.paintChartRead('chartRead', bars(60, ZIG), { gran: '15m' });
  assert.ok(!/4h read/.test(r.boxes.chartRead.innerHTML));
});

test('a value out of the record is ESCAPED — a granularity is not markup', () => {
  const r = renderer();
  r.boxes.chartRead = { innerHTML: '' };
  r.fn.paintChartRead('chartRead', bars(60, ZIG), { gran: '<img src=x onerror=1>' });
  const html = r.boxes.chartRead.innerHTML;
  assert.ok(!html.includes('<img src=x'), 'must not become a tag');
  assert.ok(html.includes('&lt;img src=x'), 'it is shown as the text it is');
});

test('NEITHER call site keeps a private copy of the verdicts', () => {
  const s = src();
  for (const shape of ['RCChartRead.vwap(', 'RCChartRead.structure(']) {
    assert.ok(!s.includes(shape),
      'dashboard.js still computes ' + shape + ' itself — that reading belongs to '
      + 'ChartReadModel, and a byte-identical copy agrees on every fixture until '
      + 'one of them is edited, which is exactly how the two gates drifted');
  }
  // Both charts reach the ONE renderer.
  const calls = [...s.matchAll(/paintChartRead\('([a-zA-Z]+)'/g)].map((m) => m[1]);
  assert.deepEqual(calls.sort(), ['chartRead', 'symReadChips']);
  assert.equal((s.match(/function paintChartRead\(/g) || []).length, 1,
    'one renderer, or it is two renderers again');
});

test('NO sample floor is re-spelled outside chartread.js', () => {
  // `vwap()` answers null under 5 bars and `structure()` under 15, each in
  // its own first line. The two call sites used to restate both — which is
  // why one let VWAP through at 5 and the other withheld everything under 15.
  const model = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'chart-read-model.js'), 'utf8'));
  assert.ok(!/length\s*[<>]=?\s*(5|15)\b/.test(model),
    'the model re-spells a floor chartread.js owns');
  const dash = src();
  const inChips = /parsed\.length\s*[<>]=?\s*15/.test(dash);
  assert.ok(!inChips, 'dashboard.js still gates a verdict on a bar count of its own');
});

test('the renderer spells NO key and picks NO colour', () => {
  // Both anchors are CODE. The first draft ended the slice on the comment
  // that follows the block — and `codeOnly` had already removed it, so
  // `indexOf` answered -1 and the guard failed on its own boundary rather
  // than on anything it was guarding. Same trap the repo records about
  // scanning source: strip comments first, then anchor to what survives.
  const s = src();
  const a = s.indexOf('function crSay(');
  const b = s.indexOf('async function openSymbol(');
  assert.ok(a > 0 && b > a, 'the renderer block moved; this guard must move with it');
  const block = s.slice(a, b);
  const keys = [...block.matchAll(/T\(\s*'([^']+)'/g)].map((m) => m[1]);
  assert.deepEqual(keys, [], 'keys spelled in the renderer are a second vocabulary: ' + keys);
  const colour = [...block.matchAll(/'[^']*chip--(?:up|down|warn|info)[^']*'/g)].map((m) => m[0]);
  assert.deepEqual(colour, [], 'the renderer picks a verdict colour itself: ' + colour);
});

test('the modal can tell a failed candle fetch from an empty one', () => {
  // `candlesAt` answered `[]` for both, so the model could not tell "nothing
  // to show" from "we could not ask".
  const s = src();
  const i = s.indexOf('const candlesAt = async');
  assert.ok(i > 0);
  const body = s.slice(i, s.indexOf('const paintTfRow', i));
  assert.ok(/return ok \? \[\] : null;/.test(body),
    'the failed read and the empty answer must not collapse into one value');
});

test('dashboard.html loads the model before the script that reads it', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  const model = html.indexOf('chart-read-model.js');
  const chartread = html.indexOf('js/chartread.js');
  const dash = html.indexOf('<script src="/js/dashboard.js');
  assert.ok(model > 0, 'the chart-read model is not loaded at all');
  assert.ok(chartread < model, 'the model reads RCChartRead, so chartread.js loads first');
  assert.ok(model < dash, 'dashboard.js reads ChartReadModel at paint time');
});

test('structure() reports whether its verdict was MEASURED', () => {
  // Driven, not scanned: the flag is the whole difference between a read
  // range and a window whose swings the detector never found.
  const ramp = READ.structure(READ.parseCandles(bars(40, RAMP)));
  assert.equal(ramp.measured, false);
  assert.equal(ramp.swings.highs.length, 0);
  assert.equal(ramp.bos, false, 'still the constructor default…');
  assert.equal(ramp.choch, false, '…which is why neither may be reported');

  const zig = READ.structure(READ.parseCandles(bars(60, ZIG)));
  assert.equal(zig.measured, true);
  assert.ok(zig.swings.highs.length >= 2 && zig.swings.lows.length >= 2);
});
