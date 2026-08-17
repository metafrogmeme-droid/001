'use strict';
/**
 * An unreadable percent must not be painted as a profit.
 *
 * Found 2026-08-17 while replacing emoji with icons — three renderers on the
 * LANDING PAGE, the one surface every visitor sees first:
 *
 *   marketplace card   `m.total_return_pct >= 0 ? 'up' : 'down'`
 *                      null >= 0 is TRUE. pct() printed '—' honestly and the
 *                      markup coloured that dash as a gain.
 *
 *   live tape strip    `t.pct >= 0 ? 'up' : 'down'` with the text built as
 *                      `Number(t.pct).toFixed(2)`. Number(null) is 0, so an
 *                      unreadable close rendered `0.00%` beside a green
 *                      stripe — the exact example in CLAUDE.md's opening
 *                      paragraph, on the front door.
 *
 *   24h movers strip   `Number(t.change24h) * 100` is NaN when the field is
 *                      absent, NaN >= 0 is FALSE, so a failed read rendered
 *                      `NaN%` in the LOSS colour: a confident negative.
 *
 * None of the three is `x || 0`; all three are that row of CLAUDE.md's table
 * spelled differently, which is why grepping for the listed shapes found none
 * of them. What found them was rendering the page and asking what each colour
 * asserts.
 *
 * `RC.pnlClass` in js/app.js was already correct — `if (n == null ||
 * !isFinite(v)) return ''` with the comment "unknown -> muted, never red". The
 * defect was three local re-implementations of a helper that already existed
 * eleven lines away. So this file tests the helper's CONTRACT and then checks
 * that the landing page routes through it rather than open-coding it again.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const PUB = path.join(__dirname, '..', 'public');
const appSrc = fs.readFileSync(path.join(PUB, 'js', 'app.js'), 'utf8');
const index = fs.readFileSync(path.join(PUB, 'index.html'), 'utf8');

/**
 * Lift pnlClass out of the 600-line IIFE and run it. Source-matching the
 * function body would pass on a body that no longer behaves — the point is the
 * behaviour, so the behaviour is what runs.
 */
function pnlClass() {
  const start = appSrc.indexOf('function pnlClass(');
  assert.ok(start > 0, 'pnlClass is gone from js/app.js');
  const end = appSrc.indexOf('\n  }', start) + 4;
  const ctx = { out: null };
  vm.createContext(ctx);
  vm.runInContext(appSrc.slice(start, end) + '\nout = pnlClass;', ctx);
  return ctx.out;
}

test('the helper mutes every unreadable value instead of colouring it', () => {
  const cls = pnlClass();
  for (const bad of [null, undefined, NaN, Infinity, -Infinity, '', 'n/a', {}]) {
    assert.strictEqual(cls(bad), '',
      `pnlClass(${String(bad)}) returned a colour class — an unreadable value `
      + 'is being rendered as a measured gain or loss');
  }
});

test('zero is a real measurement and keeps its colour', () => {
  // The other half of the rule, and the one a careless "fix" breaks: 0.0 is
  // falsy AND a genuine, measured, break-even position. Muting it would hide a
  // real result behind the treatment reserved for missing ones.
  const cls = pnlClass();
  assert.strictEqual(cls(0), 'pos');
  assert.strictEqual(cls(0.0), 'pos');
  assert.strictEqual(cls('0'), 'pos');
});

test('real gains and losses still separate', () => {
  const cls = pnlClass();
  assert.strictEqual(cls(12.5), 'pos');
  assert.strictEqual(cls(-0.01), 'neg');
  assert.strictEqual(cls('-3.2'), 'neg');
});

// ── the landing page must USE it, not re-derive it ───────────────────────────

const { codeOnly } = require('./helpers/code_only');
const code = codeOnly(index);

test('no landing renderer decides a colour with a bare >= 0', () => {
  // The shape itself, because every one of the three defects was this exact
  // comparison feeding a class name. A source scan is right here: the failure
  // is a call site not reached by the helper, which is a property of the
  // wiring and invisible to a unit test.
  // The direction ARROW legitimately picks a sprite id this way — but only
  // after its own null/NaN guard has already returned, so the comparison never
  // sees an unreadable value. Excluded by the `icon-arrow-` prefix rather than
  // by loosening the pattern, so the next bare `>= 0 ? 'up'` still fails.
  const bad = [...code.matchAll(/[>]=\s*0\s*\?\s*['"](?:up|down|pos|neg)['"]/g)]
    .filter((m) => !code.slice(Math.max(0, m.index - 40), m.index).includes('icon-arrow-'));
  assert.deepStrictEqual(bad.map((m) => m[0]), [],
    'a colour is being chosen by `>= 0`, which is true for null and false for '
    + 'NaN — use RC.pnlClass, which returns "" for both');
});

test('all three renderers route through RC.pnlClass', () => {
  const n = (code.match(/RC\.pnlClass/g) || []).length;
  assert.ok(n >= 3,
    `only ${n} references to RC.pnlClass on the landing page; the marketplace `
    + 'card, the live tape and the 24h movers strip each need one');
});

test('an unreadable percent prints a dash, never 0.00% and never NaN%', () => {
  // toFixed on a coerced null is the text half of the same lie, and it has to
  // be guarded at the same call site as the colour — fixing one and not the
  // other leaves a muted 0.00% that still reads as break-even.
  for (const frag of ['Number(t.pct).toFixed(2)', 'chg.toFixed(2) + \'%\'']) {
    const at = code.indexOf(frag);
    if (at < 0) continue;
    const around = code.slice(Math.max(0, at - 260), at + 60);
    assert.match(around, /isFinite|== null|=== null/,
      `${frag} is formatted with no readability guard nearby — an absent `
      + 'value formats as 0.00% or NaN%');
  }
});

test('direction is not carried by colour alone', () => {
  // WCAG 1.4.1. The arrow is the redundant encoding; it must also be OMITTED
  // when the value is unreadable, or it becomes its own confident claim.
  assert.match(code, /icon-arrow-'\s*\+\s*\(Number\(v\)\s*>=\s*0\s*\?\s*'up'\s*:\s*'down'\)/,
    'the landing page draws no direction arrow beside its percentages');
  assert.match(code, /v==null\|\|!isFinite\(Number\(v\)\)\?''/,
    'the direction arrow is drawn without first checking the value is '
    + 'readable — an arrow pointing somewhere asserts as much as a colour');
});

test('the scan is reading code, not comments', () => {
  // This file's own docstrings quote `>= 0 ? 'up' : 'down'` repeatedly, and so
  // do the comments now sitting beside each fixed call site. CLAUDE.md counts
  // four false failures from exactly that.
  assert.ok(code.length > 1000, 'codeOnly() returned almost nothing');
  assert.ok(!code.includes('CLAUDE.md'),
    'comment text survived into the scanned source');
  assert.ok(index.includes("? 'up' : 'down'"),
    'the raw page no longer contains the quoted shape anywhere, so the '
    + 'comment-stripping above is no longer being exercised — this control '
    + 'has gone vacuous');
});
