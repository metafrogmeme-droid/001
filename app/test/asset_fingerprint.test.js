'use strict';

/**
 * The deploy check was blind to more than half the app.
 *
 * `build` hashes server-side .js only — server.js, auth.js, db.js, lib/*.js,
 * routes/*.js. That is the right scope for what it answers, and it left a hole
 * big enough to ship a whole fix through: #973's SSE reconnect lives entirely
 * in public/js, so /api/version reported an unchanged `build` before and after
 * that deploy. The one instrument we have for "did it land?" could not see it,
 * and the only way to check was view-source on a script tag.
 *
 * That is the same defect as everything else this week, one level up: an
 * instrument whose coverage is narrower than the claim people read off it.
 * A fingerprint that only moves for server code, consulted as "is the fix
 * live?", answers a different question than the one being asked.
 *
 * `assets` is kept SEPARATE rather than folded into `build`, because the two
 * genuinely diverge: browsers cache assets independently of the server, so
 * "server updated, tab still on the old bundle" is a real state and one number
 * could not express it.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const version = require('../lib/version');
const PUB = path.join(__dirname, '..', 'public');

// ── it exists and has the documented shape ────────────────────────────────

test('the asset fingerprint has the same shape as the build one', () => {
  const a = version.assetFingerprint();
  assert.match(a, /^[0-9a-f]{12}\+\d+$/,
    'an operator compares these by eye; they should look alike');
});

test('it counts the files it actually hashed', () => {
  const n = Number(version.assetFingerprint().split('+')[1]);
  const expected = fs.readdirSync(path.join(PUB, 'js')).filter(f => f.endsWith('.js')).length
    + fs.readdirSync(PUB).filter(f => f.endsWith('.html')).length
    + fs.readdirSync(PUB).filter(f => f.endsWith('.css')).length;
  assert.equal(n, expected);
  assert.ok(n > 20, 'the public surface is not two files');
});

test('/api/version carries both, and omits rather than nulls', () => {
  const info = version.buildInfo();
  assert.ok(info.build, 'server fingerprint still reported');
  assert.ok(info.assets, 'client fingerprint now reported too');
  assert.notEqual(info.build, info.assets, 'two questions, two answers');
  // The omit-rather-than-null rule the module already follows.
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'version.js'), 'utf8');
  assert.match(src, /if \(BUILD\.assets\) out\.assets = BUILD\.assets;/);
});

// ── the property that makes it useful ─────────────────────────────────────

test('it changes when a client file changes, and only then', (t) => {
  // The whole point. Exercised against a real temp tree rather than asserted.
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-fp-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'js'), { recursive: true });
  fs.writeFileSync(path.join(root, 'js', 'app.js'), 'const a = 1;');
  fs.writeFileSync(path.join(root, 'index.html'), '<p>hi</p>');
  fs.writeFileSync(path.join(root, 'styles.css'), 'body{}');

  // Re-run the module's own algorithm against this tree by loading a fresh
  // copy whose public/ is the temp dir.
  const digest = () => {
    delete require.cache[require.resolve('../lib/version')];
    const mod = require('../lib/version');
    return mod.__digestTree ? mod.__digestTree(root) : null;
  };

  const before = digest();
  assert.ok(before, 'the test seam must be exported for this to mean anything');
  assert.equal(digest(), before, 'an unchanged tree must hash identically');

  fs.writeFileSync(path.join(root, 'js', 'app.js'), 'const a = 2;');
  assert.notEqual(digest(), before, 'a changed client file must move the digest');

  fs.writeFileSync(path.join(root, 'js', 'app.js'), 'const a = 1;');
  assert.equal(digest(), before, 'and reverting must restore it');
});

test('a new client file moves it even with every existing file untouched', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-fp-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'js'), { recursive: true });
  fs.writeFileSync(path.join(root, 'js', 'app.js'), 'x');
  const { __digestTree } = require('../lib/version');

  const before = __digestTree(root);
  fs.writeFileSync(path.join(root, 'js', 'zz.js'), 'x');
  const after = __digestTree(root);
  assert.notEqual(after, before);
  assert.equal(Number(after.split('+')[1]), Number(before.split('+')[1]) + 1);
});

test('a renamed file moves it even when the bytes are identical', (t) => {
  // The path is hashed alongside the content, so a rename is a change.
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-fp-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'js'), { recursive: true });
  fs.writeFileSync(path.join(root, 'js', 'a.js'), 'same bytes');
  const { __digestTree } = require('../lib/version');
  const before = __digestTree(root);
  fs.renameSync(path.join(root, 'js', 'a.js'), path.join(root, 'js', 'b.js'));
  assert.notEqual(__digestTree(root), before);
});

test('an empty or missing tree is null, never a fake digest', (t) => {
  const { __digestTree } = require('../lib/version');
  assert.equal(__digestTree(path.join(os.tmpdir(), 'rc-does-not-exist-' + process.pid)), null);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-fp-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  assert.equal(__digestTree(root), null, 'nothing hashed must not read as a value');
});

test('non-asset files are ignored so images cannot churn the digest', (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-fp-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'js'), { recursive: true });
  fs.writeFileSync(path.join(root, 'js', 'app.js'), 'x');
  const { __digestTree } = require('../lib/version');
  const before = __digestTree(root);
  fs.writeFileSync(path.join(root, 'logo.png'), Buffer.from([1, 2, 3]));
  fs.writeFileSync(path.join(root, 'llms.txt'), 'text');
  assert.equal(__digestTree(root), before, 'only code and markup are hashed');
});

// ── the two fingerprints stay independent ─────────────────────────────────

test('a server-only change does not move the asset digest', () => {
  // Structural: the asset collector must not reach outside public/.
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'version.js'), 'utf8');
  const block = src.slice(src.indexOf('function digestTree'),
                          src.indexOf('function compute'));
  assert.ok(!/FINGERPRINT_DIRS|FINGERPRINT_FILES/.test(block),
    'the asset digest must not include the server set');
  assert.match(block, /'public'/);
});

test('the server fingerprint still covers exactly what it did', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'version.js'), 'utf8');
  assert.match(src, /FINGERPRINT_DIRS = \['lib', 'routes'\]/);
  assert.match(src, /FINGERPRINT_FILES = \['server\.js', 'auth\.js', 'db\.js'\]/);
  // Refactoring the two to share digestOf must not have widened `build` —
  // a silently broadened fingerprint invalidates every expected value ever
  // quoted to an operator.
  const n = Number(version.fingerprint().split('+')[1]);
  const app = path.join(__dirname, '..');
  const expected = 3
    + fs.readdirSync(path.join(app, 'lib')).filter(f => f.endsWith('.js')).length
    + fs.readdirSync(path.join(app, 'routes')).filter(f => f.endsWith('.js')).length;
  assert.equal(n, expected);
});

// ── §F-15 ─────────────────────────────────────────────────────────────────

test('nothing outside the public directory is hashed', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'version.js'), 'utf8');
  // The CODE, not the doc above it — the doc names what it excludes, and a
  // test that reads prose would fail on its own explanation.
  const code = src.slice(src.indexOf('function digestTree'),
                         src.indexOf('function compute'));
  for (const forbidden of ['.env', 'data/', 'secrets']) {
    assert.ok(!code.includes(forbidden), `asset digest must not reach ${forbidden}`);
  }
  // Everything under public/ is served to anyone who asks, so a hash of it
  // can carry nothing that was not already public. The rationale is stated.
  const doc = src.slice(src.indexOf('The same question, asked of the code'),
                        src.indexOf('function digestTree'));
  assert.match(doc, /served publicly by definition/);
});
