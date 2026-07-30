'use strict';
/**
 * The analyze-budget forecast reaches the primary surface.
 *
 * The engine forecasts, from its last MEASURED batch rate, whether the scan
 * universe fits the analyze phase cap (161 symbols at ~3.3s/signal needs
 * ~531s against a 300s cap → 71 will never be analysed). #991 surfaced that
 * on /status and /health. The website is the PRIMARY surface — an operator
 * watching the Engine page saw 37 consecutive timeout ticks with nothing
 * naming the cause.
 *
 * Contract pinned here (same rules as the bot side):
 * - rendered only when the payload carries the block — unmeasured is ABSENT,
 *   and absence must not render as "measured, and fine";
 * - visually alarming only on a real shortfall — a warning that fires on
 *   healthy ticks gets ignored on the tick that matters;
 * - a shortfall names the count of symbols skipped, not a percentage
 *   ("57%" was read as progress rather than as a shortfall).
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const dash = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('the engine modules panel renders the analyze-budget forecast', () => {
  assert.match(dash, /f\.analyze_capacity/,
    'the emods panel must read features.analyze_capacity');
  assert.match(dash, /Analyze budget/,
    'the tile must be named for what it measures');
});

test('a shortfall is visually distinct and counts symbols, not percent', () => {
  assert.match(dash, /Analyze budget · SHORT/);
  const tileBlock = dash.slice(dash.indexOf('f.analyze_capacity'),
                               dash.indexOf('f.entry_timing'));
  assert.match(tileBlock, /shortfall/,
    'the shortfall count must be shown');
  assert.ok(!/toFixed\(0\)\s*\+\s*'%'/.test(tileBlock) && !/%\s*of/.test(tileBlock),
    'the shortfall is a count — a percentage was misread as progress live');
});

test('absent means absent — no placeholder tile for an unmeasured forecast', () => {
  // The tile is inside `if (f.analyze_capacity)` — no else branch that
  // renders zeros. Pin the guard exists rather than trusting the layout.
  const guarded = /if \(f\.analyze_capacity\)/.test(dash);
  assert.ok(guarded, 'the tile must be conditional on the block being present');
});

test('the alarm only fires on a real shortfall', () => {
  const tileBlock = dash.slice(dash.indexOf('f.analyze_capacity'),
                               dash.indexOf('f.entry_timing'));
  assert.match(tileBlock, /\(a\.shortfall \|\| 0\) > 0/,
    'SHORT styling must key off shortfall > 0, not off mere presence');
});
