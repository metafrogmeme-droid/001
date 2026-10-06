'use strict';
/**
 * The closed-bar rule (#505) reaches every chart read, on the clock of the
 * read that fetched the rows.
 *
 * Four findings from the sixty-PR review, one rule:
 *
 *  - The Arena position card's chip row called vwap()/structure() with no
 *    timeframe, so a forming break printed BOS/CHoCH chips and moved the VWAP
 *    chip over a chart (drawn with the timeframe) that showed neither.
 *  - closedCandles compared against the RENDER time. The pages hold candles
 *    for 120 s and the server for 15 s, so a bar that was forming when it was
 *    read became a "closed" bar once its period elapsed, its close being the
 *    price at the read. The clock is now the venue's own stamp of the read
 *    (Bitget's `requestTime`, relayed by the server), kept with the rows.
 *  - The "too few bars" footnote counted the forming bar the floors did not
 *    see.
 *  - `1M` (a month) was folded to `1m` (a minute).
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { codeOnly } = require('./helpers/code_only');

global.window = global.window || {};
require(path.join(__dirname, '..', 'public', 'js', 'chartread.js'));
const CR = global.window.RCChartRead;
const M = require(path.join(__dirname, '..', 'public', 'js', 'chart-read-model.js'));

const H = 3600000;
const T0 = Date.UTC(2026, 8, 15, 0, 0, 0);

/** 1h rows whose last bar, opened at `open`, breaks the last swing high. */
function formingBreak() {
  const pts = [[0, 100], [5, 90], [12, 110], [19, 95], [26, 115], [32, 108], [38, 114.9]];
  const rows = [];
  for (let w = 0; w < pts.length - 1; w++) {
    const [i0, p0] = pts[w], [i1, p1] = pts[w + 1];
    for (let i = i0; i < i1; i++) {
      const p = p0 + (p1 - p0) * ((i - i0) / (i1 - i0));
      rows.push([String(T0 + i * H), String(p), String(p + 0.5), String(p - 0.5), String(p), '1']);
    }
  }
  const [iL, pL] = pts[pts.length - 1];
  rows.push([String(T0 + iL * H), String(pL), String(pL + 0.5), String(pL - 0.5), String(pL), '1']);
  const open = T0 + iL * H + H;
  rows.push([String(open), '114.9', '116.8', '114.8', '116.5', '1']);
  return { rows, open };
}

/** One named function out of an inline page script, sliced by its braces. */
function pageFunction(file, name) {
  const src = fs.readFileSync(path.join(__dirname, '..', 'public', file), 'utf8');
  const code = codeOnly(src);
  const sig = new RegExp('(?:async\\s+)?function ' + name + '\\(', 'g');
  const all = code.match(sig) || [];
  assert.equal(all.length, 1, `${name} must be defined once in ${file}`);
  sig.lastIndex = 0;
  const m = sig.exec(code);
  let i = code.indexOf('{', m.index), depth = 0, q = null;
  for (; i < code.length; i++) {
    const c = code[i];
    if (q) { if (c === '\\') i++; else if (c === q) q = null; continue; }
    if (c === '"' || c === "'" || c === '`') { q = c; continue; }
    if (c === '{') depth++;
    else if (c === '}' && --depth === 0) break;
  }
  assert.ok(depth === 0 && i < code.length, `${name} did not close`);
  return src.slice(m.index, i + 1);
}

// ─── the Arena position card's chips ────────────────────────────────────────
function loadReadChips() {
  const ctx = {
    window: { RCChartRead: CR },
    esc: (s) => String(s),
    T: (_k, d) => d,
    fill: (s, o) => String(s).replace(/\{(\w+)\}/g, (_, k) => (o && o[k] != null ? o[k] : '')),
  };
  vm.createContext(ctx);
  vm.runInContext(pageFunction('arena.html', 'readChips') + '\nthis.readChips = readChips;', ctx);
  return ctx.readChips;
}

