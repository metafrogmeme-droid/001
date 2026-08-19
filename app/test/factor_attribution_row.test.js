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
