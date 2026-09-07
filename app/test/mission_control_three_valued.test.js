'use strict';
/**
 * The home screen's mission-control bar read two of three stop-loss states.
 *
 * `/api/positions` is three-valued and has been since RC-2026-016 —
 * `bot/web/user_gateway.py` computes `unknown_count` as
 * `sum(1 for r in rows if r.get("unprotected") is None)`, positions whose stop
 * could not be READ. `slPositionsHtml` distinguishes all three: a red banner
 * for unprotected, a distinct one for unknown, an all-clear only when neither.
 *
 * The bar above it read `pos?.unprotected_count || 0` and nothing else. So a
 * book whose every stop was unreadable produced a mission-control bar with no
 * warning chip on it — and the absence of an alarm is how a reader takes
 * "nothing is wrong". Same payload, same screen, one surface over: the fix
 * landed in the panel and never in the bar, which is the corollary CLAUDE.md
 * puts first.
 *
 * The two counts get separate chips on purpose. "No stop on the exchange" and
 * "nobody could ask the exchange" call for different actions, and adding them
 * would report a count of confirmed exposures containing positions that may be
 * perfectly protected.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const { blockBetween } = require('./helpers/block');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

// From the chip helper through the bar it returns. Self-contained: everything
// else the region needs is a plain local, supplied as a VM global below.
const BLOCK = blockBetween(SRC,
  '        const chip = (href, k, v, cls) => {',
  'return `<div class="mc-bar">${cells.join(\'\')}</div>`;',
  { pad: 60, label: 'mission-control bar' });

/** Render the bar for a given /api/positions payload and controls state. */
function bar({ positions = null, openN = 0, mode = 'PAPER', stance = null,
  daily = null, paused = false } = {}) {
  const ctx = vm.createContext({
    esc: (s) => String(s == null ? '' : s),
    pnlClass: (n) => (n == null ? '' : n >= 0 ? 'pos' : 'neg'),
    signed: (n) => (n == null ? '--' : `${n > 0 ? '+' : ''}${n}`),
    pos: positions, openN, mode, stance, daily, paused,
    // The two reads under test, lifted verbatim from the shipped source so the
    // test cannot pass by re-deriving them differently here.
    unp: positions?.unprotected_count || 0,
    unk: positions?.unknown_count || 0,
  });
  vm.runInContext(`this.__out = (function () {\n${BLOCK}\n})();`, ctx);
  return ctx.__out;
}

test('a book with unreadable stops raises an alarm', () => {
  const html = bar({ positions: { unprotected_count: 0, unknown_count: 3 }, openN: 3 });
  assert.match(html, /Stop unknown/,
    'three positions whose stop nobody could read produced a bar with no '
    + 'warning on it at all');
  assert.match(html, /<b>3<\/b>/);
  assert.match(html, /mc-chip--alert/, 'and it must be styled as an alert');
});

test('unprotected and unknown are separate chips, not one sum', () => {
  const html = bar({ positions: { unprotected_count: 2, unknown_count: 3 }, openN: 5 });
  assert.match(html, /Unprotected/);
  assert.match(html, /Stop unknown/);
  assert.ok(!/<b>5<\/b>/.test(html.replace(/Open<\/span><b>5<\/b>/, '')),
    'the two counts were summed into one chip — that reports 5 confirmed '
    + 'exposures when only 2 are confirmed');
});

test('a fully protected book raises nothing', () => {
  const html = bar({ positions: { unprotected_count: 0, unknown_count: 0 }, openN: 4 });
  assert.ok(!/Unprotected/.test(html));
  assert.ok(!/Stop unknown/.test(html));
  assert.ok(!/mc-chip--alert/.test(html),
    'a measured all-clear must stay quiet — the fix must not alarm on every book');
});

test('the unprotected chip is unchanged', () => {
  const html = bar({ positions: { unprotected_count: 2, unknown_count: 0 }, openN: 2 });
  assert.match(html, /⚠️ Unprotected/);
  assert.ok(!/Stop unknown/.test(html));
});

test('an unreadable positions payload raises nothing here, by design', () => {
  // `pos` is null when /api/positions itself failed — the panel below paints
  // that state. The bar must not invent an alarm from a missing payload any
  // more than it may invent an all-clear from one.
  const html = bar({ positions: null, openN: 0 });
  assert.ok(!/Stop unknown/.test(html));
  assert.ok(!/Unprotected/.test(html));
  assert.match(html, /mc-bar/);
});

test('the sliced region is the real one', () => {
  assert.match(BLOCK, /mc-chip--alert/);
  assert.match(BLOCK, /unk > 0/, 'the unknown branch is not in the shipped bar');
  assert.match(BLOCK, /unp > 0/);
});
