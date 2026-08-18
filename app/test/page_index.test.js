'use strict';
/**
 * The landing page's index — and why the page was not shortened instead.
 *
 * The stated complaint was "a long wall of text, overwhelming, poor overview".
 * The obvious reading is to cut sections, and it is wrong: the page is a
 * CATALOGUE. Fifteen sections, each carrying specific translated copy that
 * does real work — `guardianTease` alone links eight surfaces and describes
 * each one. Deleting entries from a catalogue does not make it navigable, it
 * makes it smaller and worse. What a catalogue lacks is an index.
 *
 * Reordering was tried first and `landing_doors.test.js` refused it: a visitor
 * should meet the claim and its proof before being offered a map. That still
 * holds — this sits BELOW the doors, because the doors say what the site is
 * and this says what the page contains.
 *
 * THE PART THAT IS NOT COSMETIC. Two sections ship `hidden` and appear only
 * once real data loads. An index entry pointing at one of them before it
 * exists is this repo's rule moved into the navigation: a contents line
 * asserts the content is there, and a visitor who clicks it and lands nowhere
 * has been told something false by the site's own map rather than by a number.
 * So entries are hidden until their section appears — omit, never claim.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');
const html = fs.readFileSync(path.join(PUB, 'index.html'), 'utf8');
const appjs = fs.readFileSync(path.join(PUB, 'js', 'app.js'), 'utf8');
const css = fs.readFileSync(path.join(PUB, 'styles.css'), 'utf8');
const i18n = fs.readFileSync(path.join(PUB, 'js', 'i18n.js'), 'utf8');

/** Every `<section id="…">` on the page, in document order. */
const sectionIds = [...html.matchAll(/<section[^>]*\bid="([^"]+)"/g)]
  .map((m) => m[1])
  .filter((id) => id !== 'chatDrawer');           // a drawer, not a page section

/** Every `href="#…"` inside the index nav. */
const indexBlock = html.slice(html.indexOf('<nav class="page-index"'),
  html.indexOf('</nav>', html.indexOf('<nav class="page-index"')));
const indexTargets = [...indexBlock.matchAll(/href="#([^"]+)"/g)].map((m) => m[1]);

// ── it exists, and sits where the door test requires ──────────────────────

test('the index sits below the doors, not above them', () => {
  const at = (s) => html.indexOf(s);
  assert.ok(at('id="doorsTease"') < at('id="pageIndex"'),
    'the doors summarise the SITE and must still come first');
  // marketplaceTease moved to /explore; askTease is the first section the
  // index now points at on this page.
  assert.ok(at('id="pageIndex"') < at('id="askTease"'),
    'an index below the sections it indexes is not an index');
});

// ── both directions: nothing dangles, nothing is missing ──────────────────

test('every index entry points at a section that exists', () => {
  const dangling = indexTargets.filter((id) => !sectionIds.includes(id));
  assert.deepStrictEqual(dangling, [],
    `these index entries point nowhere: ${dangling.join(', ')}`);
});

test('every section on the page is in the index', () => {
  // The other direction, and the one that rots: a section added later without
  // an entry is invisible in the contents, which is the defect this fixes
  // reintroduced one section at a time.
  // Both summaries are skipped for the same stated reason: they sit ABOVE
  // the index and describe the page rather than being part of what it
  // catalogues. #bento is the at-a-glance layer directly under the hero;
  // listing it in a contents block the reader only reaches by scrolling
  // past it points backwards.
  const skip = new Set(['bento', 'doorsTease', 'pageIndex']);
  const missing = sectionIds.filter((id) => !skip.has(id) && !indexTargets.includes(id));
  assert.deepStrictEqual(missing, [],
    `these sections are not in the index: ${missing.join(', ')}`);
});

