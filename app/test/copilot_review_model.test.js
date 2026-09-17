/**
 * THE CO-PILOT BLOCK'S BADGE, DRIVEN.
 *
 * The block was `d.verdict === 'clear' ? CLEAR : CAUTION` -- two branches over
 * a vocabulary that is now four. What that costs: `partial` (nothing flagged,
 * something not looked at) wears the word for a finding, and any verdict a
 * later bot build adds does too. The reverse is worse in the other direction:
 * had the ternary been written the other way round, an unknown word would have
 * been painted CLEAR on the block a person reads before confirming an order.
 *
 * The model deliberately derives NO sentence and NO score of its own. The
 * score's span and every unchecked reason arrive as text from the producer,
 * because a figure re-derived in the browser is the second reading the seam
 * exists to replace -- the rule the arb panel already states.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const M = require(path.join(__dirname, '..', 'public', 'js', 'copilot-review-model.js'));

test('each of the four verdicts has its own badge', () => {
  for (const v of ['clear', 'caution', 'partial', 'invalid']) {
    const b = M.badge({ verdict: v });
    assert.ok(b, `${v} must have a badge`);
    assert.equal(b.key, v);
    assert.ok(b.label && b.cls, `${v} needs a label and a class`);
  }
  const labels = new Set(['clear', 'caution', 'partial', 'invalid']
    .map(v => M.badge({ verdict: v }).label));
  assert.equal(labels.size, 4, 'two verdicts sharing a word is two states rendered as one');
  const classes = new Set(['clear', 'caution', 'partial', 'invalid']
    .map(v => M.badge({ verdict: v }).cls));
  assert.equal(classes.size, 4, 'two verdicts sharing a colour is the defect this replaces');
});

test('a verdict this build does not know is not cleared', () => {
  for (const v of ['nonsense', '', null, undefined, 0, 'CLEAR', 'Clear']) {
    assert.equal(M.badge({ verdict: v }), null, JSON.stringify(v));
  }
  assert.equal(M.badge(null), null);
  assert.equal(M.badge('clear'), null, 'a bare string is not a review payload');
});

test('a verdict named like an Object prototype key is not a badge', () => {
  // `hasOwnProperty` rather than a truthiness test on the lookup: `BADGES`
  // is an object literal, so `BADGES['toString']` is a function and a naive
  // `BADGES[v] || null` would have painted a badge for it.
  for (const v of ['toString', 'constructor', '__proto__', 'hasOwnProperty']) {
    assert.equal(M.badge({ verdict: v }), null, v);
  }
});

test('coverage prints the producer words, and drops a row missing half of them', () => {
  const rows = M.coverage({ unchecked: [
    { name: 'size_vs_equity', label: 'size vs equity', reason: 'no margin on this ticket' },
    { name: 'engine_bias', label: '', reason: 'nobody said' },
    { name: 'existing_exposure', label: 'your existing exposure', reason: '' },
    null,
    'not an object'
  ] });
  // "Not checked — : " names a subject nobody named and gives no reason.
  assert.deepEqual(rows, [{ label: 'size vs equity', reason: 'no margin on this ticket' }]);
});

test('coverage is empty, never thrown, for a payload with no list', () => {
  assert.deepEqual(M.coverage({}), []);
  assert.deepEqual(M.coverage({ unchecked: 'nope' }), []);
  assert.deepEqual(M.coverage(null), []);
});

test('the score span is the producer sentence and null when it sent none', () => {
  assert.equal(M.scoreLine({ score_line: 'score 80/100 over 3 of the 4 checks' }),
    'score 80/100 over 3 of the 4 checks');
  // An older bot build, or a review with no score. The block then prints no
  // score at all rather than a bare number with its basis unstated, which is
  // the defect the whole slice is about.
  for (const bad of [{}, { score_line: '' }, { score_line: '   ' }, { score_line: 42 }, null]) {
    assert.equal(M.scoreLine(bad), null, JSON.stringify(bad));
  }
});

test('the model states no threshold and derives no figure of its own', () => {
  const src = require('node:fs').readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'copilot-review-model.js'), 'utf8');
  const code = require('./helpers/code_only.js').codeOnly(src);
  // No arithmetic on the payload: the span, the score and every reason are the
  // producer's words. A number computed here is a second answer.
  assert.equal(/score_basis/.test(code), false,
    'reading score_basis here would derive a span the producer already wrote');
  assert.ok(!/[0-9]+\s*\/\s*[0-9]+/.test(code.replace(/\/\*[\s\S]*?\*\//g, '')),
    'no ratio is computed in the model');
});
