#!/usr/bin/env node
/*
 * Re-record app/test/js_honesty_baseline.json — deliberately.
 *
 * Separate from the test on purpose, and the reason is the one
 * cache_buster_ratchet.test.js gives about its own manifest: a baseline that
 * regenerates itself at test time agrees with whatever it finds and asserts
 * nothing. Updating it is a decision, taken in the same commit as the change
 * that moved it, exactly as `known_failures.txt` and `ruff_baseline.json` are.
 *
 *     node app/test/update_js_honesty_baseline.js          # rewrite
 *     node app/test/update_js_honesty_baseline.js --list   # print every hit
 */
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const H = require('./helpers/honesty_shapes');

const BASELINE = path.join(__dirname, 'js_honesty_baseline.json');
const hits = H.scanAll();

if (process.argv.includes('--list')) {
  for (const shape of H.SHAPES) {
    const rows = hits.filter((h) => h.shape === shape);
    if (!rows.length) continue;
    console.log(`\n── ${shape} (${rows.length}) ${'─'.repeat(40)}`);
    for (const h of rows) {
      console.log(`  ${h.file}:${h.line}  [${h.name}]  ${h.text}`);
    }
  }
  console.log(`\n${hits.length} hit(s). A hit is a place to LOOK, not a defect.`);
  process.exit(0);
}

const counts = H.countsFrom(hits);
const doc = {
  _comment:
    'Recorded shapes of "unreadable is never zero" in the JS surfaces. A hit is '
    + 'a place to LOOK, not a defect — see app/test/js_honesty_ratchet.test.js. '
    + 'Two-way ratchet: growth fails, and an improvement must be re-recorded in '
    + 'the same commit. Regenerate with node app/test/update_js_honesty_baseline.js',
  rules_fingerprint: H.rulesFingerprint(),
  roots: H.ROOTS,
  total: hits.length,
  counts,
};
fs.writeFileSync(BASELINE, `${JSON.stringify(doc, null, 2)}\n`);
console.log(`recorded ${hits.length} hit(s) under rule set ${doc.rules_fingerprint}`);
for (const s of H.SHAPES) {
  const n = Object.values(counts[s]).reduce((a, b) => a + b, 0);
  console.log(`  ${String(n).padStart(4)}  ${s}`);
}
