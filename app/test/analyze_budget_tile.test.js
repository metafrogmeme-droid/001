'use strict';
/**
 * The analyze-budget forecast reaches the primary surface.
 *
 * The engine forecasts, from its last MEASURED batch rate, whether the scan
 * universe fits the analyze phase cap (161 symbols at ~3.3s/signal needs
 * ~531s against a 300s cap → 71 will never be analysed). #991 surfaced that
 * on /status and /health. The website is the PRIMARY surface — an operator
 * watching the Engine page saw 37 consecutive timeout ticks with nothing
 * naming the cause.
 *
 * Contract pinned here (same rules as the bot side):
 * - rendered only when the payload carries the block — unmeasured is ABSENT,
 *   and absence must not render as "measured, and fine";
 * - visually alarming only on a real shortfall — a warning that fires on
 *   healthy ticks gets ignored on the tick that matters;
 * - a shortfall names the count of symbols skipped, not a percentage
 *   ("57%" was read as progress rather than as a shortfall).
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const dash = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('the engine modules panel renders the analyze-budget forecast', () => {
  assert.match(dash, /f\.analyze_capacity/,
    'the emods panel must read features.analyze_capacity');
  assert.match(dash, /Analyze budget/,
    'the tile must be named for what it measures');
});

test('a shortfall is visually distinct and counts symbols, not percent', () => {
  assert.match(dash, /Analyze budget · SHORT/);
  // Was a source scan for /shortfall/ over the tile block, and it passed on
  // the word appearing in the COMMENT above the code — the comment-matching
  // trap, inverted: an assertion satisfied by prose rather than by the thing
  // it claims to check. Run the sentence instead.
  const c = budgetCopy()({ ...REC, partial: false });
  assert.match(c.detail, /\b4 symbols\b/, 'the shortfall count must be shown');
  assert.ok(!/%/.test(c.detail) && !/%/.test(c.headline),
    'the shortfall is a count — a percentage was misread as progress live');
});

test('absent means absent — no placeholder tile for an unmeasured forecast', () => {
  // The tile is inside `if (f.analyze_capacity)` — no else branch that
  // renders zeros. Pin the guard exists rather than trusting the layout.
  const guarded = /if \(f\.analyze_capacity\)/.test(dash);
  assert.ok(guarded, 'the tile must be conditional on the block being present');
});

test('the alarm only fires on a real shortfall', () => {
  // Was pinned to the literal `(a.shortfall || 0) > 0` in the tile. That
  // expression now lives in the seam, so the property is asserted where it is
  // decided — and by running it, which also covers the null the old shape
  // silently read as zero.
  const tileBlock = dash.slice(dash.indexOf('f.analyze_capacity'),
                               dash.indexOf('f.entry_timing'));
  assert.match(tileBlock, /copy\.short\s*$|copy\.short\b/m,
    'SHORT styling must key off the computed verdict, not off mere presence');
  const f = budgetCopy();
  assert.strictEqual(f({ ...REC, shortfall: 0, partial: false }).short, false);
  assert.strictEqual(f({ ...REC, shortfall: 4, partial: false }).short, true);
  assert.strictEqual(f({ ...REC, shortfall: null, partial: false }).short, false,
    'an unreadable shortfall must not raise the alarm on no evidence');
});

/**
 * The sentence, RUN rather than grepped.
 *
 * Every assertion above this line is a source scan, and the wording is exactly
 * what a source scan cannot judge: `partial` selects between two claims of
 * different strength, and a branch that exists is not a branch that is reached.
 *
 * The defect being pinned: a forecast built from a batch that was ITSELF
 * cancelled omits the analyses still running when the cap hit, and those are
 * the slow ones. So `fits` is a ceiling and `shortfall` is a floor. On
 * 2026-09-02 a status card said "4 will not be analysed" on a tick that
 * analysed 20 of 40 — an honest number inside a sentence that overstated it.
 */
const vm = require('node:vm');

function budgetCopy() {
  const start = dash.indexOf('function analyzeBudgetCopy(a)');
  const end = dash.indexOf('// end analyzeBudgetCopy');
  assert.ok(start > 0 && end > start,
    'analyzeBudgetCopy must stay extractable — it is the seam this file tests');
  const ctx = {};
  vm.createContext(ctx);
  vm.runInContext(dash.slice(start, end) + '\nthis.f = analyzeBudgetCopy;', ctx);
  return ctx.f;
}

const REC = {
  of: 40, fits: 36, shortfall: 4, cap_s: 300, needed_s: 333,
  measured_from: 36, measured_of: 40,
};

test('a rate measured on a cancelled batch reports a floor', () => {
  const c = budgetCopy()({ ...REC, partial: true });
  assert.ok(c.short);
  assert.match(c.detail, /at least 4 symbols/);
  assert.match(c.headline, /at most 36 of 40/);
});

test('the floor names what it was measured on', () => {
  const c = budgetCopy()({ ...REC, partial: true });
  assert.match(c.detail, /cut short \(36 of 40 done\)/,
    'without the provenance, "at least" is an unexplained hedge');
});

test('a rate measured on a complete batch keeps the exact claim', () => {
  const c = budgetCopy()({ ...REC, partial: false });
  assert.ok(!/at least/.test(c.detail), 'the hedge must not leak onto a full measurement');
  assert.ok(!/at most/.test(c.headline));
  assert.match(c.detail, /^4 symbols will not be analysed this tick/);
});

test('an absent partial flag keeps the hedge but invents no provenance', () => {
  // We cannot tell whether the rate is a floor. "at least" is true either way;
  // the provenance clause would be a claim about a batch we know nothing of.
  const { partial, ...noFlag } = { ...REC, partial: true };
  const c = budgetCopy()({ ...noFlag, measured_from: undefined,
                           measured_of: undefined });
  assert.match(c.detail, /at least 4 symbols/);
  assert.ok(!/cut short/.test(c.detail));
});

test('a fitting budget is never alarming, either wording', () => {
  for (const partial of [true, false, undefined]) {
    const c = budgetCopy()({ ...REC, shortfall: 0, partial });
    assert.strictEqual(c.short, false);
    assert.ok(!/will not be analysed/.test(c.detail));
  }
});

test('an unreadable field renders as unreadable, not as zero', () => {
  // `Number(x) || 0` was the shape here before: an absent `fits` painted "0 of
  // 40 fit", which is a measured catastrophe rather than a missing number.
  const c = budgetCopy()({ ...REC, fits: null, partial: false });
  assert.match(c.headline, /—/, 'an absent count must not render as 0');
  assert.ok(!/\b0 of 40\b/.test(c.headline));
  const d = budgetCopy()({ ...REC, needed_s: null, cap_s: null, shortfall: 0 });
  assert.ok(!/~0s|\b0s cap/.test(d.detail), 'absent seconds must not render as 0s');
});
