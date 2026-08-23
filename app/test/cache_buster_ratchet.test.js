'use strict';
/**
 * A changed bundle that keeps its `?v=` never reaches a returning browser.
 *
 * CLAUDE.md has said this for months — "a moved `assets` still is not a
 * *fetched* file; browsers cache on the `?v=` in the script tag" — and it was
 * still broken twice, both times in the commit that most needed it:
 *
 *   app.js        changed by the M14 session-cookie work (#55) and left at v=7.
 *                 That commit moved LOGGED_IN from "is there a token in
 *                 localStorage" to "is there an rc_auth cookie". Every browser
 *                 that had ever loaded the site kept the OLD app.js, computed
 *                 LOGGED_IN from a localStorage token that no longer exists,
 *                 and showed "Log in to see your open positions" to people who
 *                 were signed in — while the landing page, whose logic is
 *                 inline in a no-cache HTML document, showed their account.
 *                 One session, two answers, for eleven days.
 *
 *   dashboard.js  changed by "Six read failures that rendered as measurements"
 *                 and left at v=137. Six honesty fixes that never shipped to
 *                 anyone who had visited before.
 *
 * Knowing the rule was not enough, which is this repo's oldest lesson. So the
 * rule is a ratchet now: `test/asset_versions.json` records each bundle's
 * content hash beside the `?v=` it shipped under. Change the bundle without
 * changing the number and this fails, naming the file and the number to use.
 *
 * Updating the manifest is part of bumping the version, in the same commit —
 * the `known_failures.txt` rule, for the same reason. It is deliberately not
 * auto-generated at test time: a manifest that regenerates itself agrees with
 * whatever it finds and asserts nothing.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const PUB = path.join(__dirname, '..', 'public');
const MANIFEST = path.join(__dirname, 'asset_versions.json');

const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));

/**
 * Every versioned asset reference in the shipped pages, keyed by the path
 * under public/ so the manifest key and the file on disk cannot drift apart.
 *
 * THIS USED TO MATCH ONLY `/js/*.js`. styles.css is linked by every page on
 * the site and was therefore the most-cached file here, and it sat outside the
 * ratchet for its whole life — a stylesheet edit that kept its `?v=` would
 * reach nobody who had visited before, silently, which is the entire subject of
 * this file. Found on 2026-08-17 by changing styles.css and watching the
 * ratchet say nothing; the manifest had no `styles.css` key, so `if (!rec)
 * continue` skipped it and the "every versioned bundle is in the manifest"
 * test never saw it either, because the reference scan could not produce it.
 *
 * A guard with a blind spot over the biggest asset is not a smaller guard —
 * it reads as coverage while providing none. The pattern is matched on the
 * extension now, so a versioned `.css` is ratcheted on the same terms as a
 * versioned `.js` and a new asset type is one character away.
 */
/**
 * Every place a versioned asset is referenced, and where.
 *
 * NOT JUST public/*.html, and that extension is this guard's own lesson
 * arriving one source-type later. `/embed/signals` is a page like any other —
 * a stylesheet, two scripts, all carrying `?v=` — but it is assembled and sent
 * by `routes/embed.js` rather than sitting on disk as a file. A scan that reads
 * only .html cannot see it, so its bundles were unratcheted while the manifest
 * test reported everything covered: the same "reads as coverage while providing
 * none" the docstring above describes, produced by the same assumption about
 * where pages live.
 *
 * Route modules are scanned with the identical pattern, so a page gains
 * ratcheting by being a page, not by being a file.
 */
function sources() {
  const out = [];
  for (const f of fs.readdirSync(PUB).filter((x) => x.endsWith('.html'))) {
    out.push([f, fs.readFileSync(path.join(PUB, f), 'utf8')]);
  }
  const ROUTES = path.join(__dirname, '..', 'routes');
  for (const f of fs.readdirSync(ROUTES).filter((x) => x.endsWith('.js'))) {
    out.push([`routes/${f}`, fs.readFileSync(path.join(ROUTES, f), 'utf8')]);
  }
  return out;
}

