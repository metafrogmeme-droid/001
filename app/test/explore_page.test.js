'use strict';
/**
 * /explore — and the two ways splitting a page silently loses something.
 *
 * The landing page was fifteen sections, ~7000px, and every one styled as the
 * most important thing on it. Six of those were static pitches for a
 * destination the five doors already summarise; they live here now.
 *
 *  1 · NOTHING MAY BE DELETED IN A MOVE. A migration that drops a section
 *      looks identical to one that moves it, in every diff and every green
 *      suite. Each moved section is asserted present HERE and absent THERE —
 *      absent matters as much, because a section left on both pages is a
 *      duplicate that will drift.
 *
 *  2 · NOTHING MAY BECOME UNREACHABLE. Six routes — /approvals /command /duel
 *      /intent /learn /rune — were linked ONLY from the moved sections. The
 *      reachability guard caught that before the move shipped; they are in the
 *      landing footer now, and this keeps them there.
 *
 * WHY THESE SIX AND NOT EIGHT. `marketplaceTease` and `arenaTease` stayed.
 * Their markup is driven by inline loaders that also drive the hero, the
 * ticker strip and the leaderboard — one 11KB block touching seven ids across
 * what would have been two pages. Moving them split working code in half and
 * turned `getElementById('landingTape').hidden = false` into a TypeError on
 * null; thirteen unrelated receipt tests failed on the way through. Live-data
 * sections stay with their loaders. Static pitches travel.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');
const read = (f) => fs.readFileSync(path.join(PUB, f), 'utf8');
const explore = read('explore.html');
const landing = read('index.html');
const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
const i18n = read(path.join('js', 'i18n.js'));

const MOVED = ['strengthTease', 'guardianTease', 'duelTease', 'pathTease',
  'featuresTease', 'loopTease'];
const STAYED = ['whyTease', 'provableTease', 'doorsTease', 'theaterSection',
  'boardTease', 'marketplaceTease', 'arenaTease'];

// ── 1 · the move lost nothing and duplicated nothing ──────────────────────

test('every moved section is here, and is no longer there', () => {
  for (const id of MOVED) {
    assert.ok(explore.includes(`id="${id}"`), `${id} did not arrive on /explore`);
    assert.ok(!landing.includes(`id="${id}"`),
      `${id} is on BOTH pages — a duplicate that will drift`);
  }
});

test('every section that stayed is still on the landing page', () => {
  for (const id of STAYED) {
    assert.ok(landing.includes(`id="${id}"`), `${id} vanished from the landing page`);
    assert.ok(!explore.includes(`id="${id}"`), `${id} was copied to /explore`);
  }
});

test('the live-data sections stayed with the loaders that drive them', () => {
  // The actual constraint, stated as code: an element and the script that
  // writes to it must be on the same page. This is what the first attempt got
  // wrong, and it fails as a TypeError in a browser and nowhere else.
  for (const id of ['landingTape', 'seasonRibbon', 'todayStrip', 'marketplaceCards']) {
    const markup = landing.includes(`id="${id}"`);
    const script = landing.includes(`getElementById('${id}')`);
    assert.strictEqual(markup, script,
      `${id}: markup and its loader are on different pages`);
    assert.ok(!explore.includes(`id="${id}"`), `${id} leaked onto /explore`);
  }
});

// ── 2 · nothing became unreachable ────────────────────────────────────────

test('the six routes that lived only in the moved sections are still linked', () => {
  for (const r of ['/approvals', '/command', '/duel', '/intent', '/learn', '/rune']) {
    assert.ok(landing.includes(`href="${r}"`),
      `${r} was linked only from a moved section and is now orphaned`);
  }
});

test('/explore is mounted and reachable from the landing page', () => {
  assert.match(server, /app\.get\('\/explore'/, 'the route is not mounted');
  assert.ok(landing.includes('href="/explore"'), 'nothing links to it');
});

// ── the page is a real page, not a fragment ───────────────────────────────

test('it carries the shared shell', () => {
  for (const part of ['<nav class="topbar"', 'site-footer', 'skip-link',
    '/js/i18n-core.js', '/js/app.js', '/js/icons.js']) {
    assert.ok(explore.includes(part), `/explore is missing ${part}`);
  }
  assert.match(explore, /<link rel="canonical" href="[^"]*\/explore"/);
  assert.ok(!explore.includes('href="#auth-panel"'),
    'the nav CTA anchors a panel that only exists on the landing page');
});

test('its bundles ship at the same versions as the landing page', () => {
  // The head was copied from index.html so they cannot drift — this is what
  // says so if somebody bumps one page and forgets the other.
  for (const b of ['i18n-core.js', 'app.js', 'icons.js', 'styles.css']) {
    const re = new RegExp(b.replace('.', '\\.') + '\\?v=(\\d+)');
    const a = explore.match(re); const c = landing.match(re);
    assert.ok(a && c, `${b} is not versioned on both pages`);
    assert.strictEqual(a[1], c[1], `${b}: /explore ships v${a[1]}, landing v${c[1]}`);
  }
});

test('its own strings are translated in all fourteen locales', () => {
  for (const key of ['ex.eyebrow', 'ex.h1', 'ex.lede', 'ex.nav']) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n defines ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
      'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} is missing ${loc}`);
    }
  }
});

test('no dollar figure reaches this public page', () => {
  assert.ok(!/\$\s?\d/.test(explore));
});
