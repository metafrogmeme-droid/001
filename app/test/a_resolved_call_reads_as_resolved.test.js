'use strict';
// A resolved call reads as resolved, and an ended one offers no Trade.
//
// Two readers of a signal's outcome were written before any signal could
// resolve, and each decided from a field the public payload never carries.
// The home view's "Latest signals" panel offered a Trade button when
// `s.pnl == null`, and `publicSignal` drops `pnl`, so every call got one,
// a stopped-out call included. The receipt page (/call/<key>) printed
// "<word> — not resolved yet" when `o.pnl == null`, and the route sends the
// word and its time, never an R, so a target, a stop-out and a call that was
// never filled all read as unresolved. Both read the signal panels' own
// model now, and both are driven here rather than scanned.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');

const SS = require('../public/js/signal-status-model.js');
const TM = require('../public/js/thesis-model.js');
const { codeOnly } = require('./helpers/code_only');
const { loaderBodies } = require('./helpers/loaders');

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

// ── the model's receipt line ──────────────────────────────────────────────

test('a pending word reads as not resolved yet', () => {
  assert.equal(SS.receiptOutcome({ status: 'NEW' }, esc), 'NEW — not resolved yet');
  assert.equal(SS.receiptOutcome({ status: 'OPEN' }, esc), 'ENTRY HIT — not resolved yet');
  assert.equal(SS.receiptOutcome({}, esc), 'NEW — not resolved yet', 'a row with no word is NEW');
});

test('every final word reads as resolved, coloured only where it carries an R', () => {
  const at = { resolved_at: '2026-09-28T13:00:00Z' };
  assert.equal(SS.receiptOutcome({ status: 'TARGET', ...at }, esc),
    '<b class="up-c">✓ TARGET</b> · resolved 2026-09-28T13:00:00Z');
  assert.equal(SS.receiptOutcome({ status: 'STOP', ...at }, esc),
    '<b class="down-c">✗ STOP</b> · resolved 2026-09-28T13:00:00Z');
  for (const w of ['EXPIRED', 'AMBIGUOUS', 'NO_EXIT', 'UNSCORED']) {
    const line = SS.receiptOutcome({ status: w, ...at }, esc);
    assert.ok(line.startsWith('<b>'), `${w}: ${line}`);
    assert.doesNotMatch(line, /not resolved|up-c|down-c/, w);
    assert.match(line, / · resolved 2026-09-28T13:00:00Z$/, w);
  }
  assert.equal(SS.receiptOutcome({ status: 'EXPIRED' }, esc), '<b>NOT FILLED</b>',
    'no time on record, no time printed');
});

test('a word the page does not know is printed as sent, escaped, with no verdict', () => {
  const line = SS.receiptOutcome({ status: '<i>closed</i>' }, esc);
  assert.equal(line, '&lt;I&gt;CLOSED&lt;/I&gt; — a word this page does not know');
  assert.throws(() => SS.receiptOutcome({ status: 'STOP' }), /escaper/);
  assert.equal(SS.receiptOutcome({ status: 'CLOSED', resolved_at: 't1' }, esc),
    'CLOSED — a word this page does not know · resolved t1', 'a time on record is still printed');
});

test('the resolved time is escaped, like every other field on the receipt', () => {
  assert.equal(SS.receiptOutcome({ status: 'TARGET', resolved_at: '<i>t</i>' }, esc),
    '<b class="up-c">✓ TARGET</b> · resolved &lt;i&gt;t&lt;/i&gt;');
});

// ── the receipt page, driven ─────────────────────────────────────────────

const CALL = read('public', 'call.html');

function mainScript() {
  const a = CALL.indexOf('<script>\n(function () {\n  var esc');
  assert.ok(a > 0, 'the receipt script moved');
  const b = CALL.indexOf('</script>', a);
  return CALL.slice(a + '<script>'.length, b);
}

async function renderReceipt(outcome, { model = true } = {}) {
  const payload = JSON.stringify({ kind: 'signal', symbol: 'BTC/USDT', direction: 'LONG',
    entry_price: 100, stop_loss: 95, take_profit: 110, confidence: 0.7, created_at: '2026-09-28T12:00:00Z' });
  const seal = crypto.createHash('sha256').update(payload).digest('hex');
  const body = { kind: 'signal', seal, seal_payload: payload, sealed_at: '2026-09-28T12:00:01Z',
    current: { symbol: 'BTC/USDT', direction: 'LONG', entry_price: 100, stop_loss: 95,
      take_profit: 110, confidence: 0.7 }, outcome, anchor: null };
  const out = { innerHTML: '', appendChild() {} };
  const win = {};
  const ctx = {
    window: win, location: { pathname: '/call/abc123' },
    document: { getElementById: (id) => (id === 'out' ? out : null) },
    fetch: async () => ({ status: 200, ok: true, json: async () => body }),
    crypto: globalThis.crypto, TextEncoder, Uint8Array, Array, String, Number, JSON, Math, Promise,
  };
  win.ThesisModel = TM;
  if (model) win.SignalStatusModel = SS;
  vm.createContext(ctx);
  vm.runInContext(mainScript(), ctx);
  for (let i = 0; i < 20 && !out.innerHTML.includes('Outcome'); i++) await new Promise((r) => setTimeout(r, 5));
  const m = /<span class="k">Outcome<\/span><span>(.*?)<\/span><\/div>/.exec(out.innerHTML);
  assert.ok(m, `no outcome line in: ${out.innerHTML.slice(0, 300)}`);
  return m[1];
}

