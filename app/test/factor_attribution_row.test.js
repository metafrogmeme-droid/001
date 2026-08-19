'use strict';
/**
 * A percentage rendered beside nothing, and a zero nobody measured.
 *
 * The Guardian flight record seals a factor attribution for every executed
 * decision — how much of the weighted vote each input actually drove — and it
 * was rendered NOWHERE. That is why the recorder sealing every one of them
 * with a blank name (`f.get("name")` against a model whose field is `factor`)
 * survived: a field no surface reads is a field nothing can check.
 *
 * The row exists now, and it deliberately does NOT reuse `voteRow`, which does
 *
 *     Number(v.contribution) || 0
 *
 * so an unreadable contribution renders as a measured `0.00` beside a
 * zero-width bar. A percentage nobody measured is not zero percent, and a bar
 * of length zero is a claim about the data.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// The renderer is inline in a 7k-line browser script, so the function body is
// lifted and run in a VM — the pattern this repo uses for the engine-status
// chip, for the same reason.
const SRC = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const from = SRC.indexOf('function factorRow(f) {');
assert.ok(from > 0, 'factorRow is gone from dashboard.js');
const body = SRC.slice(from, SRC.indexOf('\n  }', from) + 4);
const ctx = { esc: (s) => String(s == null ? '' : s).replace(/[&<>"]/g, ''), isFinite };
vm.createContext(ctx);
vm.runInContext(body + '\nthis.factorRow = factorRow;', ctx);
const factorRow = ctx.factorRow;

test('a named factor shows its name and its share', () => {
  const html = factorRow({ name: 'mtf_alignment', contribution_pct: 29.6, direction: 'bullish' });
  assert.match(html, /mtf alignment/, 'underscores are not humanised');
  assert.match(html, /29\.6%/);
  assert.match(html, /width:29\.6%/, 'the bar does not reflect the share');
});

test('an unmeasured contribution is a dash, never a zero', () => {
  // THE POINT. `Number(x) || 0` would print 0.0% here, which reads as "this
  // factor contributed nothing" rather than "we do not know what it did".
  for (const missing of [null, undefined, NaN, '', 'n/a']) {
    const html = factorRow({ name: 'rsi', contribution_pct: missing, direction: 'bullish' });
    assert.match(html, /—/, `contribution_pct ${JSON.stringify(missing)} did not render as unknown`);
    assert.ok(!/0\.0%/.test(html), `contribution_pct ${JSON.stringify(missing)} rendered as a measured zero`);
    assert.ok(!/width:[\d.]+%/.test(html),
      'an unknown share still drew a bar — a bar of any length is a claim');
  }
});

test('a real zero is still a real zero', () => {
  // The opposite error, and just as bad: 0.0% is a measured result and must
  // not be muted into "unknown".
  const html = factorRow({ name: 'obv', contribution_pct: 0, direction: 'neutral' });
  assert.match(html, /0\.0%/);
  assert.ok(!/—/.test(html));
});

test('a nameless factor says so instead of rendering a blank label', () => {
  // Exactly what the seal used to contain. A 59.3% bar with an empty label is
  // the same claim-attached-to-nothing, moved to the screen.
  const html = factorRow({ name: '', contribution_pct: 59.3, direction: 'bullish' });
  assert.match(html, /unnamed factor/);
  assert.match(html, /59\.3%/);
});

test('direction drives the colour, and neutral is not green', () => {
  assert.match(factorRow({ name: 'a', contribution_pct: 10, direction: 'bullish' }), /--up/);
  assert.match(factorRow({ name: 'a', contribution_pct: 10, direction: 'bearish' }), /--down/);
  const neutral = factorRow({ name: 'a', contribution_pct: 10, direction: 'neutral' });
  assert.ok(!/--up|--down/.test(neutral), 'a neutral factor is painted as a direction');
});

test('the card actually renders the rows', () => {
  // The wiring. Every test above drives factorRow directly and none of them
  // prove the flight-record card calls it.
  assert.match(SRC, /const factorRows = \(explain\.factors \|\| \[\]\)\.map\(factorRow\)/,
    'the card no longer builds the factor rows');
  assert.match(SRC, /Factor attribution — share of the weighted decision/,
    'the section is gone, so the rows have nowhere to appear');
});

// ── the derivation, not only the conclusion ──────────────────────────────────

const cfrom = SRC.indexOf('function chainStep(st) {');
assert.ok(cfrom > 0, 'chainStep is gone from dashboard.js');
const cbody = SRC.slice(cfrom, SRC.indexOf('\n  }', cfrom) + 4);
vm.runInContext(cbody + '\nthis.chainStep = chainStep;', ctx);
const chainStep = ctx.chainStep;

test('a step shows its stage and what came out of it', () => {
  const html = chainStep({ stage: 'regime_detection', input: 'ADX=28, +DI=31',
                           output: 'Regime: TREND_UP', impact: 'bullish' });
  assert.match(html, /regime detection/, 'underscores are not humanised');
  assert.match(html, /Regime: TREND_UP/);
  assert.match(html, /from ADX=28/, 'the inputs to the step are not shown');
  assert.match(html, /--up/, 'a bullish step is not coloured as one');
});

test('a step with no recorded output says so rather than rendering blank', () => {
  // A stage that produced nothing is a fact. An empty cell beside a coloured
  // bar reads as a stage that ran and is simply not worth showing.
  const html = chainStep({ stage: 'smart_money', input: '', output: '', impact: 'neutral' });
  assert.match(html, /no output recorded/);
  assert.ok(!/from /.test(html), 'an absent input rendered an empty "from" clause');
});

test('an unnamed stage is labelled, not left blank', () => {
  assert.match(chainStep({ stage: '', output: 'x', impact: 'neutral' }), /unnamed stage/);
});

test('neutral is not painted as a direction', () => {
  const n = chainStep({ stage: 's', output: 'o', impact: 'neutral' });
  assert.ok(!/--up|--down/.test(n));
});

test('a truncated chain says how much is missing', () => {
  // THE POINT OF SEALING THE TOTAL. Twelve steps shown as if they were the
  // whole derivation is a partial presented as a total — the same shape as a
  // scan card cut at 500 characters and printed whole.
  assert.match(SRC, /chainTotal > chain\.length/,
    'the card no longer compares the sealed total against what it shows');
  assert.match(SRC, /Showing \$\{chain\.length\} of \$\{chainTotal\} steps/,
    'a truncated reasoning chain is displayed as if it were complete');
});

test('the card actually renders the chain', () => {
  assert.match(SRC, /const chainRows = chain\.map\(chainStep\)\.join\(''\)/,
    'the card no longer builds the chain rows');
  assert.match(SRC, /Reasoning chain — how the decision was reached/,
    'the section is gone, so the steps have nowhere to appear');
});
