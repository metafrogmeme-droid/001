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

/** Every `/js/<file>.js?v=<n>` reference in the shipped pages. */
function references() {
  const found = new Map();
  for (const f of fs.readdirSync(PUB).filter((x) => x.endsWith('.html'))) {
    const src = fs.readFileSync(path.join(PUB, f), 'utf8');
    for (const m of src.matchAll(/\/js\/([A-Za-z0-9_.-]+\.js)\?v=(\d+)/g)) {
      if (!found.has(m[1])) found.set(m[1], new Map());
      const pages = found.get(m[1]);
      const v = Number(m[2]);
      if (!pages.has(v)) pages.set(v, []);
      pages.get(v).push(f);
    }
  }
  return found;
}

const sha = (p) => crypto.createHash('sha256')
  .update(fs.readFileSync(p)).digest('hex').slice(0, 16);

// ── the ratchet itself ────────────────────────────────────────────────────

test('a changed bundle carries a changed ?v=', () => {
  const stale = [];
  for (const [js] of references()) {
    const file = path.join(PUB, 'js', js);
    if (!fs.existsSync(file)) continue;
    const rec = manifest[js];
    if (!rec) continue;                       // the next test owns that case
    const now = sha(file);
    if (now !== rec.sha) {
      stale.push(`  ${js}: content changed but ?v= is still ${rec.v} — `
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
  for (const [js, byVersion] of references()) {
    const rec = manifest[js];
    if (!rec) continue;
    for (const [v, pages] of byVersion) {
      if (v !== rec.v) {
        wrong.push(`  ${js}: manifest says v=${rec.v}, ${pages.join(', ')} ship v=${v}`);
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
  for (const [js, byVersion] of references()) {
    if (byVersion.size > 1) {
      const detail = [...byVersion].map(([v, p]) => `v=${v} (${p.join(', ')})`).join('  ·  ');
      split.push(`  ${js}: ${detail}`);
    }
  }
  assert.deepEqual(split, [], 'same bundle, different versions:\n' + split.join('\n'));
});

// ── the manifest itself stays honest ──────────────────────────────────────

test('every versioned bundle is in the manifest', () => {
  const missing = [];
  for (const [js] of references()) {
    if (fs.existsSync(path.join(PUB, 'js', js)) && !manifest[js]) missing.push(js);
  }
  assert.deepEqual(missing, [],
    `add these to test/asset_versions.json — an unlisted bundle is unratcheted:\n  ${missing.join('\n  ')}`);
});

test('the manifest carries no entry for a bundle nobody references', () => {
  // Same rule as known_failures.txt and unreachable_baseline.txt: an entry
  // that is no longer true is an entry nobody reads.
  const refs = references();
  const dead = Object.keys(manifest).filter(
    (js) => !refs.has(js) || !fs.existsSync(path.join(PUB, 'js', js)));
  assert.deepEqual(dead, [],
    `stale manifest entries — delete them in the commit that made them stale:\n  ${dead.join('\n  ')}`);
});

test('the manifest has the shape the ratchet depends on', () => {
  for (const [js, rec] of Object.entries(manifest)) {
    assert.strictEqual(typeof rec.v, 'number', `${js}.v`);
    assert.ok(Number.isInteger(rec.v) && rec.v > 0, `${js}.v must be a positive integer`);
    assert.match(rec.sha, /^[0-9a-f]{16}$/, `${js}.sha`);
  }
});

// ── the two that actually shipped broken ──────────────────────────────────

test('app.js is past the version that shipped the session-cookie bug', () => {
  // v=7 is the number that was live while LOGGED_IN silently disagreed with
  // itself across pages. Pinned so a bad merge cannot walk it backwards.
  assert.ok(manifest['app.js'].v > 7,
    'app.js at v<=7 means returning browsers run the pre-cookie LOGGED_IN');
});

test('dashboard.js is past the version that withheld six honesty fixes', () => {
  assert.ok(manifest['dashboard.js'].v > 137);
});
