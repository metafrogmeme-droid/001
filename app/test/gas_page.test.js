'use strict';
/**
 * /gas — the live gas table as a public utility page, plus the /rune funnel
 * link from the landing. Pins:
 * - the floor column carries the literal ≥ and the note says exactly what
 *   the floor excludes;
 * - the empty state refuses to invent a table ("unknown, not zero") and the
 *   same copy answers a thrown fetch;
 * - dollar figures here are market facts (native price, floor cost) — §4
 *   permits them; no ACCOUNT amount exists on this page by construction
 *   (the page fetches only /api/gas, pinned);
 * - every gs.* key ships all 14 locales;
 * - wired: route, sitemap, llms.txt, and the landing's rune card now points
 *   at /rune (the public explainer) instead of the app.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');
const page = read('public', 'gas.html');
const i18n = read('public', 'js', 'i18n.js');

const GS_KEYS = ['gs.h1', 'gs.lede', 'gs.t_h', 'gs.t_chain', 'gs.t_gwei',
  'gs.t_native', 'gs.t_floor', 'gs.loading', 'gs.floor_note', 'gs.empty'];

test('the floor is literal and the empty state is honest', () => {
  assert.match(page, /'≥ \$' \+ Number\(c\.xfer_cost_usd\)\.toFixed\(4\)/,
    'the drawn floor carries the literal ≥');
  assert.match(page, /Real transactions add contract overhead, bridge fees, relayers and destination gas/,
    'the note says what the floor excludes');
  assert.match(page, /unknown, not zero/, 'no chains -> unknown, never zero');
  const empties = (page.match(/gs\.empty/g) || []).length;
  assert.ok(empties >= 2, 'the same honest copy answers bad response AND thrown fetch');
  // a missing field draws a dash, never a zero
  assert.match(page, /c\.native_usd != null\) \? .+ : '—'/);
  assert.match(page, /c\.xfer_cost_usd != null\) \? .+ : '—'/);
});

test('the page fetches market facts only', () => {
  const fetches = [...page.matchAll(/fetch\('([^']+)'\)/g)].map((m) => m[1]);
  assert.deepStrictEqual(fetches, ['/api/gas'],
    'only the public gas API — no account surface can leak onto this page');
  assert.match(page, /market facts, not advice/);
  assert.match(page, /never a quote/i);
});

test('every gs key ships all 14 locales', () => {
  for (const key of GS_KEYS) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n has ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
      'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} has ${loc}`);
    }
  }
  const m = page.match(/i18n(?:-core)?\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 82, 'i18n cache-buster >= 82');
});

test('wired: route, sitemap, llms, and the landing funnel', () => {
  assert.match(read('server.js'), /app\.get\('\/gas'/);
  assert.ok(require('../lib/sitemap').STATIC_PATHS.some((x) => x.path === '/gas'),
    '/gas is in the dynamic sitemap');
  assert.match(read('public', 'llms.txt'), /\/gas — live per-chain gas/);
  // the landing's rune card sends visitors to the public explainer first
  // The Rune tile lives on /explore now; the landing links /rune from the footer.
  assert.match(read('public', 'explore.html'), /<a class="feature" href="\/rune"/);
  assert.match(read('public', 'index.html'), /href="\/rune"/);
  // house treatment
  assert.match(page, /reveal-on-scroll/);
  assert.match(page, /\/js\/reveal\.js\?v=/);
  assert.match(page, /rc-constellation/);
  // wide table scrolls in its own container, never the page
  assert.match(page, /gs-scroll \{ overflow-x: auto; \}/);
});
