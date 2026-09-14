'use strict';
/**
 * The paper-arb panel prints the bot's VERDICT — and only the bot's.
 *
 * The panel printed a total paper carry and "a real 2-venue round trip costs
 * ~0.24% of notional in fees" and no verdict, so the reader made one from a
 * total over however few entries happened to accrue it. The bot's report
 * carries `arb.verdict` now — four states, a sentence in percent of the
 * notional — and `arbVerdictLine` prints it when present. An older bot
 * pushes no verdict and the panel prints NOTHING about one: a verdict
 * derived here from the total would be the second reading the bot's seam
 * exists to replace.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { codeOnly } = require('./helpers/code_only.js');
const { loaderBodies } = require('./helpers/loaders.js');

const RAW = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const SRC = codeOnly(RAW);

function sliceFn(src, head) {
  const i = src.indexOf(head);
  assert.ok(i > -1, `${head} not found`);
  const j = src.indexOf('\n  }\n', i);
  return src.slice(i, j + 4);
}

function line(arb) {
  const ctx = { esc: (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])) };
  vm.createContext(ctx);
  vm.runInContext(sliceFn(RAW, 'function arbVerdictLine(arb)') + '\nthis.fn = arbVerdictLine;', ctx);
  return ctx.fn(arb);
}

const SENTENCE = 'survives fees — mean net +0.856% of notional per entry after the 0.24% round trip, 95% interval +0.856%..+0.856%, 12 closed entries, 576h held';

test('the verdict is printed in the bot\'s own words, marked by its state', () => {
  const html = line({ verdict: { state: 'survives', sentence: SENTENCE } });
  assert.ok(html.includes('🟢 Verdict: <b>' + SENTENCE + '</b>'), html);
  assert.ok(html.includes('data-state="survives"'));
  assert.ok(line({ verdict: { state: 'does_not', sentence: 'does not survive fees — …' } }).startsWith('<p class="small mt-2 arb-verdict" data-state="does_not">🔴'));
  assert.ok(line({ verdict: { state: 'thin', sentence: 'record too thin — 3 closed entries over 24h held' } }).includes('🟡 Verdict'));
  assert.ok(line({ verdict: { state: 'unread', sentence: 'could not read the record (PermissionError)' } }).includes('⚠️ Verdict'));
});

test('an older bot that pushes no verdict gets no verdict invented for it', () => {
  assert.equal(line({ carries: [{ base: 'BTC', earned_usd: 91.7 }], snapshots: 500 }), '');
  assert.equal(line({ verdict: null }), '');
  assert.equal(line({ verdict: { state: 'survives' } }), '', 'a state with no sentence is not printed as one');
  assert.equal(line({ verdict: { state: 'survives', sentence: '   ' } }), '');
  assert.equal(line({ verdict: { state: 'green', sentence: 'x' } }), '', 'a state outside the four is not a verdict');
  assert.equal(line(null), '');
});

test('the sentence is text, never markup', () => {
  const html = line({ verdict: { state: 'thin', sentence: '<img src=x onerror=alert(1)> record too thin' } });
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
});

test('the paper-arb loader prints the line and derives no verdict of its own', () => {
  const p = loaderBodies(SRC).find((x) => x.target === "C('arb')");
  assert.ok(p, 'the arb panel loader exists');
  assert.ok(p.body.includes('arbVerdictLine(arb)'), 'the loader prints the bot\'s verdict');
  for (const word of ['survives fees', 'does not survive', 'too thin']) {
    assert.ok(!p.body.includes(word), `the loader must not spell a verdict itself: ${word}`);
  }
});
