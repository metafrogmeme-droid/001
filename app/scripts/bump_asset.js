#!/usr/bin/env node
'use strict';
/**
 * Bump a cache-buster across every page and record it in the manifest.
 *
 *   node app/scripts/bump_asset.js                 # bump whatever changed
 *   node app/scripts/bump_asset.js --dry           # show the plan, change nothing
 *   node app/scripts/bump_asset.js styles.css      # bump one, changed or not
 *
 * `test/cache_buster_ratchet.test.js` already computes the exact answer — which
 * file drifted, which number is next, which hash to record. Doing it by hand
 * after reading that message means editing 37 pages and a JSON file without
 * missing one, three times in one afternoon, and the failure mode of missing
 * one is the split-version bug the ratchet's third test exists to catch.
 *
 * THIS SCRIPT DOES NOT DECIDE ANYTHING. It re-derives the same "content hash
 * differs from the recorded hash" comparison and applies the mechanical edit.
 * The ratchet stays the authority: run it after, and it will disagree if this
 * got it wrong. Deliberately NOT wired into the test run — a manifest that
 * updates itself agrees with whatever it finds and asserts nothing, which is
 * the note the ratchet file already carries about itself.
 */

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const APP = path.join(__dirname, '..');
const PUB = path.join(APP, 'public');
const MANIFEST = path.join(APP, 'test', 'asset_versions.json');

const argv = process.argv.slice(2);
const dry = argv.includes('--dry');
const only = argv.filter((a) => !a.startsWith('--'));

const pages = fs.readdirSync(PUB).filter((f) => f.endsWith('.html'));
const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));
const sha = (p) => crypto.createHash('sha256')
  .update(fs.readFileSync(p)).digest('hex').slice(0, 16);

const targets = [];
for (const [asset, rec] of Object.entries(manifest)) {
  const file = path.join(PUB, asset);
  if (!fs.existsSync(file)) {
    console.error(`! ${asset} is in the manifest but not on disk — fix that by hand`);
    process.exitCode = 1;
    continue;
  }
  const now = sha(file);
  const named = only.length > 0 && (only.includes(asset) || only.includes(path.basename(asset)));
  if (now === rec.sha && !named) continue;
  if (only.length > 0 && !named) continue;
  targets.push({ asset, from: rec.v, to: rec.v + 1, sha: now });
}

if (targets.length === 0) {
  console.log('nothing to bump — every manifest hash matches the file on disk');
  process.exit(0);
}

for (const t of targets) {
  let touched = 0;
  const before = new RegExp(`/${t.asset.replace(/[.]/g, '\\.')}\\?v=(\\d+)`, 'g');
  const seen = new Set();
  for (const f of pages) {
    const p = path.join(PUB, f);
    const src = fs.readFileSync(p, 'utf8');
    let hit = false;
    const out = src.replace(before, (m, v) => { seen.add(v); hit = true; return `/${t.asset}?v=${t.to}`; });
    if (!hit) continue;
    touched++;
    if (!dry) fs.writeFileSync(p, out);
  }
  // A file whose pages already disagreed on the version is not a bump, it is a
  // bug — say so rather than quietly unifying it and hiding how long it was
  // split.
  if (seen.size > 1) {
    console.error(`! ${t.asset} was shipped at ${[...seen].join(', ')} across pages `
      + 'before this run — that split was live; check what shipped');
    process.exitCode = 1;
  }
  console.log(`${dry ? '[dry] ' : ''}${t.asset}: v${t.from} -> v${t.to} in ${touched} page(s)`);
  manifest[t.asset] = { sha: t.sha, v: t.to };
}

if (!dry) {
  const sorted = Object.fromEntries(Object.keys(manifest).sort().map((k) => [k, manifest[k]]));
  fs.writeFileSync(MANIFEST, JSON.stringify(sorted, null, 1) + '\n');
  console.log(`manifest updated: ${path.relative(process.cwd(), MANIFEST)}`);
  console.log('now run: node --test app/test/cache_buster_ratchet.test.js');
}