test('the Arena chips read the closed series, on the rows\' read clock', () => {
  const readChips = loadReadChips();
  const { rows, open } = formingBreak();
  const parsed = CR.parseCandles(rows);
  // Read while the bar was forming: no BOS chip, and the VWAP chip is the
  // closed series' VWAP, the one the chart under it draws.
  const live = readChips({}, parsed, null, '1h', open + 1000);
  assert.doesNotMatch(live, /BOS/, 'a forming close is not a break of structure');
  const closedVw = CR.vwap(parsed, { gran: '1h', now: open + 1000 });
  assert.match(live, new RegExp('VWAP (above|below) [+]?' + closedVw.dist_pct.toFixed(2).replace('.', '\\.') + '%'));
  // Read after it closed: the same rows are a break.
  const settled = readChips({}, parsed, null, '1h', open + H);
  assert.match(settled, /BOS ↑/);
  // And the chart's own read agrees with the chips on both clocks.
  assert.ok(!CR.tvSpec(parsed, { gran: '1h', now: open + 1000 }).legend.includes('BOS'));
});

// ─── the clock is the read, not the render ──────────────────────────────────
test('readClock is the venue stamp, else the moment the page received the rows', () => {
  const at = 1791294153168;
  assert.equal(CR.readClock({ requestTime: at, data: [] }, 5), at);
  assert.equal(CR.readClock({ requestTime: String(at) }, 5), at);
  // No stamp, a seconds value, or garbage: the page's own receipt time.
  assert.equal(CR.readClock({ data: [] }, 1791294160000), 1791294160000);
  assert.equal(CR.readClock({ requestTime: 1791294153 }, 1791294160000), 1791294160000);
  assert.equal(CR.readClock({ requestTime: 'soon' }, 1791294160000), 1791294160000);
  assert.equal(CR.readClock(null, 1791294160000), 1791294160000);
});

test('a bar that was forming when it was read stays dropped after its period elapses', () => {
  const { rows, open } = formingBreak();
  const parsed = CR.parseCandles(rows);
  // Read 10 s into the bar; the render happens long after the close. The
  // read's clock keeps it a partial bar; the render's would promote it.
  const readAt = open + 10000;
  assert.equal(CR.closedCandles(parsed, { gran: '1h', now: readAt }).length, parsed.length - 1);
  assert.equal(CR.structure(parsed, { gran: '1h', now: readAt }).bos, false);
  assert.equal(CR.closedCandles(parsed, { gran: '1h', now: open + H }).length, parsed.length);
});

function loadGetChartData(answers) {
  const calls = [];
  const ctx = {
    window: { RCChartRead: CR },
    chartData: {}, patCache: {}, TF_INSIGHT: {},
    Date, Promise,
    RC: {
      fetchJSON: async (url) => {
        calls.push(url);
        const a = answers(url);
        if (a instanceof Error) throw a;
        return a;
      },
    },
  };
  vm.createContext(ctx);
  vm.runInContext(pageFunction('arena.html', 'getChartData') + '\nthis.getChartData = getChartData;', ctx);
  return { getChartData: ctx.getChartData, ctx, calls };
}

test('the Arena keeps each candle set\'s read clock through its cache and its stale fallback', async () => {
  const stamp = Date.now() - 90000;       // the venue read these 90 s ago
  const rows = [[String(stamp - 1000), '1', '1', '1', '1', '1']];
  let candles = { ok: true, data: { code: '00000', requestTime: stamp, data: rows } };
  const { getChartData, ctx } = loadGetChartData((url) =>
    /candles/.test(url) ? candles : { ok: true, data: {} });
  const first = await getChartData('BTCUSDT', '1h');
  assert.equal(first.readAt, stamp, 'the venue stamp, not the receipt time');
  const cached = await getChartData('BTCUSDT', '1h');
  assert.equal(cached.readAt, stamp, 'a cache hit keeps the clock of its read');
  // Expire the entry; the candle refetch fails, the insight answers: the
  // stale candles come back with THEIR clock, not the refetch's.
  ctx.chartData['BTCUSDT|1h'].at = 0;
  candles = { ok: false, data: null };
  const stale = await getChartData('BTCUSDT', '1h');
  assert.deepEqual(stale.candles, rows);
  assert.equal(stale.readAt, stamp);
  // A fresh read takes the fresh stamp.
  ctx.chartData['BTCUSDT|1h'].at = 0;
  candles = { ok: true, data: { code: '00000', requestTime: stamp + 60000, data: rows } };
  assert.equal((await getChartData('BTCUSDT', '1h')).readAt, stamp + 60000);
});

