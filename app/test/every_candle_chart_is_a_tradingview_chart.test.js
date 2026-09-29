'use strict';
/**
 * Every candle chart on the site is a TradingView chart, and the honesty rules
 * that decide whether there IS a chart did not move.
 *
 * The Markets view drew with TradingView Lightweight Charts; every chart a
 * reader reaches from a SIGNAL drew its own SVG -- the signal row, the symbol
 * modal, the pattern-read mini, the Arena's three and the embed board. The
 * operator asked for one style everywhere. The SVGs stay as the fallback for a
 * library that failed to load, which is why the reading each renderer draws is
 * ONE function (`readSignal`, `RCChartRead.tvSpec`), driven here against the
 * SVG's own refusals: a flat market, a level at 0 and a half-parsed feed must
 * not get a chart from one renderer and a placeholder from the other.
 *
 * The library itself is a stand-in that records what it was asked to draw.
 * That is the right instrument for what this file claims: which bars, which
 * levels, which options. Whether the library draws them where it should is a
 * browser question, and `tv_charts_render.smoke.test.js` asks it in Chromium.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const SC = require('../public/js/signal-chart.js');
const TV = require('../public/js/tv-chart.js');
const CR = require('../public/js/chartread.js');

const PUB = path.join(__dirname, '..', 'public');
const read = (f) => fs.readFileSync(path.join(PUB, f), 'utf8');

// ── a stand-in library and DOM ─────────────────────────────────────────────

function fakeLib() {
  const charts = [];
  function series(kind, o) {
    const s = {
      kind, o, data: null, lines: [], markers: null, prims: [],
      setData(d) { s.data = d; }, createPriceLine(l) { s.lines.push(l); },
      setMarkers(m) { s.markers = m; }, attachPrimitive(p) { s.prims.push(p); },
      priceToCoordinate(p) { return p; },
    };
    return s;
  }
  const lib = {
    CrosshairMode: { Normal: 0, Magnet: 1, Hidden: 2 },
    createChart(el, opts) {
      const c = {
        el, opts, series: [], removed: false, fitted: false,
        addCandlestickSeries(o) { const s = series('candle', o); c.series.push(s); return s; },
        addLineSeries(o) { const s = series('line', o); c.series.push(s); return s; },
        timeScale() { return { fitContent() { c.fitted = true; } }; },
        remove() { c.removed = true; },
      };
      charts.push(c);
      return c;
    },
  };
  return { lib, charts };
}

function el(cls) {
  const e = {
    className: cls || '', attrs: {}, children: [], isConnected: true, textContent: '',
    style: {}, _html: '',
    classList: { add(c) { e.className += ' ' + c; } },
    setAttribute(k, v) { e.attrs[k] = v; },
    getAttribute(k) { return e.attrs[k]; },
    appendChild(c) { e.children.push(c); return c; },
    get firstElementChild() { return e.children[0] || null; },
    get firstChild() { return e.children[0] || null; },
    get innerHTML() { return e._html; },
    set innerHTML(h) {
      e._html = h; e.children = [];
      const m = /^<div class="([\w-]+)"><\/div>/.exec(h);
      if (m) e.children.push(el(m[1]));
    },
  };
  return e;
}

function withBrowser(lib, fn) {
  const had = { self: global.self, document: global.document, window: global.window };
  global.document = { createElement: () => el() };
  global.self = { RCTVChart: TV, LightweightCharts: lib };
  global.window = global.self;
  try { return fn(); } finally {
    global.self = had.self; global.document = had.document; global.window = had.window;
  }
}

const HOUR = 3600e3;
function rows(n, base = 100, step = 1) {
  return Array.from({ length: n }, (_, i) => {
    const o = base + i * step, c = o + step * 0.6;
    return [String(1.7e12 + i * HOUR), o, Math.max(o, c) + 0.5, Math.min(o, c) - 0.5, c, '10'];
  });
}
const GEO = { entry: 110, stop: 100, target: 125, direction: 'LONG' };

// ── the signal chart: one reading, two renderers ───────────────────────────

test('the TradingView spec refuses exactly what the SVG refuses, for the same reason', () => {
  const flat = Array.from({ length: 10 }, (_, i) => [String(1.7e12 + i * HOUR), 5, 5, 5, 5, '1']);
  const cases = {
    'no candles': [[], GEO],
    'unreadable rows': [[['x', 'y', 'z', 'w', 'v'], [null]], GEO],
    'too few bars': [rows(2), GEO],
    'a flat market with no levels': [flat, {}],
    'an ordinary chart': [rows(30), GEO],
  };
  for (const [name, [r, g]] of Object.entries(cases)) {
    const svg = SC.buildSignalChart(r, g);
    const spec = SC.tvSpec(r, g);
    assert.equal(spec.ok, svg.ok, name);
    assert.equal(spec.reason, svg.reason, name);
  }
});

test('a level the signal does not state is neither a line nor an axis price', () => {
  // `entry` absent used to arrive as 0 and drag the axis to zero; the spec
  // carries the levels that EXIST and nothing standing in for the others.
  const spec = SC.tvSpec(rows(30), { stop: 100, target: 125 });
  assert.equal(spec.ok, true);
  assert.deepEqual(spec.lines.map((l) => l.title), ['stop', 'target']);
  assert.deepEqual(spec.autoscale, [100, 125]);
  assert.ok(!spec.autoscale.includes(0));
});

test('each level keeps the colour that means it, and the entry says nothing about winning', () => {
  const spec = SC.tvSpec(rows(30), GEO);
  const by = Object.fromEntries(spec.lines.map((l) => [l.title, l]));
  assert.equal(by.entry.color, SC.LEVEL_COLOR.entry);
  assert.equal(by.stop.color, SC.LEVEL_COLOR.stop);
  assert.equal(by.target.color, SC.LEVEL_COLOR.target);
  assert.notEqual(by.entry.color, by.target.color);
  assert.notEqual(by.entry.color, by.stop.color);
});

test('the bars are seconds, ascending, and a repeated timestamp is drawn once', () => {
  const r = rows(20);
  const dup = r.concat([r[r.length - 1].slice()]).reverse();
  const spec = SC.tvSpec(dup, GEO);
  const times = spec.bars.map((b) => b.time);
  assert.equal(times.length, 20);
  for (let i = 1; i < times.length; i++) assert.ok(times[i] > times[i - 1]);
  assert.equal(times[0], Math.floor(1.7e12 / 1000));
});

test('the screen-reader sentence is the SVG\'s sentence', () => {
  const spec = SC.tvSpec(rows(30), GEO, { label: 'BTC' });
  const svg = SC.buildSignalChart(rows(30), GEO, { label: 'BTC' });
  assert.ok(svg.svg.includes(spec.aria.replace(/&/g, '&amp;')), spec.aria);
});

test('render draws a compact TradingView chart when the library loaded', () => {
  const { lib, charts } = fakeLib();
  const slot = el('sc-slot');
  const out = withBrowser(lib, () => SC.render(slot, rows(30), GEO, { label: 'BTC' }));
  assert.equal(out.tv, true);
  assert.equal(charts.length, 1);
  assert.equal(slot.firstElementChild.className.split(' ')[0], 'sc-tv');
  // A chart in a list must not take the page's gestures.
  assert.equal(charts[0].opts.handleScroll, false);
  assert.equal(charts[0].opts.handleScale, false);
  const candle = charts[0].series[0];
  assert.deepEqual(candle.lines.map((l) => l.title), ['entry', 'stop', 'target']);
  assert.ok(!slot.innerHTML.includes('<svg'));
});

test('render falls back to the SVG when the library did not load', () => {
  const slot = el('sc-slot');
  const out = withBrowser(null, () => SC.render(slot, rows(30), GEO));
  assert.equal(out.ok, true);
  assert.ok(slot.innerHTML.startsWith('<svg class="sc"'));
});

test('render never mounts a chart for a reading it refuses', () => {
  const { lib, charts } = fakeLib();
  const slot = el('sc-slot');
  const out = withBrowser(lib, () => SC.render(slot, [], GEO));
  assert.equal(out.ok, false);
  assert.equal(charts.length, 0);
  assert.match(slot.innerHTML, /data-sc-reason="no_candles"/);
});

test('a slot redrawn as a placeholder removes the chart it held', () => {
  // The signals list re-renders a row's slot in place: a refresh that finds
  // the candle read failed must not leave the previous chart alive under the
  // placeholder, still subscribed to the page's resize observer.
  const { lib, charts } = fakeLib();
  const slot = el('sc-slot');
  withBrowser(lib, () => {
    SC.render(slot, rows(30), GEO);
    assert.equal(charts.length, 1);
    SC.render(slot, [], GEO);
  });
  assert.equal(charts[0].removed, true, 'the old chart outlived the redraw that replaced it');
  assert.match(slot.innerHTML, /data-sc-reason="no_candles"/);
});

// ── the renderer ───────────────────────────────────────────────────────────

test('the price axis carries the decimals the price needs', () => {
  // The library's default of 2 prints a sub-cent asset's whole axis as 0.00.
  const table = [
    [83901.9, 2], [2500, 2], [14.44, 3], [1.17, 4], [0.38, 5], [0.0000112, 9],
    [1e-12, 10], [0, 2], [-5, 2], [NaN, 2], [null, 2], ['abc', 2],
  ];
  for (const [p, d] of table) assert.equal(TV.precisionFor(p), d, String(p));
});

test('mount formats the axis, and only the stated levels widen it', () => {
  const { lib, charts } = fakeLib();
  const host = el();
  const bars = TV.toBars(CR.parseCandles(rows(20, 0.0000112, 0.0000001)));
  const h = withBrowser(lib, () => TV.mount(host, {
    bars, autoscale: [0.00005],
    overlays: [{ points: [{ time: bars[0].time, value: 1 }], color: '#fff' }],
  }, {}));
  assert.equal(h.ok, true);
  const candle = charts[0].series[0];
  assert.equal(candle.o.priceFormat.precision, 9);
  assert.equal(candle.o.priceFormat.minMove, 1e-9);
  const r = candle.o.autoscaleInfoProvider(() => ({ priceRange: { minValue: 1e-5, maxValue: 2e-5 }, margins: 'm' }));
  assert.deepEqual(r, { priceRange: { minValue: 1e-5, maxValue: 0.00005 }, margins: 'm' });
  // An overlay never widens the axis: a far-away value must not squash the
  // candles into a line.
  const line = charts[0].series[1];
  assert.equal(line.o.autoscaleInfoProvider(), null);
});

test('a chart whose host left the page is removed, and so is the one a redraw replaces', () => {
  const { lib, charts } = fakeLib();
  const bars = TV.toBars(CR.parseCandles(rows(20)));
  withBrowser(lib, () => {
    const a = el(), b = el();
    TV.mount(a, { bars }, {});
    TV.mount(a, { bars }, {});          // the modal's timeframe switch
    assert.equal(charts[0].removed, true, 'the first chart on a host outlived its redraw');
    assert.equal(charts[1].removed, false);
    TV.mount(b, { bars }, {});
    a.isConnected = false;               // the signals list re-rendered
    TV.sweep();
    assert.equal(charts[1].removed, true, 'a chart whose host left the page is still alive');
    assert.equal(charts[2].removed, false);
    TV.release(b);
    assert.equal(charts[2].removed, true);
  });
});

test('mount refuses with a reason rather than drawing an empty chart', () => {
  const { lib } = fakeLib();
  withBrowser(lib, () => {
    assert.equal(TV.mount(el(), { bars: [] }, {}).reason, TV.REASONS.NO_BARS);
    assert.equal(TV.mount(null, { bars: [{}] }, {}).reason, TV.REASONS.NO_HOST);
  });
  assert.equal(TV.mount(el(), { bars: [{}] }, { lib: {} }).reason, TV.REASONS.NO_LIB);
});

test('the options: a list chart takes no gestures, a modal chart leaves vertical scroll to the page', () => {
  const lib = { CrosshairMode: { Normal: 0, Hidden: 2 } };
  const compact = TV.chartOptions(lib, { compact: true });
  assert.equal(compact.handleScroll, false);
  assert.equal(compact.handleScale, false);
  assert.equal(compact.crosshair.mode, 2);
  const modal = TV.chartOptions(lib, { pageScroll: true });
  assert.equal(modal.handleScroll.vertTouchDrag, false);
  assert.equal(modal.handleScroll.mouseWheel, false);
  assert.equal(modal.handleScroll.horzTouchDrag, true);
  assert.equal(modal.handleScale.mouseWheel, false);
  const full = TV.chartOptions(lib, {});
  assert.equal(full.handleScroll, undefined, 'the Markets chart keeps the library default');
  assert.equal(full.layout.attributionLogo, undefined);
  assert.equal(TV.chartOptions(lib, { attributionLogo: false }).layout.attributionLogo, false);
  assert.equal(TV.chartOptions(lib, { timeAxis: false }).timeScale.visible, false);
});

test('the mini: the swing range of the recent bars, or no chart', () => {
  assert.equal(TV.miniSpec(CR.parseCandles(rows(2))).ok, false);
  const flat = Array.from({ length: 10 }, (_, i) => [String(1.7e12 + i * HOUR), 5, 5, 5, 5, '1']);
  assert.equal(TV.miniSpec(CR.parseCandles(flat)).ok, false);
  const s = TV.miniSpec(CR.parseCandles(rows(60)));
  assert.equal(s.ok, true);
  assert.equal(s.bars.length, 44);
  const hi = Math.max(...s.bars.map((b) => b.high)), lo = Math.min(...s.bars.map((b) => b.low));
  assert.deepEqual(s.lines.map((l) => l.price), [hi, lo]);
  assert.ok(s.lines.every((l) => l.axisLabel === false));
});

// ── the decision-picture chart ─────────────────────────────────────────────

test('a level outside the candles is not drawn -- the other symbol\'s levels on this chart', () => {
  const c = CR.parseCandles(rows(60, 100, 0.2));
  const spec = CR.tvSpec(c, { levels: [{ price: 105, kind: 'poc', score: 1 }, { price: 2400, kind: 'pdl', score: 9 }] });
  const titles = spec.lines.map((l) => l.title + '@' + l.price);
  assert.ok(titles.includes('poc@105'), titles);
  assert.ok(!titles.some((t) => t.endsWith('@2400')), titles);
});

test('one selection of the engine\'s levels: the top five by score, inside the window', () => {
  // Both renderers draw what this answers, so the SVG and the TradingView
  // chart cannot show different levels for one symbol.
  const lv = (price, score, kind) => ({ price, score, kind });
  const got = CR.windowLevels([
    lv(101, 1, 'a'), lv(102, 7, 'b'), lv(103, 3, 'c'), lv(104, 9, 'd'),
    lv(105, 5, 'e'), lv(106, 2, 'f'), lv(107, undefined, 'g'), lv(99, 99, 'below'),
    lv(111, 99, 'above'), lv(108, 4, '<b>poc'),
  ], 100, 110);
  assert.deepEqual(got.map((l) => l.label), ['d', 'b', 'e', 'bpoc', 'c'],
    'the levels are not the top five by score inside the window, labels sanitised');
  assert.deepEqual(got.map((l) => l.price), [104, 102, 105, 108, 103]);
  // An unscored level ranks last, so it is drawn only when fewer than five are scored.
  assert.ok(CR.windowLevels([lv(101, undefined, 'x'), lv(102, 1, 'y')], 100, 110)
    .map((l) => l.label).join() === 'y,x');
});

test('one selection of the FVG zones: both edges read and inside, filled ones fainter', () => {
  const got = CR.windowFvgs([
    { top: 104, bottom: 102, kind: 'bull' },
    { top: 106, bottom: 105, kind: 'bearish', filled: true },
    { top: 120, bottom: 115, kind: 'bull' },          // above the window
    { top: null, bottom: 101, kind: 'bull' },         // an edge nobody read
    { top: 109, bottom: 108, kind: 'bull' },          // drawn: the zones above took no slot
    { top: 103, bottom: 102.5, kind: 'bull' },
    { top: 101.5, bottom: 101, kind: 'bull' },        // the fifth drawable: past the cap of four
  ], 100, 110);
  assert.deepEqual(got.map((g) => [g.top, g.bottom]), [[104, 102], [106, 105], [109, 108], [103, 102.5]]);
  assert.equal(got[0].color, 'rgba(47,191,113,.10)');
  assert.equal(got[1].color, 'rgba(224,82,82,.05)', 'a filled bearish gap drawn as an unfilled or bullish one');
});

test('a structure nobody could read is said so, never printed as RANGING', () => {
  // A monotone ramp has no swings, and the SVG's tag printed the constructor's
  // default -- the neutral verdict over the one read that never happened.
  const ramp = CR.parseCandles(rows(40, 100, 1));
  assert.equal(CR.structure(ramp).measured, false);
  const spec = CR.tvSpec(ramp, { title: 'X/USDT · 4h' });
  assert.match(spec.legend, /^X\/USDT · 4h · structure unreadable/);
  assert.ok(!/RANGING/.test(spec.legend));
  const wave = CR.parseCandles(Array.from({ length: 80 }, (_, i) => {
    const o = 100 + i * 0.4 + Math.sin(i / 3) * 6, c = o + 0.3;
    return [String(1.7e12 + i * HOUR), o, Math.max(o, c) + 0.5, Math.min(o, c) - 0.5, c, '10'];
  }));
  const st = CR.structure(wave);
  assert.equal(st.measured, true);
  assert.ok(CR.tvSpec(wave, {}).legend.startsWith(st.structure.toUpperCase()));
});

test('the position\'s levels say what reaching them does, and a far one does not squash the axis', () => {
  const c = CR.parseCandles(rows(60, 100, 0.2));
  const spec = CR.tvSpec(c, { entry: 105, tp: 110, sl: 103, liq: 20, direction: 'LONG', leverage: 10 });
  const by = Object.fromEntries(spec.lines.map((l) => [l.title.split(' ')[0], l]));
  assert.equal(by.entry.title, 'entry');
  assert.match(by.tp.title, /^tp \+47\.6% @10×$/);
  assert.match(by.sl.title, /^sl -19\.0% @10×$/);
  assert.equal(by.liq, undefined, 'a liquidation price 80% away was drawn');
  assert.ok(!spec.autoscale.includes(20));
});

test('swings are drawn from the bar they printed on, and waves where they printed', () => {
  const wave = CR.parseCandles(Array.from({ length: 80 }, (_, i) => {
    const o = 100 + Math.sin(i / 4) * 8, c = o + 0.2;
    return [String(1.7e12 + i * HOUR), o, Math.max(o, c) + 0.5, Math.min(o, c) - 0.5, c, '10'];
  }));
  const st = CR.structure(wave);
  const spec = CR.tvSpec(wave, { vwap: false, waves: [{ label: '1', price: wave[10].h }] });
  const segs = spec.overlays.filter((o) => o.points.length === 2);
  assert.ok(segs.length > 0);
  const last = Math.floor(wave[wave.length - 1].t / 1000);
  for (const s of segs) assert.equal(s.points[1].time, last);
  const sw = st.swings.highs.slice(-1)[0];
  assert.ok(segs.some((s) => s.points[0].time === Math.floor(wave[sw.i].t / 1000) && s.points[0].value === sw.p));
  assert.equal(spec.markers.length, 1);
  assert.equal(spec.markers[0].text, '1');
});

test('drawInto: TradingView when the library loaded, the SVG when not, and one chart per host', () => {
  const { lib, charts } = fakeLib();
  const c = CR.parseCandles(rows(60, 100, 0.2));
  const host = el();
  withBrowser(lib, () => {
    const h = CR.drawInto(host, c, { entry: 105, height: 280 });
    assert.equal(h.ok, true);
    assert.equal(host.style.height, '280px');
    assert.equal(charts.length, 1);
    global.window.LightweightCharts = null;
    const s = CR.drawInto(host, c, { entry: 105 });
    assert.equal(s.svg, true);
    assert.equal(charts[0].removed, true, 'the SVG replaced a chart that stayed alive');
    assert.ok(host.innerHTML.startsWith('<svg class="rc-chart"'));
  });
});

// ── the surfaces ───────────────────────────────────────────────────────────
// Scans, stated as scans: the surfaces are inline in 6k-line browser scripts
// and pages, and the claim is WHICH renderer each one asks.

test('no surface draws its own SVG chart: each asks the renderer that chooses', () => {
  const dash = read('js/dashboard.js');
  const arena = read('arena.html');
  const embed = read('js/embed-signals.js');
  for (const [name, src] of [['dashboard.js', dash], ['arena.html', arena], ['embed-signals.js', embed]]) {
    assert.ok(!/\.svgChart\(/.test(src), `${name} calls RCChartRead.svgChart directly`);
    assert.ok(!/\.buildSignalChart\(/.test(src), `${name} calls RCSignalChart.buildSignalChart directly`);
  }
  assert.equal((arena.match(/CR\.drawInto\(/g) || []).length, 3, 'the Arena has three charts');
  assert.match(dash, /RCChartRead\.drawInto\(chartBox/);
  assert.match(dash, /SC\.render\(el, rows, geo/);
  assert.match(embed, /SC\.render\(el, rows, geo/);
  // The two hand-drawn fallbacks are reached only when the library is absent.
  const mini = dash.slice(dash.indexOf('function _drawMini('), dash.indexOf('function _drawMini(') + 2500);
  assert.ok(mini.indexOf('TV.miniSpec(') > -1 && mini.indexOf('TV.miniSpec(') < mini.indexOf('miniCandleSvg('));
});

test('every page loads the library and the renderer before the code that uses them', () => {
  const order = (src, names) => names.map((n) => src.indexOf(n));
  const dash = read('dashboard.html');
  const d = order(dash, ['/vendor/lightweight-charts', '/js/tv-chart.js', '/js/signal-chart.js', '/js/chartread.js', '/js/dashboard.js']);
  assert.ok(d.every((i) => i > -1), d);
  assert.ok(d[0] < d[1] && d[1] < d[4] && d[2] < d[4] && d[3] < d[4], d);
  const arena = read('arena.html');
  const a = order(arena, ['/vendor/lightweight-charts', '/js/tv-chart.js', '/js/chartread.js']);
  assert.ok(a.every((i) => i > -1) && a[0] < a[1] && a[1] < a[2], a);
  const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'embed.js'), 'utf8');
  const e = order(route, ['/vendor/lightweight-charts', '/js/tv-chart.js', '/js/signal-chart.js', '/js/embed-signals.js']);
  assert.ok(e.every((i) => i > -1) && e[0] < e[1] && e[1] < e[2], e);
});

test('the embed board carries the attribution as a link, since its CSP refuses the logo', () => {
  const embed = read('js/embed-signals.js');
  assert.match(embed, /attributionLogo: false/);
  assert.match(embed, /href="https:\/\/www\.tradingview\.com\/"/);
});

test('the Markets chart: a draw that is no longer the latest touches nothing', () => {
  // The defect this guards: ETH's engine levels drawn on a BTC chart, from a
  // draw that finished its level fetch after the symbol changed.
  const dash = read('js/dashboard.js');
  const start = dash.indexOf('async function drawChart()');
  const body = dash.slice(start, dash.indexOf('async function drawDepth()', start));
  assert.match(body, /const seq = \+\+tvSeq;/);
  const fetchAt = body.indexOf('candleRes = await fetchJSON(');
  const guardAt = body.indexOf('if (!current()) return;', fetchAt);
  assert.ok(fetchAt > -1 && guardAt > fetchAt && guardAt < body.indexOf('await renderPanel('),
    'the stale check must sit between the candle fetch and the panel write');
  // Every write onto the series after an await asks whether this draw is still
  // the chart's, and none writes through the shared name.
  assert.ok(!/tvSeries\.update\(/.test(body), 'the live timer writes through the shared series');
  assert.match(body, /if \(pos && mine\(\)\)/);
  assert.match(body, /if \(!\(Number\(l\.price\) > 0\) \|\| !mine\(\)\) continue;/);
  assert.match(body, /if \(!mine\(\)\) return;\s*tvChart\.timeScale\(\)\.fitContent\(\);/);
  assert.match(body, /tvTimer = setInterval\(async \(\) => \{\s*if \(!mine\(\)\) return;/);
});
