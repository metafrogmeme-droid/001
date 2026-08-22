/**
 * The site's deploy fingerprint — the thing that answers "did it land?".
 *
 * `/api/version` gives the platform a build/assets pair whose four
 * combinations diagnose a deploy in one line. The marketing site had no
 * equivalent, and the symptom was "we deployed but still don't see any changes
 * to website" with no way to separate a stale host from a stale browser cache
 * from a build that never contained the change.
 *
 * A fingerprint is only worth having if it moves when the site moves and holds
 * still when it does not, so both halves are driven here rather than asserted.
 * The failure that matters most is the quiet one — a hash that stays put
 * through a real change reads as "nothing was published" forever.
 */

import test from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import * as mod from '../scripts/site_fingerprint.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.join(HERE, '..', '..', 'website');

/** A minimal served tree: one page, one bundle. */
function fixture() {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-fp-'));
  fs.mkdirSync(path.join(d, 'assets'));
  fs.mkdirSync(path.join(d, 'proof'));
  fs.writeFileSync(path.join(d, 'index.html'), '<h1>home</h1>');
  fs.writeFileSync(path.join(d, 'proof', 'index.html'), '<h1>proof</h1>');
  fs.writeFileSync(path.join(d, 'assets', 'main.abc123.js'), 'console.log(1)');
  return d;
}

test('the same tree fingerprints identically, twice', () => {
  const d = fixture();
  assert.deepStrictEqual(mod.fingerprint(d), mod.fingerprint(d));
});

test('a changed page moves pages and leaves assets alone', () => {
  const d = fixture();
  const before = mod.fingerprint(d);
  fs.writeFileSync(path.join(d, 'proof', 'index.html'), '<h1>proof v2</h1>');
  const after = mod.fingerprint(d);
  assert.notStrictEqual(after.pages, before.pages, 'a page changed and pages did not move');
  assert.strictEqual(after.assets, before.assets, 'no bundle changed');
});

test('a changed bundle moves assets and leaves pages alone', () => {
  const d = fixture();
  const before = mod.fingerprint(d);
  fs.writeFileSync(path.join(d, 'assets', 'main.abc123.js'), 'console.log(2)');
  const after = mod.fingerprint(d);
  assert.notStrictEqual(after.assets, before.assets);
  assert.strictEqual(after.pages, before.pages);
});

test('a RENAMED bundle moves assets even with identical bytes', () => {
  // Vite hashes bundle names, so a rebuild that changes nothing but the hash
  // still changes what a browser fetches. Hashing contents alone would call
  // that publish a no-op — which is the reading that sends someone hunting a
  // deploy that did land.
  const d = fixture();
  const before = mod.fingerprint(d);
  fs.renameSync(path.join(d, 'assets', 'main.abc123.js'),
    path.join(d, 'assets', 'main.def456.js'));
  assert.notStrictEqual(mod.fingerprint(d).assets, before.assets);
});

test('a new page moves pages', () => {
  const d = fixture();
  const before = mod.fingerprint(d);
  fs.mkdirSync(path.join(d, 'risk'));
  fs.writeFileSync(path.join(d, 'risk', 'index.html'), '<h1>risk</h1>');
  const after = mod.fingerprint(d);
  assert.notStrictEqual(after.pages, before.pages);
  assert.strictEqual(after.counts.pages, before.counts.pages + 1);
});

test('the stamp never fingerprints itself', () => {
  // version.json lives in the tree it describes. If it were an input, writing
  // it would invalidate it, and no two builds of identical content would ever
  // agree — the hash would churn and mean nothing.
  const d = fixture();
  const before = mod.fingerprint(d);
  fs.writeFileSync(path.join(d, mod.STAMP), mod.stampText(d));
  assert.deepStrictEqual(mod.fingerprint(d), before,
    'writing the stamp changed the fingerprint it records');
});

test('an empty tree is not a fingerprint', () => {
  // Two empty builds hash equal, so "same/same" over nothing reads as
  // "nothing published" — a confident negative derived from an unread tree.
  // The CLI refuses; this pins that the count it refuses on is honest.
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-fp-empty-'));
  assert.strictEqual(mod.fingerprint(d).counts.pages, 0);
});

test('an absent directory does not throw', () => {
  assert.strictEqual(
    mod.fingerprint(path.join(os.tmpdir(), 'rc-fp-does-not-exist')).counts.pages, 0);
});

test('the committed stamp is the fingerprint of the committed site', () => {
  // The live half of the check: whatever is in website/version.json must
  // describe website/ as committed. If a page is edited by hand and the site
  // is not rebuilt, this fails — and so does the deploy check that reads it.
  const stamp = JSON.parse(fs.readFileSync(path.join(SITE, 'version.json'), 'utf8'));
  const live = mod.fingerprint(SITE);
  assert.deepStrictEqual(stamp, live,
    'website/version.json does not match website/ — run `npm run build` in site/ '
    + 'and commit, or the published fingerprint describes a site that is not there');
});

test('the real site has as many pages as it has routes', () => {
  const fp = mod.fingerprint(SITE);
  const routes = fs.readFileSync(path.join(HERE, '..', 'prerender.js'), 'utf8')
    .match(/path:\s*'\/[^']*'/g) || [];
  assert.ok(fp.counts.pages >= routes.length,
    `${fp.counts.pages} HTML files for ${routes.length} routes — a route stopped `
    + 'emitting a page and the fingerprint would have stamped it anyway');
  assert.ok(fp.counts.assets > 0, 'no bundles found under website/assets');
});
