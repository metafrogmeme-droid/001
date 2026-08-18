'use strict';
/**
 * The sealed receipt was printing a machine stamp under the word "Reasoning".
 *
 * The bot prefixes every idea's reasoning with a provenance tag before it is
 * stored, synced and sealed:
 *
 *     [gpt-4o|TREND_UP|swing|momentum|C=0.68 MTF:up] The 4H RSI...
 *
 * and the model does not always fill the part after it. `_parse_llm_response`
 * returns an empty reasoning for two response shapes the bot accepts as good —
 * JSON with a direction and a confidence and no reasoning key, plain text with
 * DIRECTION and CONFIDENCE lines and no REASONING line — so what gets sealed on
 * those calls is the tag and a trailing space.
 *
 * That string is truthy. `('thesis' in p && p.thesis ? … )` rendered it, in
 * full, labelled Reasoning, on /call — the page whose entire purpose is that a
 * reader does not have to take the reason on trust. v4 sealing the reasoning
 * made the receipt MORE trustworthy and this row less, in the same commit.
 *
 * Absent is never a measurement, one field over from where it usually bites.
 *
 * The parity check below is the part worth keeping. A receipt that disagrees
 * with the bot about what the model said is its own kind of drift, and the two
 * implementations are in different languages in different directories with no
 * shared build — the only thing that can hold them together is a test that
 * runs the same inputs through both.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const TM = require('../public/js/thesis-model.js');
const ROOT = path.join(__dirname, '..', '..');
const CALL = fs.readFileSync(path.join(__dirname, '..', 'public', 'call.html'), 'utf8');

const TAG = '[gpt-4o|TREND_UP|swing|momentum|C=0.68 MTF:up]';

// One table, used by the unit tests AND by the cross-language parity check, so
// a case added for one is automatically demanded of the other.
const CASES = [
  [TAG + ' ', null],
  [TAG, null],
  [TAG + '   \n  ', null],
  [TAG + ' 4H RSI at 61 with MACD crossing up.', '4H RSI at 61 with MACD crossing up.'],
  [TAG + ' see [a|b] below', 'see [a|b] below'],
  ['Manual trade placed by user', 'Manual trade placed by user'],
  ['Regime=TREND_UP, RSI=61.2, confluence=0.68', 'Regime=TREND_UP, RSI=61.2, confluence=0.68'],
  ['[worth noting] the daily trend is still up.', '[worth noting] the daily trend is still up.'],
  ['', null],
  ['   ', null],
];

test('the tag alone is null, not an empty string', () => {
  // null, not '' — the receipt has to tell "sealed without a reason" apart
  // from a reason it renders as blank, and those two get different rows.
  assert.strictEqual(TM.prose(TAG + ' '), null);
  assert.strictEqual(TM.prose(null), null);
  assert.strictEqual(TM.prose(undefined), null);
});

test('every case strips the tag and nothing else', () => {
  for (const [input, want] of CASES) {
    assert.strictEqual(TM.prose(input), want, `prose(${JSON.stringify(input)})`);
  }
});

test('a bracketed opening without a pipe is left alone', () => {
  // Keyed on the tag's SHAPE. A looser rule would eat the first sentence of a
  // real thesis, which is worse than the defect being fixed.
  const s = '[worth noting] the daily trend is still up, so this is a fade.';
  assert.strictEqual(TM.prose(s), s);
  assert.strictEqual(TM.provenance(s), null);
});

test('provenance is kept, not discarded', () => {
  assert.strictEqual(TM.provenance(TAG + ' anything'),
    'gpt-4o|TREND_UP|swing|momentum|C=0.68 MTF:up');
  assert.strictEqual(TM.provenance('Manual trade placed by user'), null);
});

test('the browser model and the bot agree, character for character', () => {
  // THE PARITY PIN. Lift the pattern out of the Python module and run this
  // file's whole table through it. Two implementations that drift produce a
  // receipt claiming the bot said something it did not.
  const py = fs.readFileSync(
    path.join(ROOT, 'bot', 'formatters', 'thesis_text.py'), 'utf8');
  const m = py.match(/_PROVENANCE = re\.compile\(r"([^"]+)"\)/);
  assert.ok(m, 'bot/formatters/thesis_text.py no longer declares _PROVENANCE the '
    + 'way this test reads it — the two sides can no longer be compared');
  const fromPython = new RegExp(m[1]);

  for (const [input, want] of CASES) {
    const viaPython = input === null || input === undefined
      ? null : (String(input).replace(fromPython, '').trim() || null);
    assert.strictEqual(viaPython, want,
      `the Python pattern disagrees on ${JSON.stringify(input)}`);
  }
});

// ── the receipt is wired to it ───────────────────────────────────────────────

test('call.html loads the model before the renderer that reads it', () => {
  // The receipt reads ThesisModel inside a fetch callback. "Usually resolved
  // by then" is a race, not an order, and `defer` would make it one.
  const tag = CALL.indexOf('/js/thesis-model.js');
  assert.ok(tag > 0, 'call.html does not load thesis-model.js at all');
  assert.ok(!/thesis-model\.js[^>]*\bdefer\b/.test(CALL),
    'thesis-model.js is deferred — the renderer can run before it exists');
  assert.ok(tag < CALL.indexOf("var TM = window.ThesisModel"),
    'the model is loaded after the code that uses it');
});

test('the Reasoning row has three states and the raw thesis is not one', () => {
  const at = CALL.indexOf("'thesis' in p");
  assert.ok(at > 0, 'the Reasoning row is gone from call.html');
  const block = CALL.slice(at, at + 500);
  assert.ok(/prose\(p\.thesis\) !== undefined/.test(block),
    'an unloadable helper no longer omits the row — it makes some claim instead');
  assert.ok(/prose\(p\.thesis\) === null/.test(block),
    'the "sealed without a reason" state is gone; a missing reason is being '
    + 'rendered as something else');
  assert.ok(/not recorded/.test(block), 'the receipt hides the missing reason '
    + 'instead of saying so — on a single-source row, it must guard, not omit');
  assert.ok(!/esc\(p\.thesis\)/.test(block),
    'call.html prints the raw sealed thesis again, tag and all');
});

test('the sealed payload itself is untouched', () => {
  // Stripping is a DISPLAY decision. The seal is a commitment and must stay
  // maximal — the payload is shown verbatim further down the same page, and
  // the drift check still compares it byte for byte against the live record.
  assert.ok(/Sealed payload \(hash this yourself\)/.test(CALL));
  assert.ok(/esc\(d\.seal_payload\)/.test(CALL),
    'the receipt no longer prints the payload it asks readers to hash');
  const drift = CALL.slice(CALL.indexOf("['thesis', 'reasoning']"), CALL.indexOf("['thesis', 'reasoning']") + 900);
  assert.ok(!/ThesisModel|prose\(/.test(drift),
    'the drift check is comparing stripped prose — it must compare the bytes '
    + 'that were sealed, or it cannot detect an edit inside the tag');
});
