'use strict';
/**
 * The instrument row: symbol, sparkline, mark, move, R and stop chip for one
 * open position — four readings, each with three answers, none of them a
 * number it did not read or a colour it did not earn.
 *
 * It fails if a direction the bot did not state becomes a SHORT (every
 * number on the row is signed by it), if the move cell blames the feed for
 * an entry nobody recorded, if a position with no stop wears "bot-managed",
 * if a malformed candle body reads as "not on the feed", if an empty list
 * from a book nobody read renders as "no open positions", or if the view
 * fetches /api/positions twice for one screen. The model is DRIVEN with a
 * table that includes the decoys the first design's guard could not fail on
 * ('BUY', 'UNKNOWN', '', null); the renderers are sliced out of dashboard.js
 * between two sentinel comments and RUN in a VM with the real moveClass; the
 * two decoration readers are driven against a stubbed fetch.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { codeOnly } = require('./helpers/code_only.js');
const { blockBetween } = require('./helpers/block.js');

const APP = path.join(__dirname, '..');
const M = require('../public/js/instrument-row-model.js');
const i18n = require('../public/js/i18n');
const RAW = fs.readFileSync(path.join(APP, 'public', 'js', 'dashboard.js'), 'utf8');
const SRC = codeOnly(RAW);
const APPJS = fs.readFileSync(path.join(APP, 'public', 'js', 'app.js'), 'utf8');
const CSS = fs.readFileSync(path.join(APP, 'public', 'styles.css'), 'utf8');

// ── the model, driven ───────────────────────────────────────────────────────

const TK = (map) => ({ state: 'read', map });
const BARS = (closes) => ({ state: 'read', rows: closes.map((c, i) => [String(1757800000000 + i * 3600000), '1', '2', '0.5', String(c), '1']) });
const pos = (extra) => ({ symbol: 'ETH/USDT:USDT', pair: 'ETH', direction: 'LONG', entry_price: 100, stop_loss: 90, take_profit: 130,
  quantity: 2, size_usd: 200, leverage: 5, sl_order: 'exchange', sl_protected: true, unprotected: false, sl_unknown: false, ...extra });
const row = (p, mark, bars) => M.instrumentRow(p, mark === undefined ? TK({ ETHUSDT: { lastPr: '110' } }) : mark,
  bars === undefined ? BARS([100, 104, 102, 110]) : bars);

test('a LONG and a SHORT are measured in their own direction, and a measured zero keeps its number', () => {
  const l = row(pos());
  assert.equal(l.side, 'long');
  assert.equal(l.move.pct, 10);
  assert.equal(l.r.value, 1);
  const s = row(pos({ direction: 'SHORT' }));
  assert.equal(s.side, 'short');
  assert.equal(s.move.pct, -10, 'a short is down when the price rose');
  assert.equal(s.r.value, -1);
  const flat = row(pos(), TK({ ETHUSDT: { lastPr: 100 } }));
  assert.equal(flat.move.pct, 0, 'mark exactly at entry is a measured break-even');
  assert.equal(flat.r.value, 0, '0.00R was measured');
  assert.equal(flat.move.why, null);
});

// THE DECOYS. The first design's guard drove LONG and SHORT only, and its
// two-way `isLong()` rendered every other word as a confident, coloured SHORT
// beside a chip that had correctly declined. `side()` shares dirChip's
// vocabulary and DECLINES, and the numbers take the decline as their reason.
test('a direction the bot did not state signs nothing: BUY/SELL are read, everything else declines', () => {
  assert.equal(M.side('BUY'), 'long'); assert.equal(M.side('buy'), 'long'); assert.equal(M.side(' Long '), 'long');
  assert.equal(M.side('SELL'), 'short'); assert.equal(M.side('short'), 'short');
  for (const d of ['UNKNOWN', '', null, undefined, 'HOLD', 'LONGSHORT', 'L', 0, true, {}]) {
    assert.equal(M.side(d), null, `side(${JSON.stringify(d)})`);
    const r = row(pos({ direction: d }));
    assert.equal(r.side, null);
    assert.equal(r.move.pct, null, `${JSON.stringify(d)}: no move without a sign`);
    assert.equal(r.move.why.key, 'dd.ir_move_nodir_t');
    assert.equal(r.r.value, null);
    assert.equal(r.r.why.key, 'dd.ir_r_nodir');
    assert.equal(r.chip.text, null, 'the chip declines');
    assert.equal(r.chip.word.key, 'dd.ir_dir_none');
    assert.equal(r.chip.cls, 'chip muted');
  }
  assert.equal(row(pos({ direction: 'BUY' })).move.pct, 10, 'BUY is a long, as dirChip already says');
});

test('the row and dirChip read one vocabulary: every word answers the same side in both', () => {
  // dirChip lives in app.js and serves every page; the row builds its chip
  // from the model's side(). A two-way pin over a corpus that includes the
  // decoys, so a change to either vocabulary fails here rather than
  // shipping two answers for one row.
  const start = APPJS.indexOf('  function dirChip(direction) {');
  assert.ok(start > 0, 'dirChip is still in app.js');
  const end = APPJS.indexOf('\n  }\n', start) + 4;
  const dirChip = vm.runInNewContext(APPJS.slice(start, end) + '\ndirChip;');
  for (const d of ['LONG', 'long', 'BUY', 'SHORT', 'SELL', 'sell', 'UNKNOWN', '', null, undefined, 'HOLD', 'Long', 'LONGSHORT', 'BUYS']) {
    const html = dirChip(d);
    const chipSide = /chip--up/.test(html) ? 'long' : /chip--down/.test(html) ? 'short' : null;
    assert.equal(M.side(d), chipSide, `direction ${JSON.stringify(d)}: row says ${M.side(d)}, dirChip says ${chipSide}`);
  }
});

test('each absence has its OWN reason, decided where it is decided', () => {
  // entry unread + mark READ: the mark prints and the move says "no entry",
  // never "not on the feed" — a false statement about a read displayed one
  // cell away, reachable on every adopted position (entry recorded as 0.0).
  const a = row(pos({ entry_price: 0 }));
  assert.equal(a.mark.state, 'read'); assert.equal(a.mark.value, 110);
  assert.equal(a.move.pct, null); assert.equal(a.move.why.key, 'dd.ir_move_noentry_t');
  assert.equal(a.r.why.key, 'dd.ir_r_noentry');
  // mark unreadable: the move borrows the MARK's sentence, because the mark
  // IS the missing piece.
  const u = row(pos(), { state: 'unreadable' });
  assert.equal(u.mark.state, 'unreadable'); assert.equal(u.mark.why.key, 'dd.ir_mark_unread_t');
  assert.equal(u.move.why.key, 'dd.ir_mark_unread_t');
  assert.equal(u.r.why.key, 'dd.ir_r_nomark');
  // mark absent (feed answered, symbol not on it): a different sentence.
  const ab = row(pos(), TK({ BTCUSDT: { lastPr: 1 } }));
  assert.equal(ab.mark.state, 'absent'); assert.equal(ab.mark.why.key, 'dd.ir_mark_absent_t');
  assert.notEqual(ab.mark.why.key, u.mark.why.key);
  // a feed that carried the symbol with junk is UNREADABLE — it was meant to be there.
  const junk = row(pos(), TK({ ETHUSDT: { lastPr: 'n/a' } }));
  assert.equal(junk.mark.state, 'unreadable');
  assert.equal(row(pos(), TK({ ETHUSDT: { lastPr: 0 } })).mark.state, 'unreadable', 'a zero price is not a price');
  // no reference symbol at all (a non-USDT quote): absent, never priced as USDT.
  const nu = row(pos({ symbol: 'ETH/USDC', pair: 'ETH' }));
  assert.equal(nu.refSym, ''); assert.equal(nu.mark.state, 'absent'); assert.equal(nu.spark.state, 'absent');
});

test('R is unknown, not zero, without a usable stop — decided before the mark is asked', () => {
  const zero = row(pos({ stop_loss: 0.0 }));             // the paper row builder's spelling of "no stop"
  assert.equal(zero.r.value, null); assert.equal(zero.r.why.key, 'dd.ir_r_nostop'); assert.equal(zero.stop, null);
  const none = row(pos({ stop_loss: null }));
  assert.equal(none.r.why.key, 'dd.ir_r_nostop');
  const at = row(pos({ stop_loss: 100 }));               // a stop AT entry has no risk distance
  assert.equal(at.r.value, null); assert.equal(at.r.why.key, 'dd.ir_r_nostop');
  // ...and that is a fact about the stop, so it wins over an unreadable mark.
  const atNoMark = row(pos({ stop_loss: 100 }), { state: 'unreadable' });
  assert.equal(atNoMark.r.why.key, 'dd.ir_r_nostop', 'a stop at entry is no stop, whatever the mark did');
  assert.equal(row(pos(), { state: 'unreadable' }).r.why.key, 'dd.ir_r_nomark');
  assert.equal(M.rNow(100, 90, 110, 'long').r, 1);
  assert.equal(M.rNow(100, 110, 90, 'short').r, 1);
  assert.equal(M.rNow(100, 90, 95, 'long').r, -0.5);
});

test('the stop chip reads the LEVEL as well as the flags, and the unread case is tested first', () => {
  assert.equal(M.stopState({ sl_unknown: true, unprotected: false, sl_order: 'exchange', stop_loss: 90 }), 'unknown');
  assert.equal(M.stopState({ unprotected: null, sl_order: 'exchange', stop_loss: 90 }), 'unknown');
  assert.equal(M.stopState({ sl_order: 'exchange', stop_loss: 90 }), 'unknown', 'undefined is the other absent');
  assert.equal(M.stopState({ unprotected: true, sl_order: 'manual', stop_loss: 90 }), 'unprotected');
  assert.equal(M.stopState({ unprotected: true, sl_order: 'manual', stop_loss: 0 }), 'unprotected', 'the venue\'s alarm outranks the level');
  // The paper row builder's unconditional stamp over an absent stop.
  assert.equal(M.stopState({ stop_loss: 0.0, unprotected: false, sl_order: 'manual' }), 'no_stop');
  assert.equal(M.stopState({ stop_loss: null, unprotected: false, sl_order: 'exchange' }), 'no_stop');
  assert.equal(M.stopState({ stop_loss: 90, unprotected: false, sl_order: 'exchange' }), 'exchange');
  assert.equal(M.stopState({ stop_loss: 90, unprotected: false, sl_order: 'manual' }), 'managed');
  const chips = ['unknown', 'unprotected', 'no_stop', 'exchange', 'managed'].map((s) => M.stopChip(s));
  assert.equal(new Set(chips.map((c) => c.word.key)).size, 5, 'five states, five words');
  assert.equal(M.stopChip('no_stop').cls, 'chip muted', 'no stop is neither reassuring nor an alarm');
});

test('the sparkline has five states and only one of them draws a line', () => {
  assert.equal(M.sparkFrom({ state: 'unreadable' }).state, 'unreadable');
  assert.equal(M.sparkFrom({ state: 'absent' }).state, 'absent');
  assert.equal(M.sparkFrom(null).state, 'unreadable');
  // A 200 whose body is not an array is UNREADABLE — the mark's own rule —
  // never "not on the reference feed".
  assert.equal(M.sparkFrom({ state: 'read', rows: {} }).state, 'unreadable');
  assert.equal(M.sparkFrom({ state: 'read', rows: 'nope' }).state, 'unreadable');
  assert.equal(M.sparkFrom({ state: 'read', rows: [] }).state, 'absent', 'the feed answered with no candles');
  assert.equal(M.sparkFrom(BARS([5])).state, 'thin');
  assert.equal(M.sparkFrom(BARS([5, 5, 5])).state, 'flat');
  assert.equal(M.sparkFrom(BARS([5, 6, 5])).state, 'read');
  assert.equal(M.sparkFrom({ state: 'read', rows: [['1', '1', '1', '1', 'x'], ['2', '1', '1', '1', '0']] }).state, 'absent', 'junk closes are no closes');
  // The geometry refuses anything a line may not be drawn over.
  assert.equal(M.sparkGeometry([], 120, 28), null);
  assert.equal(M.sparkGeometry([5], 120, 28), null);
  assert.equal(M.sparkGeometry([5, 5], 120, 28), null, 'flat is said, never drawn');
  assert.equal(M.sparkGeometry(null, 120, 28), null);
  const g = M.sparkGeometry([1, 3, 2], 120, 28);
  assert.equal(g.n, 3); assert.match(g.points, /^0,\S+ 60,\S+ 120,\S+$/);
});

test('every sentence the model can emit is distinct, listed, and in all fourteen languages', () => {
  const keys = M.KEYS;
  assert.equal(new Set(keys).size, keys.length);
  for (const k of Object.keys(M.W)) assert.ok(keys.includes(M.W[k].key));
  const extra = ['dd.ir_open', 'dd.e_instr', 'dp.instr', 'aria.ir_spark'];
  const missing = [];
  for (const key of [...keys, ...extra]) {
    assert.ok(i18n.STRINGS[key], `${key} is in the dictionary`);
    for (const { code } of i18n.LANGS) {
      const v = i18n.STRINGS[key][code];
      if (typeof v !== 'string' || !v.trim().length) missing.push(key + '/' + code);
    }
  }
  assert.deepEqual(missing, []);
  assert.equal(i18n.LANGS.length, 14);
  // The one templated sentence keeps its slot in every language.
  for (const { code } of i18n.LANGS) assert.match(i18n.STRINGS['dd.ir_spark_flat'][code], /\{n\}/, code);
});

// ── the renderers, sliced out of dashboard.js and RUN ──────────────────────

function renderers(fetchStub) {
  const block = blockBetween(RAW, '// ── instrument row: renderers ─', '// ── instrument row: renderers end ─', { pad: 60, min: 2000 });
  const mc = SRC.indexOf('  function moveClass(n) {');
  assert.ok(mc > 0, 'moveClass is still in dashboard.js');
  const moveClass = SRC.slice(mc, SRC.indexOf('\n  }\n', mc) + 4);
  const ctx = {
    self: { InstrumentRowModel: M }, InstrumentRowModel: M, Date, Map, Set, JSON, Math, Number, String, Array, Object, isFinite, Promise,
    cache: { tickers: {} },
    fetchJSON: fetchStub || (async () => { throw new Error('no fetch in this test'); }),
    T: (k, en) => `[${k}]`,
    TF: (k, en, map) => `[${k}]`.replace('{sym}', map.sym).replace('{n}', map.n),
    esc: (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])),
    fmtPrice: (n) => '$' + Number(n).toFixed(2),
    signed: (n, d) => (Number(n) >= 0 ? '+' : '') + Number(n).toFixed(d),
    encodeURIComponent,
  };
  vm.runInNewContext(moveClass + '\n' + block + '\n;globalThis.__x = { instrumentRowHtml, instrumentPanelHtml, irWords, irSparkHtml, readRefTickers, readRefBars };', ctx);
  return ctx.__x;
}

test('the renderer lists exactly the keys the model can emit, each as a literal the dictionary sweep can see', () => {
  const R = renderers();
  const listed = Object.keys(R.irWords()).sort();
  assert.deepEqual(listed, [...M.KEYS].sort());
  const block = blockBetween(RAW, '// ── instrument row: renderers ─', '// ── instrument row: renderers end ─', { min: 2000 });
  for (const k of M.KEYS) assert.ok(block.includes(`T('${k}'`), `${k} is a literal T() in the renderer`);
});

test('an unread figure renders as words with its reason and no colour; a read one as a number', () => {
  const R = renderers();
  const W = R.irWords();
  const html = R.instrumentRowHtml(row(pos({ direction: 'UNKNOWN', entry_price: 0, stop_loss: 0 }), TK({ ETHUSDT: { lastPr: 110 } })), W);
  assert.match(html, /ir-mark">\$110\.00</, 'the mark printed');
  assert.match(html, /class="ir-unread ir-move" title="\[dd\.ir_move_noentry_t\]">\[dd\.ir_move_unknown\]/);
  assert.match(html, /class="ir-unread ir-r" title="\[dd\.ir_r_noentry\]">\[dd\.ir_r_unknown\]/);
  assert.ok(!/ir-move (up|down)|ir-r (up|down)/.test(html), 'no colour on an absence');
  assert.ok(!/[-+]\d+\.\d+%|[-+]\d+\.\d+R/.test(html), 'no number for a figure nobody read');
  assert.match(html, /chip muted">\[dd\.ir_dir_none\]/, 'the chip declined');
  assert.match(html, /\[dd\.ir_stop\] \[dd\.ir_none_on_record\]/, 'the level says none on record, never $0.00');
  assert.match(html, /chip muted">\[dd\.ir_stop_none\]/, 'and the chip agrees with the level');
  const ok = R.instrumentRowHtml(row(pos()), W);
  assert.match(ok, /ir-move up">\+10\.00%/); assert.match(ok, /ir-r up">\+1\.00R/);
  assert.match(ok, /chip chip--up">▲ LONG/); assert.match(ok, /chip chip--up">\[dd\.ir_stop_exch\]/);
  assert.match(ok, /<polyline points="/, 'a read window draws its line');
  assert.ok(!/\$200|5×|5x/.test(ok), 'no dollar exposure and no leverage');
  assert.match(ok, /ir-qty num">2 ETH/);
  const down = R.instrumentRowHtml(row(pos({ direction: 'SHORT' })), W);
  assert.match(down, /ir-move down">-10\.00%/); assert.match(down, /ir-r down">-1\.00R/);
  const flat = R.instrumentRowHtml(row(pos(), TK({ ETHUSDT: { lastPr: 100 } })), W);
  assert.match(flat, /ir-move up">\+0\.00%/, 'a measured zero keeps its number and its colour');
});

test('the sparkline draws nothing it did not read, and says which nothing', () => {
  const R = renderers();
  const W = R.irWords();
  const words = (bars) => R.irSparkHtml(row(pos(), undefined, bars), W);
  for (const [bars, key] of [[{ state: 'unreadable' }, 'dd.ir_spark_unread'], [{ state: 'read', rows: {} }, 'dd.ir_spark_unread'],
    [{ state: 'absent' }, 'dd.ir_spark_absent'], [{ state: 'read', rows: [] }, 'dd.ir_spark_absent'],
    [BARS([5]), 'dd.ir_spark_thin']]) {
    const h = words(bars);
    assert.ok(!h.includes('<polyline'), `${key}: no line`);
    assert.ok(!h.includes('<svg'), `${key}: no empty track either`);
    assert.match(h, new RegExp(`ir-spark--none" title="\\[${key.replace('.', '\\.')}\\]">\\[${key.replace('.', '\\.')}\\]`));
  }
  const flat = words(BARS([5, 5, 5]));
  assert.ok(!flat.includes('<polyline'));
  assert.match(flat, /\[dd\.ir_spark_flat\]/);
  const line = words(BARS([5, 6, 4, 7]));
  assert.match(line, /<polyline points="[\d., ]+"/);
  assert.ok(!/ir-spark--line (up|down)/.test(line), 'the line is not coloured: its window is the reference feed\'s, not the position\'s');
  assert.match(line, /aria-label="\[aria\.ir_spark\]"/);
});

test('a book nobody read is a named absence, never "no open positions"; a read empty book is the empty state', () => {
  const R = renderers();
  const tk = TK({});
  assert.match(R.instrumentPanelHtml({ live: true, book_read: false, positions: [] }, tk, {}), /ir-book-unread">\[dd\.ir_book_unread\]/);
  assert.match(R.instrumentPanelHtml({ live: true, positions: [] }, tk, {}), /ir-book-unread/, 'an older bot that says nothing is not a read book');
  assert.equal(R.instrumentPanelHtml({ live: true, book_read: true, positions: [] }, tk, {}), null, 'renderPanel\'s empty state, reachable only from a read book');
  assert.equal(R.instrumentPanelHtml({ live: false, book_read: true, positions: [] }, tk, {}), null);
  const live = R.instrumentPanelHtml({ live: true, book_read: true, positions: [pos()] }, tk, {});
  assert.match(live, /ir-foot small muted">\[dd\.ir_foot_live\]/, 'the book is labelled');
  const paper = R.instrumentPanelHtml({ live: false, book_read: true, positions: [pos()] }, tk, {});
  assert.match(paper, /\[dd\.ir_foot_paper\]/);
  assert.match(paper, /ir-spark--none" title="\[dd\.ir_spark_absent\]/, 'no bars handed in for the symbol is absent');
});

test('the two decoration readers classify a throw, a 502, a non-array 200 and a read differently, and cache only a read', async () => {
  const calls = [];
  const mk = (answer) => async (url, opts) => { calls.push([url, opts]); if (answer instanceof Error) throw answer; return answer; };
  let R = renderers(mk(new Error('socket hang up')));
  // Field by field: objects built in the VM realm have another Object.prototype,
  // so a deep-strict comparison would fail on identity rather than on values.
  let t0 = await R.readRefTickers(6000); assert.equal(t0.state, 'unreadable'); assert.equal(t0.map, null);
  let b0 = await R.readRefBars('ETHUSDT', 6000); assert.equal(b0.state, 'unreadable'); assert.equal(b0.rows, null);
  R = renderers(mk({ ok: false, status: 502, data: { error: 'x' } }));
  assert.equal((await R.readRefTickers(6000)).state, 'unreadable');
  assert.equal((await R.readRefBars('ETHUSDT', 6000)).state, 'unreadable');
  R = renderers(mk({ ok: true, status: 200, data: { data: {} } }));
  assert.equal((await R.readRefTickers(6000)).state, 'unreadable', 'a 200 whose body is not an array is not a read');
  assert.equal((await R.readRefBars('ETHUSDT', 6000)).state, 'unreadable');
  R = renderers(mk({ ok: true, status: 200, data: { data: [['1', '1', '1', '1', '5']] } }));
  const none = await R.readRefBars('', 6000);
  assert.equal(none.state, 'absent', 'no reference symbol is absent'); assert.equal(none.rows, null);
  const first = await R.readRefBars('ETHUSDT', 6000);
  assert.equal(first.state, 'read');
  const n = calls.length;
  await R.readRefBars('ETHUSDT', 6000);
  assert.equal(calls.length, n, 'a read is cached');
  assert.match(calls[calls.length - 1][0], /^\/api\/market\/candles\/ETHUSDT\?granularity=1h&limit=24$/);
  assert.equal(calls[calls.length - 1][1].timeoutMs, 6000, 'the budget the loader states is the one asked for');
  const tk = renderers(mk({ ok: true, status: 200, data: { data: [{ symbol: 'ETHUSDT', lastPr: '110' }] } }));
  const t = await tk.readRefTickers(6000);
  assert.equal(t.state, 'read'); assert.equal(t.map.ETHUSDT.lastPr, '110');
});

// ── wiring ──────────────────────────────────────────────────────────────────

function loaderBody(target) {
  const at = SRC.indexOf(`renderPanel(C('${target}')`);
  assert.ok(at > 0, `the ${target} panel is mounted`);
  let depth = 0, i = SRC.indexOf('(', at);
  for (; i < SRC.length; i++) {
    if (SRC[i] === '(') depth++;
    else if (SRC[i] === ')') { depth--; if (depth === 0) break; }
  }
  return SRC.slice(at, i + 1);
}

test('each view reads /api/positions ONCE and every consumer takes that read', () => {
  const home = SRC.slice(SRC.indexOf('async function renderHome()'), SRC.indexOf('async function renderPortfolio()'));
  const pf = SRC.slice(SRC.indexOf('async function renderPortfolio()'));
  assert.equal((home.match(/fetchJSON\('\/api\/positions'/g) || []).length, 1, 'home');
  assert.equal((pf.match(/fetchJSON\('\/api\/positions'/g) || []).length, 1, 'portfolio');
  assert.equal((SRC.match(/fetchJSON\('\/api\/positions'/g) || []).length, 2, 'nowhere else');
  for (const t of ['cmd', 'hpos', 'instr', 'lpos']) assert.match(loaderBody(t), /positionsRead/, `${t} consumes the shared read`);
  assert.match(home, /const positionsRead = LOGGED_IN \? fetchJSON\('\/api\/positions', \{ timeoutMs: 15000 \}\)\.catch\(\(\) => null\) : null;/);
  assert.match(pf, /const positionsRead = fetchJSON\('\/api\/positions', \{ timeoutMs: 15000 \}\)\.catch\(\(\) => null\);/);
});

test('the instrument panel guards its spine, classifies its decorations, and states its budget arithmetic', () => {
  const body = loaderBody('instr');
  assert.match(body, /const r = await positionsRead;\s*mustRead\(r\);/, 'GUARD on the spine');
  assert.match(body, /readRefTickers\(6000\)/); assert.match(body, /readRefBars\(sym, 6000\)/);
  assert.match(body, /instrumentPanelHtml\(d, tickers, barsBySym\)/);
  assert.match(body, /timeoutMs: 22000/, 'the panel budget covers the serial worst case');
  assert.ok(!/cache\.tickers|_fetchMiniCandles|getTickers\(/.test(body), 'the readers that cannot say "unreadable" are not sources');
  // The arithmetic the budget gate cannot check, stated where the budget is.
  const raw = RAW.slice(RAW.indexOf("renderPanel(C('instr')") - 1200, RAW.indexOf("renderPanel(C('instr')"));
  assert.match(raw, /15000 \+ 6000 = 21000ms/);
  // The other consumers' budgets cover the shared read, and say so.
  assert.match(loaderBody('cmd'), /timeoutMs: 17000/);
  assert.match(loaderBody('hpos'), /timeoutMs: 17000/);
  assert.match(loaderBody('lpos'), /timeoutMs: 17000/);
});

test('the panel is mounted above the protection list, and its model loads before dashboard.js', () => {
  const pf = SRC.slice(SRC.indexOf('async function renderPortfolio()'));
  const instr = pf.indexOf('id="p-instr"'), lpos = pf.indexOf('id="p-lpos"');
  assert.ok(instr > 0 && lpos > instr, 'p-instr sits above p-lpos');
  assert.ok(pf.indexOf("renderPanel(C('instr')") < pf.indexOf("renderPanel(C('lpos')"));
  const html = fs.readFileSync(path.join(APP, 'public', 'dashboard.html'), 'utf8');
  const model = html.indexOf('/js/instrument-row-model.js');
  assert.ok(model > 0 && model < html.indexOf('/js/dashboard.js'));
});

test('the styles let an absence wrap, use the sheet\'s one focus ring, and colour no sparkline', () => {
  // Comments stripped first: a comment that names the thing it forbids is
  // indistinguishable from the sheet doing it.
  const block = CSS.slice(CSS.indexOf('/* ---- Instrument row')).replace(/\/\*[\s\S]*?\*\//g, '');
  assert.ok(block.length > 500, 'the instrument-row block is at the end of the sheet');
  assert.match(block, /\.ir-unread \{[^}]*white-space: normal/);
  assert.match(block, /\.ir-nums \{[^}]*flex-wrap: wrap/);
  assert.match(block, /\.ir-row:focus-visible \{[^}]*box-shadow: var\(--ring\)/);
  assert.ok(!/\.ir-spark--line\.(up|down)/.test(block), 'the line wears no verdict');
  assert.ok(!/#[0-9a-fA-F]{3,8}\b/.test(block), 'tokens only');
  assert.ok(!/@media \(min-width: 1024px\)/.test(block));
});
