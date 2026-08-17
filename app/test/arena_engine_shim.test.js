'use strict';
/**
 * `lib/arena.js` is now a one-line re-export of `public/js/arena_engine.js`.
 *
 * That move exists so the browser practice sandbox and the server run the SAME
 * rules — a sandbox with its own liquidation maths teaches a habit that costs
 * money the first time somebody uses it for real.
 *
 * A shim has one failure mode and it is quiet: dropping an export. Nothing
 * fails here; the caller fails, later, somewhere else, with `undefined is not
 * a function` and no hint that a re-export is responsible. So the shim is
 * pinned to the module it forwards.
 *
 * It also re-checks the thing the move endangered. `arena_refusal_codes` scans
 * the rules for misleading wording; pointed at the shim it would pass
 * vacuously, because a file with no wording in it trivially lacks the banned
 * string. That test now reads the engine, and this one makes sure the engine
 * is still where it thinks it is.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const shim = require('../lib/arena.js');
const engine = require('../public/js/arena_engine.js');

test('the shim forwards every export, by identity', () => {
  const a = Object.keys(shim).sort();
  const b = Object.keys(engine).sort();
  assert.deepStrictEqual(a, b, 'the shim and the engine disagree on exports');
  assert.ok(a.length >= 15, `only ${a.length} exports — something was dropped`);
  for (const k of a) assert.strictEqual(shim[k], engine[k], `${k} is not the same value`);
});

test('the rules really are in the engine, not left behind in the shim', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'arena.js'), 'utf8');
  assert.ok(!/function (posPnl|liqPrice|validateOpen)/.test(src),
    'a rule is still defined in the shim — there are two implementations again');
  const eng = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'arena_engine.js'), 'utf8');
  for (const fn of ['posPnl', 'liqPrice', 'isLiquidated', 'equity',
    'validateOpen', 'validateTpSl', 'exitCheck', 'trailRatchet']) {
    assert.ok(eng.includes(`function ${fn}`), `${fn} is missing from the engine`);
  }
});

test('the engine loads in a browser as well as in node', () => {
  // UMD both ways. If the browser branch broke, the sandbox silently falls
  // back to nothing and the page renders an empty panel — which is exactly the
  // "absent rendered as fine" shape, in a feature about learning the rules.
  const eng = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'arena_engine.js'), 'utf8');
  assert.match(eng, /module\.exports = factory\(\)/, 'the node branch');
  assert.match(eng, /root\.ArenaEngine = factory\(\)/, 'the browser branch');
  assert.ok(!/\brequire\s*\(/.test(eng.replace(/\/\*[\s\S]*?\*\//g, '')),
    'the engine must stay dependency-free to run in a browser');
});
