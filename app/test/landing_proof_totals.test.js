'use strict';
/**
 * The root strip's window totals — the front door states, in counts anyone
 * can re-derive from /api/roots: how many sealed calls across how many
 * published daily roots, and (only when true) how many days are anchored.
 * Pins:
 * - the totals are computed from the SAME feed rows the strip already
 *   shows — one fetch, re-derivable numbers, no second source;
 * - the anchored clause renders only when anchored > 0 (zero would be
 *   honest, but "not yet anchored" is already covered by mirroring — the
 *   line simply doesn't appear);
 * - real-or-nothing survives: the strip (totals included) stays hidden
 *   until a completed day actually has a root;
 * - both keys ship all 14 locales with their slots.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', 'public', ...p), 'utf8');
const index = read('index.html');
const i18n = read('js', 'i18n.js');

test('totals come from the same feed rows, inside the same real-or-nothing guard', () => {
  const loader = index.slice(index.indexOf("fetch('/api/roots'"));
  const block = loader.slice(0, loader.indexOf('})();'));
  assert.match(block, /seals \+= Number\(d\.roots\[ri\]\.seal_count\) \|\| 0;/);
  assert.match(block, /if \(d\.roots\[ri\]\.anchor_tx\) anchored\+\+;/);
  // the guard that hides everything without a real root comes FIRST
  const guard = block.indexOf('if (!row || !row.root) return;');
  const totals = block.indexOf('rootStripTotals');
  assert.ok(guard > 0 && totals > guard, 'no root -> no strip, totals included');
  // anchored clause is conditional on a real anchor
  assert.match(block, /if \(anchored > 0\) \{/);
});

test('the totals element lives inside the strip, hidden with it', () => {
  const strip = index.slice(index.indexOf('id="rootStrip"'), index.indexOf('</section>', index.indexOf('id="rootStrip"')));
  assert.match(strip, /id="rootStripTotals"/);
});

test('both keys ship all 14 locales with their slots', () => {
  for (const [key, slots] of [['sec.prov_totals', ['{s}', '{d}']],
    ['sec.prov_totals_anch', ['{a}']]]) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n has ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
      'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} has ${loc}`);
    }
    for (const s of slots) {
      assert.ok((line.split(s).length - 1) >= 14, `${key}: ${s} in every locale`);
    }
  }
  const m = index.match(/i18n(?:-core)?\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 84, 'buster >= 84');
});

test('§4: counts only — no amount can enter the totals line', () => {
  const loader = index.slice(index.indexOf("fetch('/api/roots'"));
  const block = loader.slice(0, loader.indexOf('})();'));
  assert.ok(!/\$|usd|balance|pnl/i.test(block), 'the loader touches counts alone');
});
