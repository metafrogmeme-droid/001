'use strict';
/**
 * The dashboard asks /api/market/candles for a CONTRACT symbol, never a display one.
 *
 * The signal stream rendered "No price history returned for this symbol" under
 * every row. The market was fine. The route relays Bitget, which knows
 * `LABUSDT` and answers 502 for `LAB` — and the chart slot was built from
 * `dsBase(s.symbol)`, which produces the display name. Every one of those
 * requests was refused before it reached a market, and the failure was rendered
 * as a fact ABOUT the market.
 *
 * THIS IS THE SAME DEFECT AS /embed/signals, ON THE SURFACE THAT WAS NOT
 * CHECKED. The embed was fixed first; CLAUDE.md's corollary says to ask which
 * OTHER surface makes the same claim before calling a fix done, and that step
 * was skipped. The dashboard went on saying it for as long as it took an
 * operator to notice and send a screenshot.
 *
 * So this test does not check the one line that broke. It checks EVERY caller,
 * because the reason this shipped is that only one caller was looked at.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require('./helpers/code_only');
const SRC = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const CODE = codeOnly(SRC);

/** Every `/api/market/candles/<expr>` in the file, with the symbol expression. */
function candleCallers(source) {
  const out = [];
  for (const m of source.matchAll(/market\/candles\/([^?`'"]*)/g)) {
    out.push(m[1].trim());
  }
  return out;
}

test('the scan finds the candle callers at all', () => {
  // A scan that matches nothing passes every assertion below while checking
  // none of them — the failure mode this whole file exists to catch, one level
  // up.
  const callers = candleCallers(CODE);
  assert.ok(callers.length >= 3,
    `only ${callers.length} candle caller(s) found; the pattern has drifted and `
    + 'this file is asserting nothing');
});

test('no candle fetch is handed a bare display symbol', () => {
  // `${sym}` is only safe when sym is already contract form. The expressions
  // that end in USDT, or read a data attribute built from dsContract, are
  // fine; a bare dsBase(...) is the bug.
  const bad = candleCallers(CODE).filter((expr) => /dsBase\(/.test(expr));
  assert.deepEqual(bad, [],
    `these fetch candles with a DISPLAY symbol, which the route answers 502 for:\n  `
    + bad.join('\n  '));
});

test('the signal-stream slot carries the contract symbol, and a separate label', () => {
  // Both halves, because swapping them is silent: the chart fetches fine and is
  // titled LABUSDT, or is titled LAB and fetches nothing.
  assert.match(CODE, /data-sc-sym="\$\{esc\(dsContract\(/,
    'the signal-stream chart slot is not built from dsContract — every row will '
    + 'render "No price history returned for this symbol" against a live market');
  assert.match(CODE, /data-sc-label="\$\{esc\(dsBase\(/,
    'the slot lost its readable label, so the chart is titled with venue notation');
});

test('dsContract is defined in terms of dsBase so the two cannot drift', () => {
  // Two independent implementations of "which symbol is this" is how the embed
  // and the dashboard came to disagree in the first place.
  const fn = CODE.slice(CODE.indexOf('function dsContract'));
  const body = fn.slice(0, fn.indexOf('\n  }') + 4);
  assert.match(body, /dsBase\(sym\)/,
    'dsContract re-derives the base itself instead of using dsBase');
  assert.match(body, /\+ 'USDT'/);
});

// ── the translation itself ────────────────────────────────────────────────

/** dsBase and dsContract, lifted out of the IIFE so they can be driven. */
function loadHelpers() {
  const a = CODE.indexOf('function dsBase');
  const b = CODE.indexOf('function dsContract');
  const dsBaseSrc = CODE.slice(a, CODE.indexOf('\n  }', a) + 4);
  const dsContractSrc = CODE.slice(b, CODE.indexOf('\n  }', b) + 4);
  // eslint-disable-next-line no-new-func
  return new Function(`${dsBaseSrc}\n${dsContractSrc}\nreturn { dsBase, dsContract };`)();
}

const H = loadHelpers();

test('every symbol form the stream carries maps to what Bitget answers', () => {
  // The live payload has carried all three of these spellings.
  for (const [input, contract, display] of [
    ['LAB/USDT:USDT', 'LABUSDT', 'LAB'],
    ['SOL/USDT', 'SOLUSDT', 'SOL'],
    ['BTCUSDT', 'BTCUSDT', 'BTC'],
    ['pengu/usdt:usdt', 'PENGUUSDT', 'PENGU'],
  ]) {
    assert.equal(H.dsContract(input), contract, `dsContract(${input})`);
    assert.equal(H.dsBase(input), display, `dsBase(${input})`);
  }
});

test('a contract symbol does not gain a second quote leg', () => {
  // `BTCUSDT` -> `BTCUSDTUSDT` is a 502 arriving by the opposite door.
  assert.equal(H.dsContract('BTCUSDT'), 'BTCUSDT');
  assert.equal(H.dsContract(H.dsContract('BTCUSDT')), 'BTCUSDT');
});

test('an unusable symbol yields empty rather than a bare USDT', () => {
  // `USDT` is a real request for a market that is not one, and it would come
  // back as a chart-shaped answer to a question nobody asked.
  for (const junk of ['', null, undefined, '///', ':::']) {
    assert.equal(H.dsContract(junk), '', `dsContract(${JSON.stringify(junk)})`);
  }
});

test('dsBase stays a display name — it is also the deep-scan index key', () => {
  // The fix must not have quietly turned dsBase into a contract builder: the
  // deep-scan hits are indexed by base ticker and would stop matching.
  assert.equal(H.dsBase('LAB/USDT:USDT'), 'LAB');
  assert.ok(!H.dsBase('LAB/USDT:USDT').endsWith('USDT'));
});
