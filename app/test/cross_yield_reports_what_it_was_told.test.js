'use strict';
/**
 * The scanner reports what an item told it, and manufactures neither safety
 * fact — because the two REQUIRED gates one process over read them.
 *
 * `bot/guardian/yield_plan.py` refuses a stables move unless the route is shown
 * to be non-custodial and the destination recallable. Those two facts arrived
 * here through THREE coercions, each of which answered the reassuring value for
 * an item that reported nothing:
 *
 *     clampNum = (n) => (Number.isFinite(n) ? n : 0)   // lockup_days
 *     custodial: !!(it && it.custodial)                 // custodial
 *     lockup_days: Number(r.best.lockup_days) || 0      // routes/cross_yield.js
 *
 * Driven through the real planMoves before the fix, a lockup nobody reported
 * came back as `lockup_days: 0` — byte-identical to a measured "withdraw
 * anytime" — and an unreported route came back `custodial: false`. So the
 * Python `or 0.0` was the SECOND place the distinction was lost, and a
 * Python-only fix would have left the gate reading a zero this file invented.
 * That is `meme.js`'s recorded lesson (the coercion at the NORMALIZER, "the
 * earliest place the distinction can be lost, and the one that decides every
 * reader downstream at once") one module over, on a path that moves money.
 *
 * clampNum STAYS for the cost arithmetic: an unknown gas anchor really is "add
 * nothing", and `moveCostUsd` is not a safety claim. The split is the point.
 *
 * The Python half is tests/test_an_unreported_lockup_is_not_a_recallable_route.py.
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const CY = require('../lib/cross_yield');
const { codeOnly } = require('./helpers/code_only');

const ROOT = path.join(__dirname, '..');
const BASE = { asset: 'USDC', amount_usd: 500, from_chain: 'base', current_apy: 2, best_apy: 9 };
const plan = (over) => CY.planMoves([{ ...BASE, ...over }], {}).plans[0];

// ── the two readings ────────────────────────────────────────────────────
test('reportedNum answers null for a number nobody reported', () => {
  assert.equal(CY.reportedNum(30), 30);
  assert.equal(CY.reportedNum(0), 0, 'a measured zero is a reading');
  for (const absent of [undefined, null, NaN, Infinity, 'n/a']) {
    assert.equal(CY.reportedNum(absent), null, `${String(absent)} read as reported`);
  }
});

test('reportedFlag answers null for anything that is not a reported boolean', () => {
  assert.equal(CY.reportedFlag(true), true);
  assert.equal(CY.reportedFlag(false), false, 'a measured false is a reading');
  assert.equal(CY.reportedFlag(1), true);
  assert.equal(CY.reportedFlag(0), false);
  for (const absent of [undefined, null, 'false', 'true', '', 2]) {
    assert.equal(CY.reportedFlag(absent), null, `${String(absent)} read as a reported flag`);
  }
});

test('and the string spelling is not a reading, in either direction', () => {
  // `!!'false'` is true — the UNSAFE answer — where `!!undefined` is false,
  // the safe one. One expression, two wrong answers, decided by spelling.
  assert.equal(!!'false', true);
  assert.equal(CY.reportedFlag('false'), null);
});

// ── the normalizer ──────────────────────────────────────────────────────
test('a measured lockup and a measured safe route still come through', () => {
  const p = plan({ custodial: false, lockup_days: 0 });
  assert.equal(p.lockup_days, 0);
  assert.equal(p.custodial, false);
  const q = plan({ custodial: true, lockup_days: 30 });
  assert.equal(q.lockup_days, 30);
  assert.equal(q.custodial, true);
});

test('a lockup nobody reported is null, not a measured "withdraw anytime"', () => {
  assert.equal(plan({ custodial: false }).lockup_days, null);
  assert.equal(plan({ custodial: false, lockup_days: 'n/a' }).lockup_days, null);
});

test('a route nobody described is null, not a measured non-custodial one', () => {
  assert.equal(plan({ lockup_days: 0 }).custodial, null);
  assert.equal(plan({ lockup_days: 0, custodial: 'false' }).custodial, null);
});

test('the cost arithmetic keeps its zero, which is why clampNum stays', () => {
  // An unknown chain's gas anchor is "add the typical L2 figure", not "refuse
  // to cost the move" — a different question from whether a route is safe.
  const cost = CY.moveCostUsd(500, 'a-chain-nobody-has-heard-of', {});
  assert.ok(Number.isFinite(cost.total_usd) && cost.total_usd > 0);
});

// ── one reading, every caller ───────────────────────────────────────────
test('the route asks the shared reading rather than re-spelling the coercion', () => {
  const src = codeOnly(fs.readFileSync(path.join(ROOT, 'routes/cross_yield.js'), 'utf8'));
  assert.ok(/reportedFlag\(r\.best\.custodial\)/.test(src),
    'routes/cross_yield.js re-spells the custodial coercion');
  assert.ok(/reportedNum\(/.test(src),
    'routes/cross_yield.js re-spells the lockup coercion');
  assert.ok(!/!!\s*r\.best\.custodial/.test(src), 'the `!!` copy is back');
  assert.ok(!/lockup_days:\s*Number\([^)]*\)\s*\|\|\s*0/.test(src), 'the `|| 0` copy is back');
});

// ── the client stops asserting what nobody told it ──────────────────────
test('the dashboard panel asserts neither safety fact in the move it posts', () => {
  // codeOnly, because the fix's OWN comment has to name the literals it
  // removed — a bare scan would match the explanation and report it as the
  // defect, which is this repo's recorded "a comment that quotes the string
  // it forbids" trap, from the author's side.
  const src = codeOnly(fs.readFileSync(path.join(ROOT, 'public/js/dashboard.js'), 'utf8'));
  const start = src.indexOf('function mountYieldPlanPreview');
  assert.ok(start > 0, 'the cross-plan panel moved');
  const end = src.indexOf('function ', start + 40);
  const block = src.slice(start, end > start ? end : undefined);

  // BOTH spellings. The first draft of this assertion read `custodial:\s*false`
  // — the object-literal one — and the mutation round put the literals back as
  // `move.custodial = false`, which sailed past it. An assertion that names one
  // spelling is not an assertion about the claim, which is this repo's own
  // `_SLASH_COMMAND` lesson arriving in a guard written the same hour.
  const assigns = (name, value) =>
    new RegExp(`${name}\\s*[:=]\\s*${value}\\b`).test(block);
  assert.ok(!assigns('custodial', 'false'),
    'the panel asserts a non-custodial route the operator never stated');
  assert.ok(!assigns('lockup_days', '0'),
    'the panel asserts a recallable destination the operator never stated');
  assert.ok(!assigns('breakeven_days', '\\d'),
    'the panel invents a breakeven the operator never stated');
  // And it asks, rather than staying silent about facts it needs. Anchored on
  // the whole attribute: `/yp-cust/` alone matched `yp-cust-removed`, so the
  // substring acquitted the very removal it was written to catch.
  assert.ok(/id="yp-cust"/.test(block) && /id="yp-lock"/.test(block),
    'the panel no longer offers the operator a way to state either fact');
});
