'use strict';
// Two modules, four instances, one coercion — and both modules said in their
// own words that they did not do it.
//
// arena_discipline.js opens with "Honest by construction: ... Nothing is
// asked, sampled or estimated", and then:
//
//     const win = (parseFloat(t.pnl) || 0) > 0;
//
// `rows` filters on `reason != null` and never on pnl, so a close with no
// recorded P&L reached that line, became NaN, then 0, and `0 > 0` is false —
// filed as a DEFEAT. Both published win rates were depressed by every
// unrecorded close, and nothing on the surface said so. A win rate over 12 of
// 20 closes and one over 20 of 20 are different readings; only the count of
// what could not be scored tells them apart.
//
// theater.js had the same coercion three times, and this is the part worth
// keeping: fixing the first two did not fix the third. The value flows
// through `retPct` -> `pct` -> `finalTxt`, so a fix aimed at the LINE leaves
// the surface half-cured. The fix has to follow the VALUE.
//
//   line 59  retPct       unreadable pnl over a known size -> 0/size -> the
//                         caller renders a measured "+0.00%"
//   line 126 curve colour  NaN||0 >= 0 -> true -> the replay is painted GREEN
//   line 169 outcome word  same test -> the word "WIN"
//
// Colour is a claim. A green curve says "this one won" as loudly as the label
// does, and the label said it too.
//
//     A TRADE THE RECORD CANNOT PRICE IS NOT A LOSS, AND NOT A WIN.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { computeDiscipline } = require('../lib/arena_discipline.js');
const theater = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'theater.js'), 'utf8');

const row = (reason, pnl) => ({ reason, pnl });

// ── arena_discipline ────────────────────────────────────────────────────────

test('an unscorable close is not counted as a loss', () => {
  // Three planned closes: two wins and one with no recorded P&L. The old code
  // scored 2/3 = 66.7%; the truth is 2 of 2 that could be scored.
  const r = computeDiscipline([
    row('tp', 5), row('tp', 3), row('tp', null),
    row('sl', -1), row('manual', 2),
  ]);
  assert.strictEqual(r.win_rate_planned_pct, 66.7,
    'tp 5, tp 3, sl -1 are scorable: 2 wins of 3');
  assert.strictEqual(r.unscored_closes, 1);
});

test('the unscored count is reported so the rate can be judged', () => {
  const r = computeDiscipline([
    row('tp', null), row('tp', null), row('tp', 1),
    row('sl', -1), row('manual', 1),
  ]);
  assert.strictEqual(r.unscored_closes, 2,
    'without this, a rate from 3 of 5 closes looks like a rate from 5');
});

test('a clean record reports zero unscored', () => {
  const r = computeDiscipline([
    row('tp', 1), row('tp', 2), row('sl', -1), row('manual', 3), row('trail', 4),
  ]);
  assert.strictEqual(r.unscored_closes, 0);
});

test('NaN, null, undefined and non-numeric strings are all unscorable', () => {
  for (const bad of [null, undefined, NaN, 'n/a', '', {}]) {
    const r = computeDiscipline([
      row('tp', bad), row('tp', 1), row('sl', -1), row('manual', 1), row('trail', 1),
    ]);
    assert.strictEqual(r.unscored_closes, 1, `pnl: ${String(bad)}`);
  }
});

test('a real zero P&L is still scored — it is a measurement', () => {
  // 0 is falsy and 0 is a recorded break-even. `> 0` makes it a non-win,
  // which is correct; what must not happen is it being dropped as unreadable.
  const r = computeDiscipline([
    row('tp', 0), row('tp', 1), row('sl', -1), row('manual', 1), row('trail', 1),
  ]);
  assert.strictEqual(r.unscored_closes, 0);
  // tp 0, tp 1, sl -1, trail 1 are the four planned closes. 0 is not > 0, so
  // two of the four are wins.
  assert.strictEqual(r.win_rate_planned_pct, 50);
});

test('reason-based rates still count every row', () => {
  // planned_rate_pct and liquidation_rate_pct read the close REASON, which is
  // present on an unscorable row. Dropping those rows would understate how
  // many closes happened.
  const r = computeDiscipline([
    row('tp', null), row('tp', null), row('tp', null),
    row('manual', 1), row('manual', 1),
  ]);
  assert.strictEqual(r.closes, 5);
  assert.strictEqual(r.planned_rate_pct, 60, '3 of 5 closes were planned');
  assert.strictEqual(r.win_rate_planned_pct, null,
    'none of the planned closes could be scored, so there is no rate');
});

test('a side with no scorable sample yields null, not zero', () => {
  const r = computeDiscipline([
    row('manual', null), row('manual', null), row('tp', 1),
    row('tp', 1), row('sl', -1),
  ]);
  assert.strictEqual(r.win_rate_manual_pct, null,
    '0% would claim every manual close lost');
});

test('the docstring now states the rule it follows', () => {
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'lib', 'arena_discipline.js'), 'utf8');
  assert.match(src, /A close with no readable P&L is not a loss/,
    'the promise and the code have to agree — that mismatch is the defect');
});

// ── theater.js: the same value, three renderings ────────────────────────────

test('an unreadable P&L yields no derived percent', () => {
  const i = theater.indexOf('function retPct');
  const b = theater.slice(i, i + 800);
  assert.match(b, /if \(!Number\.isFinite\(pnl\)\) return null;/,
    '0/size renders as a measured "+0.00%"');
});

test('the replay curve has a third, uncoloured state', () => {
  const i = theater.indexOf('const _p = Number(trade.pnl)');
  const b = theater.slice(i, i + 400);
  assert.match(b, /Number\.isFinite\(_p\) \? _p >= 0 : null/);
  assert.match(b, /win == null \? 'var\(--muted\)'/,
    'colour is a claim; an unknown result must not be painted green');
});

test('the outcome word admits when it does not know', () => {
  const i = theater.indexOf('const finalTxt');
  const b = theater.slice(i, i + 300);
  assert.match(b, /RESULT UNKNOWN/);
  assert.match(b, /_res == null/);
});

test('no coercion of trade.pnl to zero survives in theater.js', () => {
  // Comments stripped first: the fix documents the old expression verbatim,
  // and prose ABOUT a defect is not the defect. Scanning raw source failed
  // this assertion on its own rationale — the second time that trap has
  // caught a test in this suite.
  const code = theater
    .split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n');
  assert.doesNotMatch(code, /Number\(trade\.pnl\) \|\| 0/,
    'three separate sites shared this coercion; fixing the line rather than '
    + 'the value is how the third one survived the first two fixes');
});

test('the exclusion reaches the surface, in every language', () => {
  // #1018's rule, applied to my own field: a count nothing renders answers
  // nothing. Silent exclusion reads as "won 60% of 20" when the truth is
  // "won 60% of the 12 we could score".
  const arena = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(arena, /disc\.unscored_closes > 0/,
    'rendered only when there is something to disclose');
  assert.match(arena, /arena\.disc_unscored/);
  const i18n = require('../public/js/i18n.js');
  const entry = i18n.STRINGS['arena.disc_unscored'];
  assert.ok(entry, 'missing i18n key');
  for (const c of i18n.LANGS.map((l) => l.code)) {
    assert.ok(entry[c] && String(entry[c]).trim(), `arena.disc_unscored:${c}`);
    assert.match(String(entry[c]), /\{n\}/, `placeholder lost in ${c}`);
  }
});
