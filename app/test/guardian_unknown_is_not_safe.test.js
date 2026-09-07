'use strict';
/**
 * The Guardian console must not paint an unread book green.
 *
 * THE PRODUCER WAS FIXED AND THE RENDERERS WERE NOT — which is the corollary
 * CLAUDE.md puts above the rule itself: *ask which OTHER surface makes the same
 * claim before calling the fix done*.
 *
 * `engine.guardian_status()` used to fail open to `posture: "none"` and
 * `twin/sentinel/escape.risk: "none"` — "the calmest reading it has, about a
 * book it never read" — and its own source comment records replacing those
 * defaults with `None`. That `None` reaches this console as `null`, and
 * `String(null || 'none')` is `'none'`, so the card went on rendering GREEN
 * NONE: the same all-clear, now assembled from an explicit "I do not know"
 * instead of an implicit one. `position_count` did the same trick one line
 * down — null is falsy, and the falsy branch said "· flat".
 *
 * Colour is a claim. A green chip says "nothing flagged" as loudly as the word
 * does, and this is the safety layer's own status card.
 *
 * The renderers are inline in dashboard.js, so they are sliced out and run in a
 * VM — the pattern the engine-status chip test established for exactly this.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const { blockBetween } = require('./helpers/block');

const SRC = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

// The whole risk-rendering region: the colour map, the module chip, and the
// posture card that composes them.
// The pad closes `guardianPostureCard` and stops there. A generous one runs
// into the next `const _BAND_COL = {` and cuts it mid-object, which the VM
// reports as a bare "SyntaxError: Invalid or unexpected token" with no hint
// that the slice is the problem — hence the exact tail and the shape guard at
// the bottom of this file.
const BLOCK = blockBetween(SRC, 'const _RISK_COL = {',
  'the escape agent recovers.</div>\n    </section>`;\n  }',
  { pad: 60, label: 'guardian posture card' });

const ctx = vm.createContext({
  // The real one HTML-escapes; identity is enough here and keeps the
  // assertions about the words rather than about entities.
  esc: (s) => String(s == null ? '' : s),
});
vm.runInContext(`${BLOCK}\nthis.riskCol = riskCol; this.moduleChip = moduleChip;`
  + '\nthis.guardianPostureCard = guardianPostureCard;', ctx);
const { riskCol, moduleChip, guardianPostureCard } = ctx;

const GREEN = 'var(--up,#31c48d)';
const MUTED = 'var(--muted,#8a94a6)';

// ── the colour, which is the half a reader takes in at a glance ─────────

test('an unassessed risk is muted, not green', () => {
  assert.strictEqual(riskCol(null), MUTED,
    'null risk painted the safe colour — the exact fail-open the engine stopped '
    + 'emitting, restored by the renderer');
  assert.strictEqual(riskCol(undefined), MUTED);
  assert.strictEqual(riskCol(''), MUTED);
  assert.strictEqual(riskCol('   '), MUTED);
});

test('a measured "none" keeps its green', () => {
  // NOT EVERY MATCH IS A DEFECT. A book that WAS assessed and flagged nothing
  // is a real measurement and the safest thing this panel can truthfully say.
  // Muting it would remove a true statement to satisfy a rule about false ones.
  assert.strictEqual(riskCol('none'), GREEN);
});

test('the graded risks keep their own colours', () => {
  assert.strictEqual(riskCol('low'), 'var(--accent,#3fb6ff)');
  assert.strictEqual(riskCol('medium'), 'var(--warn,#f0a848)');
  assert.strictEqual(riskCol('high'), 'var(--down,#f05252)');
});

test('a risk word nobody knows is muted, not green', () => {
  assert.strictEqual(riskCol('catastrophic'), MUTED);
});

// ── the word ────────────────────────────────────────────────────────────

test('a module with no reading says UNKNOWN, not NONE', () => {
  const chip = moduleChip('🔮 Twin', null, true);
  assert.match(chip, /UNKNOWN/);
  assert.ok(!/NONE/.test(chip), `an unread module still reads NONE: ${chip}`);
  assert.ok(chip.includes(MUTED), 'and it must not be painted green');
});

test('a module that reported none says NONE', () => {
  const chip = moduleChip('🔮 Twin', 'none', true);
  assert.match(chip, /NONE/);
  assert.ok(chip.includes(GREEN));
});

// ── the card ────────────────────────────────────────────────────────────

const card = (gs) => guardianPostureCard(gs);

test('a fully unreadable status is not an all-clear', () => {
  // Exactly what engine.guardian_status() returns when every read fails.
  const html = card({
    flags: {}, chain: { length: 0, ok: null }, policy: null,
    twin: { risk: null, position_count: null },
    sentinel: { risk: null }, escape: { risk: null },
    posture: null,
  });
  assert.match(html, /UNKNOWN/, 'the posture chip must say so');
  assert.ok(!/>NONE</.test(html) && !/\bNONE\b/.test(html),
    `the card still announces NONE somewhere: ${html}`);
  assert.ok(!html.includes(GREEN),
    'nothing on a card assembled from failed reads may be green');
  assert.ok(!/· flat/.test(html),
    'an unread book was announced as flat — "no open positions" is a claim, and '
    + 'the count is null precisely when nobody could count');
  assert.match(html, /could not be read/,
    'the card must say why it is empty rather than just being empty');
  assert.ok(!/Live-book safety read/.test(html),
    'the card must not describe itself as a safety read it did not perform');
});

test('a real posture still renders as one', () => {
  const html = card({
    flags: { firewall: true, intent_policy: true },
    twin: { risk: 'medium', position_count: 3 },
    sentinel: { risk: 'low' }, escape: { risk: 'none' },
    posture: 'medium',
  });
  assert.match(html, /MEDIUM/);
  assert.match(html, /3 open positions/);
  assert.match(html, /Live-book safety read/);
  assert.ok(!/UNKNOWN/.test(html));
});

test('a genuinely flat book still says flat', () => {
  // 0 is a measurement. The fix must not turn "we looked and there is nothing"
  // into "we could not look".
  const html = card({
    flags: {}, twin: { risk: 'none', position_count: 0 },
    sentinel: { risk: 'none' }, escape: { risk: 'none' }, posture: 'none',
  });
  assert.match(html, /· flat/);
  assert.ok(!/position count unread/.test(html));
});

test('a readable posture with an unreadable position count says both', () => {
  // The mixed case, which is the one a single boolean would collapse.
  const html = card({
    flags: {}, twin: { risk: 'high', position_count: null },
    sentinel: { risk: 'high' }, escape: { risk: 'high' }, posture: 'high',
  });
  assert.match(html, /HIGH/);
  assert.match(html, /position count unread/);
  assert.ok(!/· flat/.test(html));
});

test('one open position is singular', () => {
  const html = card({ flags: {}, twin: { risk: 'low', position_count: 1 },
    sentinel: {}, escape: {}, posture: 'low' });
  assert.match(html, /1 open position(?!s)/);
});

// ── the guard that this test is testing the shipped code ────────────────

test('the sliced region is the real one', () => {
  // blockBetween already refuses an empty slice; this refuses a slice that
  // found its markers but missed the functions they were meant to bracket.
  assert.match(BLOCK, /function guardianPostureCard/);
  assert.match(BLOCK, /function moduleChip/);
  assert.match(BLOCK, /function riskCol/);
  assert.strictEqual(typeof guardianPostureCard, 'function');
});