function references() {
  const found = new Map();
  for (const [name, src] of sources()) {
    for (const m of src.matchAll(/["'(]\/((?:[A-Za-z0-9_.-]+\/)*[A-Za-z0-9_.-]+\.(?:js|css))\?v=(\d+)/g)) {
      if (!found.has(m[1])) found.set(m[1], new Map());
      const pages = found.get(m[1]);
      const v = Number(m[2]);
      if (!pages.has(v)) pages.set(v, []);
      pages.get(v).push(name);
    }
  }
  return found;
}

const sha = (p) => crypto.createHash('sha256')
  .update(fs.readFileSync(p)).digest('hex').slice(0, 16);

// ── the ratchet itself ────────────────────────────────────────────────────

test('a changed bundle carries a changed ?v=', () => {
  const stale = [];
  for (const [asset] of references()) {
    const file = path.join(PUB, asset);
    if (!fs.existsSync(file)) continue;
    const rec = manifest[asset];
    if (!rec) continue;                       // the next test owns that case
    const now = sha(file);
    if (now !== rec.sha) {
      stale.push(`  ${asset}: content changed but ?v= is still ${rec.v} — `
        + `bump it to ${rec.v + 1} in every page, then set `
        + `{"v": ${rec.v + 1}, "sha": "${now}"} in test/asset_versions.json`);
    }
  }
  assert.deepEqual(stale, [],
    'these bundles will not reach a browser that has visited before:\n'
    + stale.join('\n'));
});

test('the recorded ?v= is the one the pages actually ship', () => {
  const wrong = [];
  for (const [asset, byVersion] of references()) {
    const rec = manifest[asset];
    if (!rec) continue;
    for (const [v, pages] of byVersion) {
      if (v !== rec.v) {
        wrong.push(`  ${asset}: manifest says v=${rec.v}, ${pages.join(', ')} ship v=${v}`);
      }
    }
  }
  assert.deepEqual(wrong, [], 'manifest and markup disagree:\n' + wrong.join('\n'));
});

test('one bundle is never shipped at two versions', () => {
  // Two pages on different numbers means the browser caches two copies and
  // which one you get depends on where you landed first — the same class of
  // bug, arriving by a different door.
  const split = [];
  for (const [asset, byVersion] of references()) {
    if (byVersion.size > 1) {
      const detail = [...byVersion].map(([v, p]) => `v=${v} (${p.join(', ')})`).join('  ·  ');
      split.push(`  ${asset}: ${detail}`);
    }
  }
  assert.deepEqual(split, [], 'same bundle, different versions:\n' + split.join('\n'));
});

// ── the manifest itself stays honest ──────────────────────────────────────

test('every versioned bundle is in the manifest', () => {
  const missing = [];
  for (const [asset] of references()) {
    if (fs.existsSync(path.join(PUB, asset)) && !manifest[asset]) missing.push(asset);
  }
  assert.deepEqual(missing, [],
    `add these to test/asset_versions.json — an unlisted bundle is unratcheted:\n  ${missing.join('\n  ')}`);
});

test('the manifest carries no entry for a bundle nobody references', () => {
  // Same rule as known_failures.txt and unreachable_baseline.txt: an entry
  // that is no longer true is an entry nobody reads.
  const refs = references();
  const dead = Object.keys(manifest).filter(
    (a) => !refs.has(a) || !fs.existsSync(path.join(PUB, a)));
  assert.deepEqual(dead, [],
    `stale manifest entries — delete them in the commit that made them stale:\n  ${dead.join('\n  ')}`);
});

test('the manifest has the shape the ratchet depends on', () => {
  for (const [asset, rec] of Object.entries(manifest)) {
    assert.strictEqual(typeof rec.v, 'number', `${asset}.v`);
    assert.ok(Number.isInteger(rec.v) && rec.v > 0, `${asset}.v must be a positive integer`);
    assert.match(rec.sha, /^[0-9a-f]{16}$/, `${asset}.sha`);
  }
});

// ── the two that actually shipped broken ──────────────────────────────────

test('app.js is past the version that shipped the session-cookie bug', () => {
  // v=7 is the number that was live while LOGGED_IN silently disagreed with
  // itself across pages. Pinned so a bad merge cannot walk it backwards.
  assert.ok(manifest['js/app.js'].v > 7,
    'app.js at v<=7 means returning browsers run the pre-cookie LOGGED_IN');
});

test('dashboard.js is past the version that withheld six honesty fixes', () => {
  assert.ok(manifest['js/dashboard.js'].v > 137);
});