// ─── the dashboard: its renderer block, run ─────────────────────────────────
/** The dashboard's chart-read block (painter + the modal's candle reader). */
function dashboardBlock(over) {
  const raw = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  const a = raw.indexOf('// ── the chart read: one renderer, both charts ─');
  const b = raw.indexOf('// ── the chart read: renderer end ─');
  assert.ok(a > 0 && b > a, 'the chart-read block lost its markers');
  const boxes = {};
  const ctx = Object.assign({
    window: { ChartReadModel: M, RCChartRead: CR },
    document: { getElementById: (id) => (boxes[id] === undefined ? null : boxes[id]) },
    Object, String, Number, Array, isFinite, Math, Date, Promise,
    T: (_k, en) => en,
    esc: (s) => String(s),
  }, over || {});
  vm.runInNewContext(raw.slice(a, b) + '\n;globalThis.__x = { paintChartRead, candleReader };', ctx);
  return { fn: ctx.__x, boxes };
}

test('the symbol modal keeps each candle set\'s read clock through its cache and its stale fallback', async () => {
  let clock = Date.UTC(2026, 9, 6, 12);
  const stamp = clock - 90000;              // the venue read these 90 s ago
  const rows = [[String(stamp - 1000), '1', '1', '1', '1', '1']];
  let answer = { ok: true, data: { code: '00000', requestTime: stamp, data: rows } };
  const urls = [];
  const d = dashboardBlock({
    Date: { now: () => clock },
    fetchJSON: async (url) => { urls.push(url); if (answer instanceof Error) throw answer; return answer; },
  });
  const read = d.fn.candleReader('BTCUSDT');
  const first = await read('1h');
  assert.equal(first.rows, rows);
  assert.equal(first.now, stamp, 'the venue stamp, not the receipt time');
  assert.match(urls[0], /^\/api\/market\/candles\/BTCUSDT\?granularity=1h&limit=120$/);
  // Inside the 120 s cache: no refetch, and the clock of the read it holds.
  clock += 60000;
  const cached = await read('1h');
  assert.equal(urls.length, 1);
  assert.equal(cached.now, stamp, 'a cache hit keeps the clock of its read');
  // Past the cache, the refetch fails: the stale rows with THEIR clock.
  clock += 61000;
  answer = new Error('ECONNRESET');
  const stale = await read('1h');
  assert.equal(urls.length, 2);
  assert.equal(stale.rows, rows);
  assert.equal(stale.now, stamp, 'a stale fallback keeps the clock of its read');
  // A fresh read takes the fresh stamp.
  answer = { ok: true, data: { code: '00000', requestTime: clock - 500, data: rows } };
  assert.equal((await read('1h')).now, clock - 500);
});

test('the dashboard paints the chart read on the rows\' read clock', () => {
  const Q = 900000;
  const rows = Array.from({ length: 5 }, (_, i) =>
    [String(T0 + i * Q), '100', '101', '99', '100.5', '10']);
  const lastOpen = T0 + 4 * Q;
  const d = dashboardBlock();
  d.boxes.chartRead = { innerHTML: '' };
  // Read while the last bar was forming: 4 closed bars, under the floor.
  d.fn.paintChartRead('chartRead', rows, { venue: 'Bitget', gran: '15min', now: lastOpen + 1000 });
  assert.match(d.boxes.chartRead.innerHTML, /too few bars to read — 4 on record/);
  assert.doesNotMatch(d.boxes.chartRead.innerHTML, /VWAP/);
  // The same rows read after it closed: 5 bars, and a VWAP chip.
  d.fn.paintChartRead('chartRead', rows, { venue: 'Bitget', gran: '15min', now: lastOpen + Q });
  assert.match(d.boxes.chartRead.innerHTML, /VWAP/);
  assert.doesNotMatch(d.boxes.chartRead.innerHTML, /too few bars/);
});

