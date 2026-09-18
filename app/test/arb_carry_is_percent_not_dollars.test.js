'use strict';
/**
 * The paper-carry column is a percent of a stated stake, and the total says
 * how much of the table it covers.
 *
 * §4: `GET /api/reports` carries no auth and no limiter, and it published
 * `arb.carries[].earned_usd` per coin. The producer emits `earned_pct` now
 * (`bot/core/web_reports.py`, guarded by
 * `tests/test_the_public_report_carries_no_dollar.py`); this is the panel
 * half, and the panel had three defects of its own on the same rows:
 *
 *   Number(c.earned_usd) || 0   an unread carry printed as a measured $0.00
 *   pnlClass(c.earned_usd)      and PAINTED — colour is a claim
 *   reduce((a, c) => a + ...)   summing `|| 0` over unreadable rows and
 *                               printing the result as the whole table
 *
 * The last is the shapes table's own "a partial total, printed as whole".
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { codeOnly } = require('./helpers/code_only.js');

const A = require('../public/js/arb-carry-model.js');
const DASH = codeOnly(fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8'));

test('a read carry prints with its sign and a percent', () => {
  assert.equal(A.pct(3.812), '+3.81%');
  assert.equal(A.pct(-1.25), '-1.25%');
});

test('a MEASURED zero prints — a spread that paid nothing is a reading', () => {
  assert.equal(A.pct(0), '+0.00%');
  assert.equal(A.cls(0), '', 'and claims neither direction');
});

test('an unread carry is a dash, never a zero', () => {
  for (const v of [null, undefined, NaN, Infinity, '3.8', {}, []]) {
    assert.equal(A.pct(v), '—', `${String(v)} must not print as a number`);
  }
});

test('colour is a claim: an unread carry is muted, not green and not red', () => {
  assert.equal(A.cls(null), 'muted');
  assert.equal(A.cls(undefined), 'muted');
  assert.equal(A.cls(NaN), 'muted');
  assert.equal(A.cls(2), 'up');
  assert.equal(A.cls(-2), 'down');
});

test('the total covers the rows it could read, and says which', () => {
  const t = A.total([{ earned_pct: 3 }, { earned_pct: null }, { earned_pct: -1 }]);
  assert.equal(t.pct, 2);
  assert.equal(t.scored, 2);
  assert.equal(t.n, 3);
  assert.match(A.sample(t), /2 of 3/);
});

test('a total over rows that were ALL read carries no caveat', () => {
  // A permanent "across 2 of 2" under a healthy table is the row that trains
  // a reader to stop reading the line.
  const t = A.total([{ earned_pct: 1 }, { earned_pct: 2 }]);
  assert.equal(t.pct, 3);
  assert.equal(A.sample(t), '');
});

test('a total over NOTHING readable is null, not zero — and carries no caveat', () => {
  const t = A.total([{ earned_pct: null }, { earned_pct: NaN }]);
  assert.equal(t.pct, null, 'a sum over an empty set is not a measured zero');
  assert.equal(A.pct(t.pct), '—');
  assert.equal(A.sample(t), '',
    'the dash has already said so; a caveat about a figure that is not there '
    + 'is a hedge rather than a disclosure');
});

test('an hour count is three-valued on the same rows, for the same reason', () => {
  // The panel reads a WIRE payload, so it cannot lean on the producer's
  // invariants. `(c.held_hours || 0).toFixed(0)` printed "0h" for a hold time
  // that did not arrive — a claim about how long a spread was held. Fixing the
  // carry cell and leaving this one is "fixing two left the third", on one row.
  assert.equal(A.hours(72), '72h');
  assert.equal(A.hours(0), '0h', 'a period observed and never entered is a reading');
  for (const v of [null, undefined, NaN, Infinity, '72']) {
    assert.equal(A.hours(v), '—', `${String(v)} must not print as an hour count`);
  }
});

test('a junk rows argument does not throw', () => {
  for (const bad of [null, undefined, 'rows', 42, {}]) {
    const t = A.total(bad);
    assert.equal(t.pct, null);
    assert.equal(t.n, 0);
  }
});

test('the stake the percents are OF is stated, or said to be unreported', () => {
  // `Number(arb.notional_usd || 1000)` labelled the column $1,000 whether or
  // not that was the basis the producer used — correct only while the constant
  // never moves, and a guessed basis is a claim about what every number beside
  // it means.
  assert.match(A.basis(1000), /\$1,000 would have earned/);
  assert.match(A.basis(5000), /\$5,000 would have earned/);
  for (const v of [null, undefined, 0, -1, NaN, '1000']) {
    const s = A.basis(v);
    assert.match(s, /percent of the tracked stake/, String(v));
    assert.match(s, /not reported/, String(v));
    assert.ok(!/\$/.test(s), `${String(v)} must not name a dollar figure`);
  }
});

test('the panel reads the model and spells no dollar of its own', () => {
  // The panel's block, bounded by its own renderPanel call rather than by
  // "whatever function comes next".
  const i = DASH.indexOf("renderPanel(C('arb')");
  assert.ok(i > 0, 'arb panel not found');
  const block = DASH.slice(i, DASH.indexOf("renderPanel(C('", i + 40));
  assert.ok(block.length > 300 && block.length < 4000, `block ${block.length}`);

  assert.ok(!/earned_usd/.test(block), 'the dollar field must not be read');
  assert.ok(!/\$\$\{[^}]*earned/.test(block), 'and never rendered with a $');
  assert.ok(/ArbCarryModel/.test(block), 'the model is bound');
  assert.ok(/ArbCarry\.pct\(/.test(block) && /ArbCarry\.cls\(/.test(block));
  assert.ok(/ArbCarry\.total\(/.test(block) && /ArbCarry\.sample\(/.test(block),
    'the total and its sample both come from the model');
  assert.ok(!/pnlClass\(c\./.test(block),
    'the row colour must come from the model, which can decline');
  assert.ok(!/\|\|\s*0\s*\)\.toFixed/.test(block),
    'an or-zero on a value that can be absent');
});

test('an absent model is a script that did not load, not a blank table', () => {
  const i = DASH.indexOf("renderPanel(C('arb')");
  const block = DASH.slice(i, DASH.indexOf("renderPanel(C('", i + 40));
  assert.match(block, /if \(!ArbCarry\) throw/,
    'a table of bare numbers would claim a reading nothing performed');
});

test('the parity panel prints ratios, not the operator\'s dollars', () => {
  // Same payload, same public route. `net_pnl` was the operator's realized
  // net on the LIVE book, justified as "already public on /track" — and
  // /track indexes its curve to 100 so no account size escapes.
  const i = DASH.indexOf("renderPanel(C('eparity')");
  assert.ok(i > 0, 'parity panel not found');
  const block = DASH.slice(i, DASH.indexOf("renderPanel(C('", i + 40));
  assert.ok(!/p\.net_pnl/.test(block), 'net_pnl is an account dollar figure');
  assert.ok(!/p\.total_fees/.test(block), 'total_fees likewise');
  assert.ok(/p\.pf/.test(block), 'the ratio that carries the same signal stays');
  assert.ok(/fee_drag_of_gross/.test(block),
    'fee drag is what the dollar total_fees was there to say');
});

test('the page loads the model', () => {
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  assert.match(html, /<script src="\/js\/arb-carry-model\.js\?v=\d+" defer><\/script>/,
    'the panel throws without it');
});
