'use strict';
// The public track record published "12 (7W/4L)" and let the reader find the
// twelfth.
//
// `trades.pnl` is DECIMAL(14,2) with no NOT NULL, so a closed row can
// genuinely carry no recorded P&L — and the monthly block turned that into
// two separate claims:
//
//   m.trades += 1 counted every row, while wins/losses only incremented on a
//   readable one. W + L < trades, with nothing on the page accounting for the
//   difference. Anyone checking the arithmetic on a record whose whole purpose
//   is to be checkable finds a trade that is neither a win nor a loss.
//
//   `result` is the SIGN of the month's dollar sum, and an unreadable row
//   added 0 to that sum. A month whose trades were ALL unpriced summed to
//   zero and published itself as "flat" — break-even, asserted about a month
//   nobody could price.
//
// This is the public surface. Everywhere else the same rule was applied to a
// number the operator reads; here it is a number strangers read, on a page
// that invites them to verify it.
//
//     ABSENT IS NOT ZERO — AND ON A PUBLIC RECORD, THE COUNTS MUST RECONCILE.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const route = fs.readFileSync(
  path.join(__dirname, '..', 'routes', 'track.js'), 'utf8');
const page = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'track.html'), 'utf8');
const i18n = require('../public/js/i18n.js');

// The monthly fold, lifted out so the arithmetic is exercised rather than
// only grepped for. Mirrors the route; the source assertions below fail if
// the route's shape drifts from this.
function fold(trades) {
  const monthly = {};
  const out = {};
  for (const t of trades) {
    const key = t.month;
    const m = out[key] || (out[key] = { trades: 0, wins: 0, losses: 0, unpriced: 0 });
    m.trades += 1;
    const p = parseFloat(t.pnl);
    if (!Number.isFinite(p)) { m.unpriced += 1; continue; }
    monthly[key] = (monthly[key] || 0) + p;
    if (p > 0) m.wins += 1; else if (p < 0) m.losses += 1;
  }
  for (const key of Object.keys(out)) {
    const m = out[key];
    const net = monthly[key] || 0;
    m.result = m.unpriced === m.trades ? 'unknown'
      : net > 0 ? 'green' : net < 0 ? 'red' : 'flat';
  }
  return out;
}

const row = (month, pnl) => ({ month, pnl });

test('the counts reconcile: W + L + unpriced === trades', () => {
  const m = fold([
    row('2026-07', 5), row('2026-07', -2), row('2026-07', null),
    row('2026-07', 3), row('2026-07', undefined),
  ])['2026-07'];
  assert.strictEqual(m.trades, 5);
  assert.strictEqual(m.wins + m.losses + m.unpriced, m.trades,
    'a public record that does not add up invites the reader to distrust it');
});

test('an unpriced month is "unknown", never "flat"', () => {
  const m = fold([row('2026-07', null), row('2026-07', null)])['2026-07'];
  assert.strictEqual(m.result, 'unknown');
  assert.notStrictEqual(m.result, 'flat',
    'flat is a measured break-even; this month was never priced');
});

test('a genuinely break-even month is still "flat"', () => {
  // The distinction must cut both ways, or the fix trades one wrong claim
  // for another.
  const m = fold([row('2026-07', 5), row('2026-07', -5)])['2026-07'];
  assert.strictEqual(m.result, 'flat');
  assert.strictEqual(m.unpriced, 0);
});

test('one unpriced row does not drag a green month toward flat', () => {
  // The sum used to fold in a 0 for it. With a small net that is enough to
  // change the published sign of the month.
  const m = fold([row('2026-07', 0.4), row('2026-07', null)])['2026-07'];
  assert.strictEqual(m.result, 'green');
  assert.strictEqual(m.unpriced, 1);
});

test('a losing month with an unpriced row stays red', () => {
  const m = fold([row('2026-07', -3), row('2026-07', null)])['2026-07'];
  assert.strictEqual(m.result, 'red');
});

test('months are kept apart', () => {
  const out = fold([row('2026-07', 1), row('2026-06', null)]);
  assert.strictEqual(out['2026-07'].result, 'green');
  assert.strictEqual(out['2026-06'].result, 'unknown');
});

// ── the route actually does this ────────────────────────────────────────────

test('the route skips unreadable rows instead of adding zero', () => {
  assert.match(route, /if \(!Number\.isFinite\(v\)\) continue;/,
    'the dollar sum must not absorb an unreadable row');
  assert.match(route, /if \(!Number\.isFinite\(p\)\) \{ m\.unpriced \+= 1; continue; \}/);
});

test('the route emits the unpriced count', () => {
  assert.match(route, /trades: 0, wins: 0, losses: 0, unpriced: 0/);
});

test("the route refuses 'flat' when nothing was scorable", () => {
  assert.match(route, /m\.unpriced === m\.trades \? 'unknown'/);
});

test('no coercion of pnl to zero survives in the monthly block', () => {
  const i = route.indexOf('const monthlyOut = {}');
  const block = route.slice(i, i + 1400)
    .split('\n').map((l) => l.replace(/\/\/.*$/, '')).join('\n');
  assert.doesNotMatch(block, /parseFloat\(t\.pnl\) \|\| 0/);
});

// ── the page shows it ───────────────────────────────────────────────────────

test('the page renders the unpriced count when there is one', () => {
  assert.match(page, /v\.unpriced \|\| 0\) > 0/, 'silent when there is none');
  assert.match(page, /tk\.m_unpriced/);
});

test('the page has a word for an unpriced month', () => {
  assert.match(page, /v\.result === 'unknown' \? T\('tk\.m_unknown'/);
});

test('both new strings exist in all 14 languages', () => {
  for (const k of ['tk.m_unknown', 'tk.m_unpriced']) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `missing key: ${k}`);
    for (const c of i18n.LANGS.map((l) => l.code)) {
      assert.ok(e[c] && String(e[c]).trim(), `${k}:${c}`);
    }
  }
});

test('the unpriced count is a count, never a value', () => {
  // §4: the public record is percent, ratio and count only.
  //
  // Written as an allowlist rather than a hunt for '$'. Two earlier versions
  // of this assertion failed on JavaScript syntax instead of on a currency
  // figure -- first on template-literal `${...}`, then on the `$(id)` DOM
  // helper. A pattern broader than the claim it checks reports the wrong
  // thing twice before it reports the right thing once. So: name the fields
  // that must not appear, which is what "no dollar amount" actually means.
  const i = page.indexOf('const un = (v.unpriced');
  assert.ok(i > 0, 'the unpriced fragment must exist');
  const frag = page.slice(i, page.indexOf(';', page.indexOf(': \'\'', i)));
  for (const money of ['pnl', 'net', 'usd', 'equity', 'size', 'amount']) {
    assert.ok(!frag.toLowerCase().includes(money),
      `the unpriced fragment must not reference ${money}: ${frag}`);
  }
  assert.match(frag, /v\.unpriced/, 'it renders the count');
  assert.match(frag, /T\('tk\.m_unpriced'/, 'and a translated word');
});
