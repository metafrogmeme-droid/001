'use strict';
/**
 * The first screenful may not contain a number the code cannot back.
 *
 * The bento is the at-a-glance layer: four cards, above everything else, read
 * by someone deciding in about eight seconds whether this is serious. That
 * makes it the worst possible place for a figure that has quietly stopped
 * being true — and the likeliest, because marketing copy is edited by people
 * who are not reading the trading engine.
 *
 * So the rule for this section is stricter than for the rest of the page:
 *
 *   1. Exactly two numbers are allowed, and both are DERIVED. The paper stake
 *      and the leverage cap come from `public/js/arena_engine.js` — the bytes
 *      the browser sandbox and the server BOTH load, reached here through the
 *      `lib/arena.js` shim — so changing START_BALANCE or MAX_LEVERAGE reddens
 *      this file until the page agrees. The card cannot advertise a rule the
 *      sandbox does not play by, because there is only one copy of the rule.
 *   2. Nothing else numeric. No win rate, no return, no user count, no "800+".
 *      A live metric in the hero has to be fetched, and a fetched number has a
 *      failure mode — which on this surface would render as a confident claim
 *      at the top of the page. Absent is never a measurement, and the cheapest
 *      way to never print an unreadable measurement is to promise none.
 *   3. Every card goes somewhere that exists. The whole point of the block is
 *      surfacing capability that was already built and unreachable; a card
 *      pointing at a 404 would be that failure with a nicer face on it.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');
const index = fs.readFileSync(path.join(PUB, 'index.html'), 'utf8');
const arena = require('../lib/arena');

/** The bento section's markup, and nothing else on the page. */
function bento() {
  const start = index.indexOf('<section class="section section--tight reveal-on-scroll" id="bento"');
  assert.ok(start > 0, 'the #bento section is gone from index.html');
  const end = index.indexOf('</section>', start);
  assert.ok(end > start, '#bento has no closing tag');
  return index.slice(start, end);
}

test('the paper stake on the card is the stake the code gives you', () => {
  const m = bento().match(/data-rc-arena-stake>([\d,]+)</);
  assert.ok(m, 'the stake figure is not marked with data-rc-arena-stake');
  const shown = Number(m[1].replace(/,/g, ''));
  assert.strictEqual(shown, arena.START_BALANCE,
    `the bento advertises ${m[1]} vUSDT and the arena engine starts accounts at `
    + `${arena.START_BALANCE}`);
});

test('the leverage cap on the card is the cap the engine enforces', () => {
  const m = bento().match(/data-rc-arena-lev>(\d+)</);
  assert.ok(m, 'the leverage figure is not marked with data-rc-arena-lev');
  assert.strictEqual(Number(m[1]), arena.MAX_LEVERAGE,
    `the bento advertises ${m[1]}x and the arena engine caps leverage at `
    + `${arena.MAX_LEVERAGE}x — advertising MORE than the engine allows is a `
    + 'promise the trade path will refuse');
});

test('those two are the ONLY numbers in the block', () => {
  // Everything else must be a capability claim, which stays true without a
  // read. Strip the marked figures, then look for any digit left in text.
  let text = bento()
    .replace(/data-rc-arena-stake>[\d,]+</, 'data-rc-arena-stake><')
    .replace(/data-rc-arena-lev>\d+</, 'data-rc-arena-lev><')
    .replace(/<!--[\s\S]*?-->/g, '')          // comments are not rendered
    .replace(/<[^>]+>/g, ' ');                // attributes are not rendered
  const digits = text.match(/\d[\d.,]*\s*%?/g) || [];
  assert.deepStrictEqual(digits, [],
    'the bento renders numbers beyond the two derived ones: '
    + `${digits.join(', ')} — a figure here cannot be kept true without a live `
    + 'read, and a failed read on the first screenful renders as a confident '
    + 'claim');
});