test('the receipt says a stopped-out call hit its stop', async () => {
  assert.equal(await renderReceipt({ status: 'STOP', resolved_at: '2026-09-28T15:00:00Z' }),
    '<b class="down-c">✗ STOP</b> · resolved 2026-09-28T15:00:00Z');
});

test('the receipt says a call that was never filled was not filled, not "not resolved"', async () => {
  const line = await renderReceipt({ status: 'EXPIRED', resolved_at: '2026-09-28T16:00:00Z' });
  assert.match(line, /NOT FILLED/);
  assert.doesNotMatch(line, /not resolved/);
});

test('a pending call still reads as not resolved', async () => {
  assert.equal(await renderReceipt({ status: 'NEW', resolved_at: null }), 'NEW — not resolved yet');
});

test('without the model the receipt prints the word as sent and draws no verdict', async () => {
  const line = await renderReceipt({ status: 'STOP' }, { model: false });
  assert.equal(line, 'STOP');
  assert.equal(await renderReceipt({ status: '<i>x</i>' }, { model: false }), '&lt;i&gt;x&lt;/i&gt;');
});

test('the receipt loads the model before the script that reads it', () => {
  const m = CALL.indexOf('/js/signal-status-model.js?v=');
  const s = CALL.indexOf('<script>\n(function () {\n  var esc');
  assert.ok(m > 0 && s > m, 'the model is not loaded ahead of the receipt');
  assert.doesNotMatch(codeOnly(mainScript()), /o\.pnl/, 'the receipt reads a pnl the route never sends');
});

// ── the home view's Latest signals panel, driven ─────────────────────────

const DASH = codeOnly(read('public', 'js', 'dashboard.js'));

function hsigLoader() {
  const panel = loaderBodies(DASH).find((l) => l.target === "C('hsig')");
  assert.ok(panel && panel.inline, 'the home signals panel moved');
  const a = panel.body.indexOf('async () => {');
  let depth = 0; let i = panel.body.indexOf('{', a);
  for (; i < panel.body.length; i++) {
    if (panel.body[i] === '{') depth++;
    else if (panel.body[i] === '}') { depth--; if (depth === 0) break; }
  }
  return panel.body.slice(a, i + 1);
}

async function renderHome(signals, { model = true } = {}) {
  const ctx = {
    self: model ? { SignalStatusModel: SS } : {},
    fetchJSON: async () => ({ ok: true, data: { signals } }),
    mustRead: () => {}, esc, dirChip: (d) => `[${d}]`, fmtPrice: (p) => String(p), fmtAgo: () => 'now',
    JSON,
  };
  vm.createContext(ctx);
  const fn = vm.runInContext(`(${hsigLoader()})`, ctx);
  return fn();
}

const row = (over) => Object.assign({ symbol: 'BTC/USDT', direction: 'LONG', pattern: 'p',
  entry_price: 100, stop_loss: 95, take_profit: 110, created_at: '2026-09-28T12:00:00Z' }, over);

test('the home panel offers Trade on a pending call and not on an ended one', async () => {
  const html = await renderHome([
    row({ symbol: 'AAA', status: 'NEW', outcome: null }),
    row({ symbol: 'BBB', status: 'STOP', outcome: 'LOSS' }),
    row({ symbol: 'CCC', status: 'EXPIRED', outcome: null }),
    row({ symbol: 'DDD', status: 'OPEN', outcome: null }),
  ]);
  const unesc = (v) => v.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&');
  const offered = [...html.matchAll(/data-ptrade='([^']*)'/g)].map((m) => JSON.parse(unesc(m[1])).sy);
  assert.deepEqual(offered, ['AAA', 'DDD'],
    'a stopped-out call and an unfilled one were offered as trades');
});

test('the home panel offers nothing when the model did not load', async () => {
  const html = await renderHome([row({ status: 'NEW' })], { model: false });
  assert.doesNotMatch(html, /data-ptrade/);
});

test('no panel that reads the public signal stream decides from pnl', () => {
  // The public payload carries no `pnl` (lib/public_signal.js), so any test
  // of it answers "unresolved" for every call. Read the outcome word.
  const readers = loaderBodies(DASH).filter((l) => /fetchJSON\('\/api\/signals/.test(l.body));
  assert.ok(readers.length >= 2, readers.map((r) => r.target).join(', '));
  for (const r of readers) assert.doesNotMatch(r.body, /\.pnl\b/, `${r.target} reads pnl`);
});
