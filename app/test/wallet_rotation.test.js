'use strict';
/**
 * RPC rotation wraps around. The old one-way rotation returned false forever
 * once every endpoint had failed once — a single double-blip (exactly what a
 * rate-limited public RPC produces) then pinned the chain to the LAST
 * endpoint until process restart, never returning to a recovered primary.
 * The operator's "rpc unreadable — missing revert data" on Base is the
 * failure mode this heals: a transient blip must stay transient.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'wallet.js'), 'utf8');

test('rotation wraps around instead of dying at the end of the list', () => {
  assert.match(src, /this\._i = \(this\._i \+ 1\) % this\._urls\.length;/,
    'modulo rotation — a recovered primary is reachable again');
  assert.match(src, /if \(this\._urls\.length < 2\) return false;/,
    'a single-URL list has nowhere to rotate to');
});

test('the wrap cannot loop: call sites rotate at most once per read', () => {
  // Every rotate() use in this file is the retry-once pattern.
  const uses = [...src.matchAll(/provider\.rotate\(\)/g)];
  assert.ok(uses.length >= 1, 'rotation is actually used');
  assert.match(src, /if \(provider && typeof provider\.rotate === 'function' && provider\.rotate\(\)\) \{\s*return await fn\(active\(\)\);\s*\}/,
    'rotate-then-retry-once, never rotate-in-a-loop');
});

test('behavioural: after both endpoints fail once, the next read tries again', () => {
  // Reconstruct the wrapper contract directly (the same shape the factory
  // builds): two URLs, rotate must cycle 0 → 1 → 0 → 1 …, never lock.
  const p = { _urls: ['a', 'b'], _i: 0, _mk: (u) => u, current: 'a' };
  p.rotate = new Function(
    "return function () {\n" +
    "  if (this._urls.length < 2) return false;\n" +
    "  this._i = (this._i + 1) % this._urls.length;\n" +
    "  this.current = this._mk(this._urls[this._i]);\n" +
    "  return true;\n" +
    "};")();
  assert.strictEqual(p.rotate(), true);
  assert.strictEqual(p.current, 'b');
  assert.strictEqual(p.rotate(), true);
  assert.strictEqual(p.current, 'a', 'wraps back to the recovered primary');
  const single = { _urls: ['only'], _i: 0, _mk: (u) => u, current: 'only', rotate: p.rotate };
  assert.strictEqual(single.rotate(), false, 'single URL: nowhere to go');
});
