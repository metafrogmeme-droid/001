'use strict';
/**
 * A signal's confidence is a score on every page, never a percent.
 *
 * Every surface printed it as `72%`, which reads as a probability. The figure
 * the bot publishes is `displayed_confidence(idea).value`: the analyzer's blend
 * snapshotted BEFORE any calibration curve, or a producer's own score that
 * never went through one. No curve has mapped it to outcomes, so it is
 * worded `score 72 (uncalibrated)` by one module, `confidence-score.js`, the
 * twin of `bot/core/signal_confidence.py`.
 *
 * Driven where a page can be driven (the module, the embed card, the sealed
 * receipt); the dashboard's stream is inside a 10,000-line IIFE no test boots,
 * so its two rows are read from comment-stripped source, bounded by the
 * function that renders them.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const CS = require('../public/js/confidence-score');
const ROW = require('../public/js/embed-row');
const TM = require('../public/js/thesis-model');
const i18n = require('../public/js/i18n');
const { codeOnly } = require('./helpers/code_only');

const ROOT = path.join(__dirname, '..', '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8');

// ── the twin agrees with the bot ───────────────────────────────────────────

/** A module-level `NAME = "..."` or `NAME = ("..." "...")` from Python source. */
function pyString(src, name) {
  const m = new RegExp('^' + name + ' = (\\([\\s\\S]*?\\)|"[^"\\n]*")$', 'm').exec(src);
  assert.ok(m, `${name} is not a module-level string in signal_confidence.py`);
  const parts = m[1].match(/"((?:[^"\\]|\\.)*)"/g);
  return parts.map((q) => JSON.parse(q)).join('');
}

test('the words and the sentence are the bot\'s own', () => {
  const src = read('bot', 'core', 'signal_confidence.py');
  assert.equal(CS.SCORE_WORD, pyString(src, 'SCORE_WORD'));
  assert.equal(CS.UNCALIBRATED_WORD, pyString(src, 'UNCALIBRATED_WORD'));
  assert.equal(CS.MEANING, pyString(src, 'SCORE_MEANING'));
  assert.equal(CS.PATTERN_SCORE_WORD, pyString(src, 'PATTERN_SCORE_WORD'));
  assert.equal(CS.UNMEASURED_WORD, pyString(src, 'UNMEASURED_WORD'));
});

// ── the module ─────────────────────────────────────────────────────────────

test('a figure on the signal row\'s 0-1 scale reads as a score', () => {
  assert.equal(CS.text(0.72), 'score 72 (uncalibrated)');
  assert.equal(CS.text('0.6600'), 'score 66 (uncalibrated)');
  assert.equal(CS.number(0.72), '72');
  // A measured zero is still a reading.
  assert.equal(CS.text(0), 'score 0 (uncalibrated)');
});

test('a figure a route already multiplied reads the same at scale 100', () => {
  assert.equal(CS.text(72, 100), 'score 72 (uncalibrated)');
  assert.equal(CS.number(66.0, 100), '66');
});

test('unreadable is never a score of zero', () => {
  for (const v of [null, undefined, '', '  ', 'n/a', NaN, Infinity, true, false]) {
    assert.equal(CS.text(v), null, String(v));
    assert.equal(CS.number(v), null, String(v));
  }
});

// ── the embed card and its share text ──────────────────────────────────────

test('the embed card prints the score and no percent sign', () => {
  const html = ROW.rowHtml({ symbol: 'ICP/USDT', direction: 'LONG', confidence: '0.6000' }, {});
  const cell = /<span class="e-conf">([^<]*)<\/span>/.exec(html);
  assert.ok(cell, html);
  assert.equal(cell[1], 'score 60 (uncalibrated)');
  assert.ok(!html.includes('60%'), html);
});

test('the embed card says nothing it could not read', () => {
  const html = ROW.rowHtml({ symbol: 'ICP/USDT', direction: 'LONG', confidence: null }, {});
  assert.equal(/<span class="e-conf">([^<]*)<\/span>/.exec(html)[1], '—');
});

test('the cast text carries the score, and omits an absent one', () => {
  const t = ROW.shareText({ symbol: 'NOKSTOCK/USDT:USDT', direction: 'SHORT', confidence: '0.6600' });
  assert.match(t, /confidence score 66 \(uncalibrated\)/);
  assert.ok(!t.includes('%'), t);
  const none = ROW.shareText({ symbol: 'BTC/USDT', direction: 'LONG', confidence: null });
  assert.ok(!/confidence/.test(none), none);
});

// ── the sealed receipt ─────────────────────────────────────────────────────

const CALL = read('app', 'public', 'call.html');

function receiptScript() {
  const a = CALL.indexOf('<script>\n(function () {\n  var esc');
  assert.ok(a > 0, 'the receipt script moved');
  return CALL.slice(a + '<script>'.length, CALL.indexOf('</script>', a));
}

