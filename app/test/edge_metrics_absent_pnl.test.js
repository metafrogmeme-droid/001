'use strict';
// A comment that promised the exact property the next line broke.
//
//   // ...All computed client-side from the same closed-trade history the
//   // calendar uses; nothing is invented.
//   renderPanel(C('edge'), async () => {
//     ...
//     const pnls = trades.map(t => parseFloat(t.pnl) || 0);
//
// `|| 0` turns an UNRECORDED result into a measured break-even, three lines
// under "nothing is invented". And the invented zero was incoherent as well
// as untrue — it was counted four different ways at once:
//
//   * into the expectancy MEAN, through the denominator;
//   * as neither a win nor a loss (p > 0 and p < 0 are both false), so win
//     rate and payoff quietly skipped it;
//   * as a streak-breaker, ending a run that may never have been broken;
//   * as a candidate for Math.max — "best trade: +0.00".
//
// A trade whose result was never recorded is not a break-even trade. It is a
// trade this panel cannot speak about.
//
// Two neighbours had the same bug. The daily calendar summed the zero and
// painted the day GREEN, because its renderer treats v >= 0 as profit. And
// the AI post-mortem computed `won = (parseFloat(pnl) || 0) >= 0`, so a
// missing result became a win in the prompt — a fabricated fact is worse fed
// to a model than printed on a card, because the model reasons onward from
// it.
//
// The rule, for the fourth surface this session:
//
//     ABSENT IS NOT ZERO. EXCLUDE IT, AND SAY THAT YOU DID.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const dash = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

function block(anchor, len) {
  const i = dash.indexOf(anchor);
  assert.ok(i > 0, `anchor not found: ${anchor}`);
  return dash.slice(i, i + len);
}

// ── Edge metrics ────────────────────────────────────────────────────────────

test('an unrecorded P&L never becomes a zero in the edge metrics', () => {
  assert.doesNotMatch(dash, /const pnls = trades\.map\(t => parseFloat\(t\.pnl\) \|\| 0\)/,
    'that zero was counted into the mean and into neither win nor loss');
});

test('the edge metrics keep only finite results', () => {
  const b = block('const _readable = trades', 700);
  assert.match(b, /Number\.isFinite\(p\)/,
    'isFinite also rejects NaN from an unparseable string, which `|| 0` '
    + 'silently converted');
  assert.match(b, /const _unreadable = trades\.length - _readable\.length/);
});

test('the panel still refuses to speak below two readable trades', () => {
  // The old guard counted trades; the new one must count READABLE trades, or
  // two unrecorded rows would produce a full set of metrics from nothing.
  const b = block('const _readable = trades', 700);
  assert.match(b, /if \(_readable\.length < 2\) return null/);
});

test('the exclusion is disclosed, with both counts', () => {
  const b = block('Positive expectancy with payoff', 600);
  assert.match(b, /_unreadable \?/, 'silent on a clean set');
  assert.match(b, /no recorded P&amp;L/);
  assert.match(b, /\$\{pnls\.length\} of \$\{trades\.length\}/,
    'a reader cannot judge the figures without knowing what they cover');
});

test('a complete set adds no caveat', () => {
  // A warning printed on every healthy panel is how a real one gets skipped.
  const b = block('Positive expectancy with payoff', 600);
  assert.match(b, /\$\{_unreadable \? `[^`]*` : ''\}/,
    'the caveat must be conditional');
});

// ── Daily P&L calendar ──────────────────────────────────────────────────────

test('the calendar skips unreadable results rather than summing zero', () => {
  const b = block('const byDay = {}', 500);
  assert.match(b, /if \(!Number\.isFinite\(v\)\) return;/);
  assert.doesNotMatch(b, /parseFloat\(t\.pnl\) \|\| 0/);
});

test('a day with nothing readable stays null, not green', () => {
  // The cell renderer paints v >= 0 as profit and v == null as grey. A summed
  // zero therefore claimed a profitable day out of unknown ones.
  const b = block('const byDay = {}', 500);
  assert.match(b, /byDay\[d\] = \(byDay\[d\] \|\| 0\) \+ v;/,
    'the day accumulator still starts at 0 — that zero is a sum seed, not a '
    + 'measurement, and it is only reached once a finite value exists');
  const cell = block('const bg = v == null', 400);
  assert.match(cell, /v == null \? 'var\(--surface-2\)'/,
    'null must still render as the neutral shade');
});

// ── The AI post-mortem ──────────────────────────────────────────────────────

test('a missing result is never reported to the analyst as a win', () => {
  assert.doesNotMatch(dash, /const won = \(parseFloat\(pnl\) \|\| 0\) >= 0/,
    'null became 0, and 0 >= 0 is true — every unrecorded trade "won"');
});

test('the prompt names the gap and forbids assuming an outcome', () => {
  const b = block('const _pnl = parseFloat(pnl)', 900);
  assert.match(b, /Number\.isFinite\(_pnl\)/);
  assert.match(b, /do NOT assume it won or lost/,
    'the model reasons onward from whatever premise it is handed');
  assert.match(b, /not recorded/,
    'the P&L slot itself must not carry a fabricated figure');
});

test('a recorded result still states won or lost plainly', () => {
  // The fix must not cost the panel its normal, useful behaviour.
  const b = block('const _pnl = parseFloat(pnl)', 900);
  assert.match(b, /_pnl >= 0 \? 'won' : 'lost'/);
});