test('no section is left without an id to link to', () => {
  // Four sections were anonymous; an index cannot address those. If a new one
  // arrives anonymous, the test above cannot even see it — so count instead.
  const total = (html.match(/<section\b/g) || []).length;
  assert.strictEqual(total, sectionIds.length + 1,     // +1 for #chatDrawer
    'a <section> without an id cannot appear in the index and is invisible here');
});

// ── the honesty rule, in the navigation ───────────────────────────────────

test('an entry whose section is hidden does not claim the section is there', () => {
  assert.match(appjs, /function syncPageIndex\(\)/);
  const fn = appjs.slice(appjs.indexOf('function syncPageIndex()'),
    appjs.indexOf('// Trade a pre-cookie localStorage session'));
  const code = fn.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

  assert.match(code, /li\.hidden = target\.hidden/,
    'the entry must follow its section');
  assert.match(code, /if \(!target\) \{ li\.hidden = true; return; \}/,
    'a missing section must hide its entry, not leave a dead anchor');
  assert.ok(!/setInterval|setTimeout/.test(code),
    'polling would show the entry late on exactly the slow connections that '
    + 'need it most; the reveal is observed');
  assert.match(code, /MutationObserver/,
    'the sections are revealed by independent loaders — order cannot be assumed');
});

test('the two sections that ship hidden are the ones handled', () => {
  // If a third starts shipping hidden, the reveal path must cover it too —
  // and this is where somebody finds that out.
  const hidden = [...html.matchAll(/<section[^>]*\bid="([^"]+)"[^>]*\shidden/g)]
    .map((m) => m[1])
    .filter((id) => id !== 'chatDrawer');   // a drawer, excluded from the index too
  assert.deepStrictEqual(hidden.sort(), ['boardTease', 'theaterSection'],
    'the set of hidden sections changed — re-check syncPageIndex covers them');
  for (const id of hidden) assert.ok(indexTargets.includes(id), id);
});

test('syncPageIndex is a no-op on every other page', () => {
  // app.js is a shared bundle. A landing-page feature that throws elsewhere
  // takes the whole bundle down with it, and the symptom appears on a page
  // that has nothing to do with the change.
  const fn = appjs.slice(appjs.indexOf('function syncPageIndex()'));
  assert.match(fn.slice(0, 400), /if \(!nav\) return;/);
});

// ── it is reachable, translated, and adds no new colour ───────────────────

test('every index label is translated in all fourteen locales', () => {
  const keys = [...indexBlock.matchAll(/data-i18n="([^"]+)"/g)].map((m) => m[1]);
  // Eight entries left with the sections they pointed at when /explore was
  // split out; one was added for /explore itself.
  assert.ok(keys.length >= 6, `expected a label per entry, found ${keys.length}`);
  for (const key of ['idx.h', ...keys]) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n defines ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
      'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} is missing ${loc}`);
    }
  }
});

test('the index introduces no colour of its own', () => {
  // The palette decision this round was to leave tokens alone and judge
  // structure first. A hex literal here would quietly start a second palette.
  const block = css.slice(css.indexOf('.page-index {'),
    css.indexOf('Motion & interaction polish'));
  assert.ok(block.length > 200, 'the page-index styles are gone');
  assert.ok(!/#[0-9a-fA-F]{3,8}\b/.test(block),
    'use the existing tokens — no new hex values');
});

test('anchored sections clear the sticky topbar', () => {
  // The topbar is position:sticky. Without scroll-margin every jump lands
  // with the heading tucked underneath it, which reads as a broken link.
  assert.match(css, /scroll-margin-top:\s*calc\(var\(--topbar-h\)/);
});

test('the entries are links, not buttons', () => {
  // Same reasoning as the doors: the page already has thirty-two buttons, and
  // that is why none of them reads as the next step.
  assert.ok(!/<button/.test(indexBlock), 'an index of buttons competes with the CTA');
  assert.match(indexBlock, /<h2[^>]*id="pidx-h"/, 'the nav is labelled for screen readers');
  assert.match(html, /<nav class="page-index" id="pageIndex" aria-labelledby="pidx-h">/);
});
