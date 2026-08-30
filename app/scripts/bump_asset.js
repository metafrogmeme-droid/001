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

/**
 * Every place a versioned asset is referenced — html on disk AND the route
 * modules that assemble pages and send them.
 *
 * This read only `public/*.html`, which is the blind spot
 * `test/cache_buster_ratchet.test.js` already fixed in its own scan and
 * documented at length: `/embed/signals` and `/miniapp` are pages like any
 * other, they just live in `routes/embed.js` and `routes/miniapp.js` rather
 * than on disk. So for a bundle referenced only from a route module, this
 * script updated the manifest, edited NOTHING, and printed "in 0 page(s)" as
 * an ordinary note — leaving the manifest claiming v2 while every served page
 * still asked for v1. That is the split-version bug this script exists to
 * prevent, manufactured by the script itself.
 *
 * Caught by running it against embed-arena-view.js, whose only two call sites
 * are route modules.
 */
function referencingSources() {
  const out = [];
  for (const f of fs.readdirSync(PUB).filter((x) => x.endsWith('.html'))) {
    out.push(path.join(PUB, f));
  }
  const ROUTES = path.join(APP, 'routes');
  for (const f of fs.readdirSync(ROUTES).filter((x) => x.endsWith('.js'))) {
    out.push(path.join(ROUTES, f));
  }
  return out;
}
const pages = referencingSources();
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
  for (const p of pages) {
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
  // ZERO references is a failure, not a note. Recording v2 in the manifest
  // while every page still requests v1 IS the split this script prevents, and
  // printing "in 0 page(s)" alongside "manifest updated" reads like success.
  // The manifest is left alone so the ratchet still reports the real drift.
  if (touched === 0) {
    console.error(`! ${t.asset}: content changed but NOTHING references it with a ?v= — `
      + 'manifest left unchanged. Either the reference scan cannot see its call '
      + 'site, or the bundle is referenced without a version at all.');
    process.exitCode = 1;
    continue;
  }
  console.log(`${dry ? '[dry] ' : ''}${t.asset}: v${t.from} -> v${t.to} in ${touched} page(s)`);
  manifest[t.asset] = { sha: t.sha, v: t.to };
}

if (!dry) {
  // Written to match the file already on disk: two-space indent, `v` before
  // `sha`. It used to emit one-space indent with the keys the other way round,
  // which reformatted all 220 entries on every run — so bumping TWO assets
  // produced a 440-line diff.
  //
  // That is not cosmetic. A ratchet file's diff is how a reviewer sees which
  // hashes moved, and a blanket refresh of every sha (which is how this
  // ratchet gets defeated — an asset updated without its `?v=`) looks
  // identical to reformatting noise when the whole file rewrites anyway.
  // Small diffs are what make the dangerous change visible.
  const sorted = Object.fromEntries(Object.keys(manifest).sort()
    .map((k) => [k, { v: manifest[k].v, sha: manifest[k].sha }]));
  fs.writeFileSync(MANIFEST, JSON.stringify(sorted, null, 2) + '\n');
  console.log(`manifest updated: ${path.relative(process.cwd(), MANIFEST)}`);
  console.log('now run: node --test app/test/cache_buster_ratchet.test.js');
}