test('every bento card links somewhere that exists', () => {
  const hrefs = [...bento().matchAll(/<a class="bento-card[^"]*" href="([^"]+)"/g)]
    .map((m) => m[1]);
  assert.ok(hrefs.length >= 4, `only ${hrefs.length} bento cards found`);
  const { STATIC_PATHS } = require('../lib/sitemap');
  const known = new Set(STATIC_PATHS.map((s) => s.path));
  for (const h of hrefs) {
    assert.ok(known.has(h),
      `a bento card points at ${h}, which the sitemap does not list — the `
      + 'block exists to surface things that work, so a card into a 404 is '
      + 'the original defect wearing a nicer face');
  }
});

test('the lead card names the capability that was hardest to find', () => {
  // follow-an-agent lives eleven hundred lines into arena.html and was
  // reachable by nobody who did not already know it existed. If this sentence
  // goes, the feature goes back to being invisible.
  const b = bento();
  assert.match(b, /data-i18n="bento\.arena_f3"/,
    'the follow-an-agent line is gone from the lead card');
  const i18n = fs.readFileSync(path.join(PUB, 'js', 'i18n.js'), 'utf8');
  const m = i18n.match(/'bento\.arena_f3':\s*\{\s*en:\s*"([^"]*)"/);
  assert.ok(m, 'bento.arena_f3 has no en string');
  assert.match(m[1], /follow/i, `bento.arena_f3 no longer describes following: "${m[1]}"`);
});

test('the block is above the long scroll, not buried in it', () => {
  // Its entire job is to be seen before the fifteen-section catalogue. Placed
  // after them it is just a sixteenth section.
  const at = (s) => index.indexOf(s);
  assert.ok(at('id="bento"') < at('id="howItWorks"'));
  assert.ok(at('id="bento"') < at('id="doorsTease"'));
  assert.ok(at('id="bento"') < at('id="pageIndex"'));
});

test('the sticky mobile CTA reserves the space it covers', () => {
  // It is position:fixed, so without this the footer's last row of links is
  // permanently unreachable on a phone — measured covering the Guardian card's
  // link at 390px before the rule existed.
  const css = fs.readFileSync(path.join(PUB, 'styles.css'), 'utf8');
  assert.match(css, /body\.past-hero\s*\{[^}]*padding-bottom/,
    'nothing reserves space for the fixed .mobile-cta, so it sits on top of '
    + 'whatever is at the bottom of the page');
  // Only while it is SHOWN — an unconditional gap would appear for visitors
  // who never scroll past the hero.
  assert.ok(!/^\s*body\s*\{[^}]*padding-bottom:\s*calc\(var\(--tabbar-h/m.test(css),
    'the clearance is unconditional; it must be scoped to .past-hero');
});

test('the sweep is measuring something', () => {
  const b = bento();
  assert.ok(b.length > 800, `the bento slice is ${b.length} chars — the parser `
    + 'has stopped finding the section');
  assert.ok(arena.START_BALANCE > 0 && arena.MAX_LEVERAGE > 0,
    'the arena engine exports non-positive constants; every comparison above is vacuous');
  // And the digit scan must still be able to say no.
  assert.ok(/\d/.test('a 7 here'), 'sanity');
});

// ── the catalogue door ───────────────────────────────────────────────────────

test('the tool count matches what /explore actually lists', () => {
  // DERIVED, because this is the number most likely to rot: a tool is added to
  // explore.html and nobody thinks of a sentence on the landing page. It counts
  // links to real sitemap paths, so a typo'd href drops the count rather than
  // inflating it.
  const m = index.match(/data-rc-tool-count>(\d+)</);
  assert.ok(m, 'the tool count is not marked with data-rc-tool-count');
  const ex = fs.readFileSync(path.join(PUB, 'explore.html'), 'utf8');
  const { STATIC_PATHS } = require('../lib/sitemap');
  const known = new Set(STATIC_PATHS.map((s) => s.path));
  const listed = new Set([...ex.matchAll(/href="(\/[a-z0-9-]+)"/g)]
    .map((x) => x[1]).filter((p) => known.has(p)));
  assert.strictEqual(Number(m[1]), listed.size,
    `the landing page says ${m[1]} tools and /explore lists ${listed.size}`);
});

test('the count is markup, not translated copy', () => {
  // A figure inside a translated string is fourteen places to update it and
  // fourteen chances for one to keep the old number.
  const i18n = fs.readFileSync(path.join(PUB, 'js', 'i18n.js'), 'utf8');
  const m = i18n.match(/'doors\.all_note':\s*\{([^}]*)\}/);
  assert.ok(m, 'doors.all_note is missing');
  assert.ok(!/\d/.test(m[1].replace(/[a-z]{2}:/g, '')),
    `a number leaked into the translated copy: ${m[1].slice(0, 120)}`);
});

test('the catalogue has a door outside the footer and the More menu', () => {
  // The whole finding: all 25 pages were linked, but 14 only from the footer's
  // wall of 28. /explore lists every one of them and lived in three places you
  // only look if you already suspect it exists.
  const doors = index.slice(index.indexOf('id="doorsTease"'));
  const block = doors.slice(0, doors.indexOf('<!-- Page index'));
  assert.match(block, /class="doors-all"/,
    'the five-doors block no longer offers a route to the full catalogue');
  assert.match(block, /href="\/explore"/);
});
