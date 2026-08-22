'use strict';
/**
 * The verifier a reader's browser runs was never the one that was verified.
 *
 * `app/lib/canonical.js` is the JS counterpart of the Python sealer's
 * `json.dumps(bundle, sort_keys=True, separators=(",",":"),
 * ensure_ascii=False)`, and `verify_anyone.test.js` pins it against a fixture
 * generated with CPython — including a non-ASCII key, embedded quotes, and the
 * string-numbers the CSF invariant guarantees.
 *
 * It is imported by NOTHING except that test.
 *
 * `app/public/proof.html` — the page whose own copy says it "re-derives that
 * hash in your own browser", so you are "trusting math on your machine, not
 * our word" — carries a SECOND implementation, inline, which is the one that
 * actually runs. Found by a reachability sweep of app/lib and app/routes:
 * 198 modules, exactly one imported by no non-test file.
 *
 * THE TWO HAD DRIFTED, and the drift changes the hash:
 *
 *     {a:'1', b:undefined}   lib {"a":"1"}     page {"a":"1","b":undefined}
 *     {a: NaN}               lib throws        page {"a":null}
 *
 * The first is not valid JSON at all. Neither input is reachable from a bundle
 * that arrived over the wire — JSON has no `undefined`, and every number in a
 * sealed bundle is already a string — so this was a LATENT divergence, not a
 * live miscount, and the difference is worth stating rather than dressing up.
 *
 * What was not latent: the copy that ships had nothing keeping it in agreement
 * with the sealer, on the one page whose entire claim is that you do not have
 * to trust the operator. `code_only.js` records the general form — the same bug
 * in two copies is one bug with two places to recur — and this is its sharper
 * version, where the copy under test is the copy nobody runs.
 *
 * WHY NOT JUST IMPORT THE LIBRARY. `proof.html` is a static page served to a
 * browser with no bundler, and `app/lib` is CommonJS outside the public root.
 * Making it importable is a build-system change on a page that currently has
 * no build step. Extracting the shipped function and testing THAT is the
 * cheaper answer and the one this repo already uses for browser code —
 * `stream_reconnect` and `engine_status_scenarios` both slice a function out
 * and run it.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');

const { canonicalStringify } = require('../lib/canonical');

/** The `canonical` function as it exists in the shipped page. */
function shippedCanonical() {
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'proof.html'), 'utf8');
  const start = html.indexOf('function canonical(obj) {');
  assert.ok(start > 0, 'the browser canonicalizer is gone from proof.html');
  // Brace-matched to its own close, so no comment or heading bounds this.
  let i = html.indexOf('{', start);
  let depth = 0;
  let end = -1;
  for (; i < html.length; i += 1) {
    if (html[i] === '{') depth += 1;
    else if (html[i] === '}') { depth -= 1; if (depth === 0) { end = i + 1; break; } }
  }
  assert.ok(end > start, 'could not find the end of the browser canonicalizer');
  const src = html.slice(start, end);
  const ctx = { JSON, Object, Array, Number, Error };
  vm.createContext(ctx);
  vm.runInContext(`${src}; this.canonical = canonical;`, ctx);
  return ctx.canonical;
}

/**
 * The CPython fixture, identical to verify_anyone.test.js.
 *
 * Duplicated deliberately rather than exported from there: this file's whole
 * subject is two copies of one algorithm drifting apart, and a shared fixture
 * that someone "fixes" in one place would hide exactly that.
 */
const FIXTURE = {
  bundle: { z: '1.50', a: { 'β': 'x', b: ['2', null, true] },
    list: [{ k: 'v' }], note: 'línea…"q"' },
  canonical: '{"a":{"b":["2",null,true],"β":"x"},"list":[{"k":"v"}],"note":"línea…\\"q\\"","z":"1.50"}',
  sha256: 'a327937870bd388f1a97062b222b84dad818dc0a1c274ddab643ecb2c46e7452',
};

test('the browser copy reproduces the CPython bytes, like the library does', () => {
  const page = shippedCanonical();
  assert.equal(page(FIXTURE.bundle), FIXTURE.canonical,
    'the function a reader actually runs no longer matches the Python sealer');
  const h = crypto.createHash('sha256')
    .update(Buffer.from(page(FIXTURE.bundle), 'utf8')).digest('hex');
  assert.equal(h, FIXTURE.sha256);
});

test('the two implementations agree on every shape that reaches a bundle', () => {
  const page = shippedCanonical();
  const cases = [
    ['the fixture', FIXTURE.bundle],
    ['key order', { note: 'x', a: { b: ['2', null, true], 'β': 'y' }, z: '1' }],
    ['nested arrays', { a: [[{ b: '1' }], []] }],
    ['empty object', {}],
    ['null and bools', { a: null, b: true, c: false }],
    ['string numbers', { a: '0', b: '-1.5', c: '1e9' }],
    ['non-ASCII', { 'ключ': 'значение', 'β': '…' }],
  ];
  for (const [name, v] of cases) {
    assert.equal(page(v), canonicalStringify(v),
      `${name}: the shipped verifier and the pinned library disagree — one of `
      + 'them is wrong about the seal and the page cannot say which');
  }
});

test('they agree on the shapes that made them diverge', () => {
  // NOT REACHABLE FROM THE WIRE — JSON has no `undefined`, and the CSF
  // invariant keeps every number a string. Kept because the divergence was
  // real and a future edit could make one of them reachable; a test that only
  // covers what is reachable today records nothing about why it was written.
  const page = shippedCanonical();
  assert.equal(page({ a: '1', b: undefined }), canonicalStringify({ a: '1', b: undefined }),
    'an undefined value serialises differently in the two copies — the shipped '
    + 'one used to emit `{"a":"1","b":undefined}`, which is not JSON');
  assert.equal(page({ x: { y: undefined, z: '2' } }),
    canonicalStringify({ x: { y: undefined, z: '2' } }),
    'nested undefined still diverges');
  assert.throws(() => page({ a: NaN }), /non-finite/,
    'the shipped copy turns NaN into null instead of refusing — a silent hash '
    + 'change where the library raises');
});

test('the library is still imported by nothing, and that is now recorded', () => {
  // A MODULE NOTHING CALLS IS INDISTINGUISHABLE FROM ONE THAT DOES NOT WORK.
  // Kept as a live measurement rather than a comment: if `canonical.js` ever
  // acquires a real caller, this fails and the duplication above should be
  // resolved by importing it rather than by mirroring it.
  const roots = [path.join(__dirname, '..', 'lib'), path.join(__dirname, '..', 'routes')];
  const files = [path.join(__dirname, '..', 'server.js'), path.join(__dirname, '..', 'auth.js')];
  for (const d of roots) {
    for (const f of fs.readdirSync(d)) if (f.endsWith('.js')) files.push(path.join(d, f));
  }
  const importers = files.filter((f) =>
    !f.endsWith(path.join('lib', 'canonical.js'))
    && /require\(\s*['"][^'"]*canonical(?:\.js)?['"]\s*\)/.test(fs.readFileSync(f, 'utf8')));
  assert.deepStrictEqual(importers, [],
    'lib/canonical.js now has a real caller — good. The browser copy in '
    + 'proof.html should stop being a second implementation and start being '
    + 'the same one, and this test should be replaced by that.');
});
