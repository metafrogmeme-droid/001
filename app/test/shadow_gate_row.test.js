/*
 * A shadow-book gate's colour was a claim made off a bare total.
 *
 *     <b class="num ${g.net_r > 0 ? 'neg' : 'pos'}">
 *
 * `net_r` is a TOTAL over blocked trades. The live bot's own scoreboard on
 * 2026-09-08 held MTF_ALIGNMENT at +4.1R over 97 blocked trades — +0.042R
 * each, which a coin flip reaches — and this expression painted it exactly as
 * red as a gate at +4.1R over four. It had no threshold at all; the Telegram
 * scoreboard at least required 0.5R.
 *
 * The else-branch is the worse half. `net_r === 0` takes it, and so does a row
 * whose net_r never arrived, because `undefined > 0` is false — so both render
 * GREEN on a panel whose own caption says green means the gate SAVED YOU
 * MONEY. That is two rows of CLAUDE.md's table at once: absent-is-zero, and
 * unreadable-won.
 *
 * The verdict comes off bot/core/shadow_book.py::gate_report (a 95% interval
 * on R per blocked trade, plus MIN_GATE_TRADES) and is read here, not
 * recomputed — the rule winrate-bar.js states about MIN_RATED.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert');

const SG = require('../public/js/shadow-gate-row.js');

/** A row shaped as `gate_report()` builds one. */
function gate(over) {
  return Object.assign({
    gate: 'MTF_ALIGNMENT', n: 97, net_r: 4.1, avg_r: 0.042,
    wins: 30, losses: 60, sum_r2: 210, lower_r: -0.2519, upper_r: 0.3365,
    verdict: 'undistinguished',
  }, over || {});
}

test('the live card row carries no colour', () => {
  const c = SG.classify(gate());
  assert.strictEqual(c.tone, '', '+0.042R/trade was painted');
  assert.strictEqual(c.established, false);
  assert.match(c.note, /not distinguishable from noise/);
});

test('an established gate is still painted', () => {
  const eating = SG.classify(gate({ verdict: 'eating_edge' }));
  assert.strictEqual(eating.tone, 'neg');
  assert.strictEqual(eating.established, true);
  assert.strictEqual(eating.note, '');
  const saving = SG.classify(gate({ verdict: 'saving' }));
  assert.strictEqual(saving.tone, 'pos');
});

test('a net of exactly zero is not green', () => {
  // `net_r > 0 ? 'neg' : 'pos'` sent a measured break-even down the else
  // branch, and the panel's caption reads green as "it saved money".
  const c = SG.classify(gate({ net_r: 0, avg_r: 0, verdict: 'undistinguished' }));
  assert.notStrictEqual(c.tone, 'pos');
  assert.strictEqual(c.tone, '');
  assert.strictEqual(c.netR, 0, 'a measured zero must survive as a number');
});

test('a missing net_r is not green either', () => {
  const c = SG.classify({ gate: 'X', n: 12, verdict: null });
  assert.strictEqual(c.tone, '');
  assert.strictEqual(c.netR, null);
  assert.match(SG.buildRows([{ gate: 'X', n: 12 }]), /—/);
});

test('a row with no verdict field at all is not established', () => {
  // An older cached scan predates the field. Absent is not "fine".
  const c = SG.classify({ gate: 'OLD', n: 50, net_r: 12, avg_r: 0.24 });
  assert.strictEqual(c.established, false);
  assert.strictEqual(c.tone, '');
  assert.match(c.note, /not established/);
});

test('an unknown verdict string is not promoted', () => {
  const c = SG.classify(gate({ verdict: 'definitely_bad' }));
  assert.strictEqual(c.tone, '');
});

test('garbage never throws and never paints', () => {
  for (const bad of [null, undefined, 42, 'x', [], { verdict: 7 }]) {
    const c = SG.classify(bad);
    assert.strictEqual(c.tone, '');
    assert.strictEqual(c.established, false);
  }
  assert.strictEqual(SG.buildRows(null), '');
  assert.strictEqual(SG.buildRows([]), '');
  assert.strictEqual(SG.buildRows('nope'), '');
});

test('the per-trade figure is rendered beside the total', () => {
  const html = SG.buildRows([gate()]);
  assert.match(html, /\+4\.1R/, 'the total is gone');
  assert.match(html, /\+0\.04R\/tr/, 'the figure the verdict turns on is missing');
  assert.match(html, /×97/);
});