test('every chart read a page makes with a timeframe carries the read clock', () => {
  // The call sites are held to the shape as well: a drawInto that names a
  // timeframe names the clock, and every chart-read paint names it. The
  // painter and the modal's candle reader are driven above; the call sites
  // sit inside handlers no test runs.
  const argsOf = (code, at) => {
    let i = code.indexOf('(', at), depth = 0, q = null;
    const start = i;
    for (; i < code.length; i++) {
      const c = code[i];
      if (q) { if (c === '\\') i++; else if (c === q) q = null; continue; }
      if (c === '"' || c === "'" || c === '`') { q = c; continue; }
      if (c === '(') depth++;
      else if (c === ')' && --depth === 0) break;
    }
    return code.slice(start, i + 1);
  };
  let seen = 0;
  for (const file of ['js/dashboard.js', 'arena.html']) {
    const code = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'public', file), 'utf8'));
    for (const re of [/\bdrawInto\(/g, /\bpaintChartRead\(/g]) {
      let m;
      while ((m = re.exec(code))) {
        const args = argsOf(code, m.index);
        if (/^\(\s*id\s*,/.test(args)) continue;              // the definition
        if (/paintChartRead/.test(m[0]) || /\bgran\s*:/.test(args)) {
          seen++;
          assert.match(args, /\bnow\s*:/, `${file}: ${m[0]} with a timeframe and no read clock: ${args.slice(0, 120)}`);
        }
      }
    }
  }
  assert.ok(seen >= 5, `expected the five timeframe reads, saw ${seen}`);
});

// ─── the footnote ───────────────────────────────────────────────────────────
test('"too few bars" counts the closed bars the floors were measured on', () => {
  const Q = 900000;
  const rows = Array.from({ length: 5 }, (_, i) =>
    [String(T0 + i * Q), '100', '101', '99', '100.5', '10']);
  const lastOpen = T0 + 4 * Q;
  // Forming: 4 closed bars, under vwap's floor of 5; the footnote says 4.
  const forming = M.chartRead(rows, CR, { venue: 'Bitget', gran: '15min', now: lastOpen + 1000 });
  assert.equal(forming.items.length, 0);
  assert.equal(forming.thinN, 4);
  // Closed: 5 bars meet the floor and there is a reading, no footnote.
  const closed = M.chartRead(rows, CR, { venue: 'Bitget', gran: '15min', now: lastOpen + Q });
  assert.ok(closed.items.length > 0);
  assert.equal(closed.thinN, null);
});

// ─── a month is not a minute ────────────────────────────────────────────────
test('a monthly bar closes at the end of its month, in the venue\'s own day', () => {
  // UTC-anchored month (ccxt `1M`).
  const sep = Date.UTC(2026, 8, 1);
  assert.equal(CR.periodEnd(sep, '1M'), Date.UTC(2026, 9, 1));
  assert.equal(CR.periodEnd(sep, '3M'), Date.UTC(2026, 11, 1));
  // Bitget's plain `1M` opens at 00:00 UTC+8: 16:00 UTC the day before.
  const octUtc8 = Date.UTC(2026, 8, 30, 16);
  assert.equal(CR.periodEnd(octUtc8, '1M'), Date.UTC(2026, 9, 31, 16));
  // A zone west of UTC carries across a 31-day month the same way.
  const octWest = Date.UTC(2026, 9, 1, 5);
  assert.equal(CR.periodEnd(octWest, '1M'), Date.UTC(2026, 10, 1, 5));
  // `1m` is still one minute, and nothing is guessed for an unknown period.
  assert.equal(CR.periodEnd(sep, '1m'), sep + 60000);
  assert.equal(CR.periodEnd(sep, '1mo'), 0);

  const bars = [[String(Date.UTC(2026, 7, 1)), '1', '2', '0.5', '1.5', '1'],
                [String(sep), '1.5', '2', '1', '1.8', '1']];
  const parsed = CR.parseCandles(bars);
  assert.equal(CR.closedCandles(parsed, { gran: '1M', now: Date.UTC(2026, 8, 20) }).length, 1,
    'three weeks into September the September bar is forming');
  assert.equal(CR.closedCandles(parsed, { gran: '1M', now: Date.UTC(2026, 9, 1) }).length, 2);
});