async function receiptConfidence({ model }) {
  const payload = JSON.stringify({ kind: 'signal', symbol: 'BTC/USDT', direction: 'LONG',
    entry_price: 100, stop_loss: 95, take_profit: 110, confidence: 0.7,
    created_at: '2026-09-28T12:00:00Z' });
  const seal = crypto.createHash('sha256').update(payload).digest('hex');
  const body = { kind: 'signal', seal, seal_payload: payload, sealed_at: '2026-09-28T12:00:01Z',
    current: { symbol: 'BTC/USDT', direction: 'LONG', entry_price: 100, stop_loss: 95,
      take_profit: 110, confidence: 0.7 }, outcome: { status: 'NEW' }, anchor: null };
  const out = { innerHTML: '', appendChild() {} };
  const win = { ThesisModel: TM };
  if (model) win.RCConfidenceScore = CS;
  const ctx = {
    window: win, location: { pathname: '/call/abc123' },
    document: { getElementById: (id) => (id === 'out' ? out : null) },
    fetch: async () => ({ status: 200, ok: true, json: async () => body }),
    crypto: globalThis.crypto, TextEncoder, Uint8Array, Array, String, Number, JSON, Math, Promise,
  };
  vm.createContext(ctx);
  vm.runInContext(receiptScript(), ctx);
  for (let i = 0; i < 20 && !out.innerHTML.includes('Confidence'); i++) {
    await new Promise((r) => setTimeout(r, 5));
  }
  const m = /<span class="k">Confidence<\/span><span>(.*?)<\/span>/.exec(out.innerHTML);
  assert.ok(m, `no confidence line in: ${out.innerHTML.slice(0, 300)}`);
  return m[1];
}

test('the receipt prints the sealed confidence as a score', async () => {
  assert.equal(await receiptConfidence({ model: true }), 'score 70 (uncalibrated)');
});

test('without the wording the receipt prints the sealed number as sealed', async () => {
  assert.equal(await receiptConfidence({ model: false }), '0.7');
});

test('the receipt page loads the wording before its renderer', () => {
  const at = CALL.indexOf('/js/confidence-score.js?v=');
  assert.ok(at > 0 && at < CALL.indexOf('<script>\n(function () {\n  var esc'));
});

// ── the arena chip, in every language ──────────────────────────────────────

test('the arena chip keeps its slot and has no percent sign in any language', () => {
  const e = i18n.STRINGS['arena.sig_conf'];
  assert.equal(e.en, 'confidence score {p} (uncalibrated)');
  for (const [lang, s] of Object.entries(e)) {
    assert.ok(s.includes('{p}'), `${lang} dropped the slot`);
    assert.ok(!s.includes('%'), `${lang} still spells a percent: ${s}`);
  }
  const page = codeOnly(read('app', 'public', 'arena.html'));
  assert.match(page, /CS\.number\(s\.confidence, 100\)/);
  assert.ok(page.indexOf('/js/confidence-score.js?v=') > 0);
});

// ── the dashboard's stream and the agents' picks ───────────────────────────

/**
 * The body of the function `header` opens, by matching its braces on
 * comment-stripped source: bounded by the function, not by whatever follows.
 * Braces inside strings here are balanced (`{p}`, `${...}`), so the count holds.
 */
function functionBody(src, header) {
  const a = src.indexOf(header);
  assert.ok(a > 0, `${header} moved`);
  assert.equal(src.indexOf(header, a + 1), -1, `${header} matches twice`);
  const open = src.indexOf('{', a + header.length);
  let depth = 0;
  for (let i = open; i < src.length; i++) {
    if (src[i] === '{') depth += 1;
    else if (src[i] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(a, i + 1);
    }
  }
  throw new Error(`${header} never closes`);
}

test('the dashboard asks the wording for both signal rows', () => {
  const src = codeOnly(read('app', 'public', 'js', 'dashboard.js'));
  const stream = functionBody(src, 'async function drawStream()');
  assert.match(stream, /renderPanel\(C\('stream'\)/);
  assert.match(stream, /CS\.text\(s\.confidence\)/);
  assert.match(stream, /<th class="r">Score<\/th>/);
  assert.doesNotMatch(stream, /s\.confidence[^`]*\* 100\)\}%/);
  // The agents' picks row, the other place a signal row prints its figure.
  assert.match(src, /confidence \$\{esc\(\(self\.RCConfidenceScore && self\.RCConfidenceScore\.text\(s\.confidence\)\) \|\| '—'\)\}/);
  assert.doesNotMatch(src, /conf \$\{s\.confidence != null \? \(Number\(s\.confidence\) \* 100\)/);
});

test('the chat\'s "Trade this" chip asks the wording', () => {
  const src = codeOnly(read('app', 'public', 'js', 'chat.js'));
  const fn = functionBody(src, 'function appendSetupAction(s)');
  assert.match(fn, /CS \? CS\.text\(s\.confidence\) : null/);
  assert.match(fn, /confidence \$\{esc\(confText\)\}/);
  assert.doesNotMatch(fn, /\* 100\)\}% conf/);
});

test('every page that renders a signal loads the wording first', () => {
  const dash = read('app', 'public', 'dashboard.html');
  assert.ok(dash.indexOf('/js/confidence-score.js?v=') < dash.indexOf('/js/chat.js?v='));
  assert.ok(dash.indexOf('/js/confidence-score.js?v=') < dash.indexOf('/js/dashboard.js?v='));
  const index = read('app', 'public', 'index.html');
  assert.ok(index.indexOf('/js/confidence-score.js?v=') > 0
    && index.indexOf('/js/confidence-score.js?v=') < index.indexOf('/js/chat.js?v='));
  const embed = read('app', 'routes', 'embed.js');
  assert.ok(embed.indexOf('/js/confidence-score.js?v=') < embed.indexOf('/js/embed-row.js?v='));
});