test('a row with no avg_r shows no per-trade figure rather than a zero', () => {
  const html = SG.buildRows([{ gate: 'X', n: 12, net_r: 3, verdict: null }]);
  assert.ok(!/0\.00R\/tr/.test(html), 'an absent average rendered as break-even');
});

test('an unestablished row says so on the row itself', () => {
  const html = SG.buildRows([gate()]);
  assert.match(html, /not distinguishable from noise/);
  // Anchored to the class attribute rather than searching for the bare word:
  // "pos" appears inside "position" and inside style properties, and this
  // assertion is the kind that misfires on prose.
  assert.ok(!/class="num pos"/.test(html), 'noise was painted green');
  assert.ok(!/class="num neg"/.test(html), 'noise was painted red');
  assert.match(html, /class="num muted"/);
});

test('an established row gets its class', () => {
  assert.match(SG.buildRows([gate({ verdict: 'eating_edge' })]),
    /class="num neg"/);
  assert.match(SG.buildRows([gate({ verdict: 'saving', net_r: -30 })]),
    /class="num pos"/);
});

test('the gate label is escaped and clipped', () => {
  const html = SG.buildRows([gate({ gate: '<img src=x onerror=1>' })]);
  assert.ok(!html.includes('<img'), 'a gate label reached the DOM as markup');
  assert.match(html, /&lt;img/);
  const long = SG.classify(gate({ gate: 'Z'.repeat(80) }));
  assert.strictEqual(long.label.length, SG.MAX_LABEL);
});

test('rows are capped', () => {
  const many = Array.from({ length: 20 }, (_, i) => gate({ gate: 'G' + i }));
  const html = SG.buildRows(many);
  assert.strictEqual((html.match(/kv-row/g) || []).length, SG.MAX_ROWS);
});

test('establishedCount counts only what carries a verdict', () => {
  assert.strictEqual(SG.establishedCount([
    gate(), gate({ verdict: 'eating_edge' }), gate({ verdict: 'saving' }),
    { gate: 'OLD', n: 5 },
  ]), 2);
  assert.strictEqual(SG.establishedCount(null), 0);
});

test('the panel caption uses establishedCount rather than exporting it dark', () => {
  // A module nothing calls is indistinguishable from one that does not work,
  // and that applies to an exported function nobody reaches too.
  const fs = require('node:fs');
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(fs.readFileSync(
    require('node:path').join(__dirname, '../public/js/dashboard.js'), 'utf8'));
  assert.ok(src.includes('establishedCount('),
    'establishedCount is exported and reached by nothing');
});

/*
 * The panel wiring. The renderer lives inline in 6k lines of dashboard.js, so
 * the seam is the module — but nothing above proves the panel USES it, and a
 * fix that lands in the model and not the caller has not landed (#999: a card
 * that was present, correct, tested and rendered zero times).
 */
test('the dashboard panel renders through the module, not its own ternary', () => {
  const fs = require('node:fs');
  // STRIP COMMENTS FIRST. The first draft of this assertion failed against a
  // correct dashboard.js, because the comment recording the old expression
  // quotes it verbatim — which is indistinguishable, to a scanner, from the
  // code doing it. CLAUDE.md counts four prior false failures from exactly
  // this; that made five.
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(fs.readFileSync(
    require('node:path').join(__dirname, '../public/js/dashboard.js'), 'utf8'));
  const i = src.indexOf("C('eshadow')");
  assert.ok(i > 0, 'the shadow panel is gone');
  const body = src.slice(i, i + 1400);
  assert.ok(body.includes('RCShadowGates'), 'the panel does not use the module');
  assert.ok(!body.includes("net_r > 0 ?"), 'the bare-total ternary is back');
});

test('the page loads the module before dashboard.js', () => {
  const fs = require('node:fs');
  const html = fs.readFileSync(
    require('node:path').join(__dirname, '../public/dashboard.html'), 'utf8');
  // Anchored to the SCRIPT TAG, not the bare filename: dashboard.html mentions
  // "js/dashboard.js" in an HTML comment ninety lines above the tag, so the
  // first draft compared a comment's position against a script's and failed on
  // a correct page. The same misfire as the test above, one file over.
  const at = (f) => html.search(
    new RegExp('<script[^>]+src="/js/' + f.replace('.', '\\.') + '\\?'));
  const mod = at('shadow-gate-row.js');
  const dash = at('dashboard.js');
  assert.ok(mod > 0, 'shadow-gate-row.js is not loaded — the panel renders nothing');
  assert.ok(dash > 0, 'dashboard.js is not loaded at all');
  assert.ok(mod < dash, 'the module must be declared before its caller');
});
