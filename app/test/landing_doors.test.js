'use strict';
/**
 * The five doors — and the promise that they were added, not traded for.
 *
 * The landing page carries fourteen content sections, each arguing well for a
 * different product, and thirty-two buttons across twenty-five destinations.
 * The result is that a first-time visitor has no single obvious next step: the
 * page is a feature inventory where it needs to be a path.
 *
 * The doors are the cheap half of the fix. They group the whole site by the
 * job it does, high enough on the page to actually be taken, and they REMOVE
 * NOTHING — every destination is still linked from its own section below. That
 * matters because it makes the change reversible and testable: if five doors
 * do not change behaviour, deleting one section costs nothing to undo.
 *
 * So the load-bearing assertion in this file is not "the doors exist". It is
 * "the doors exist AND everything that was there before is still there" —
 * because the moment that stops being true, this is no longer an additive
 * experiment and the reasoning that justified shipping it without the full
 * restructure no longer holds.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');
const html = fs.readFileSync(path.join(PUB, 'index.html'), 'utf8');
const i18n = fs.readFileSync(path.join(PUB, 'js', 'i18n.js'), 'utf8');

const section = html.slice(html.indexOf('id="doorsTease"'),
  html.indexOf('aria-labelledby="ask-h"'));

const DOORS = [
  { key: 'doors.d1_h', href: '/proof', en: 'Evidence' },
  { key: 'doors.d2_h', href: '/arena', en: 'Play' },
  { key: 'doors.d3_h', href: '/dashboard', en: 'Trade' },
  { key: 'doors.d4_h', href: '/guardian', en: 'Safety' },
  { key: 'doors.d5_h', href: '/letter', en: 'Story' },
];

// ── the doors are there, and they go somewhere real ───────────────────────

test('all five doors ship, each linking a page that exists', () => {
  assert.ok(section.length > 200, 'the doors section is present');
  for (const d of DOORS) {
    assert.match(section, new RegExp(`href="${d.href}"`), `${d.en} links ${d.href}`);
    assert.match(section, new RegExp(`data-i18n="${d.key}"`), `${d.en} is translatable`);
    const file = d.href.replace(/^\//, '') + '.html';
    assert.ok(fs.existsSync(path.join(PUB, file)), `${file} exists to be linked`);
  }
});

test('every door string ships all fourteen locales on one line', () => {
  // Several tests in this suite parse i18n.js as TEXT and require the whole
  // locale set on a single physical line, so a prettily-wrapped entry fails.
  const keys = ['doors.h', 'doors.sub'];
  for (let i = 1; i <= 5; i++) keys.push(`doors.d${i}_h`, `doors.d${i}_p`);
  for (const key of keys) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n defines ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
      'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} is missing ${loc}`);
    }
  }
});

// ── ADDITIVE: nothing was traded away to make room ────────────────────────

test('every section that existed before the doors still exists somewhere', () => {
  // The doors shipped additively; /explore later moved eight of these onto
  // their own page. The claim worth keeping is that NONE of them was deleted
  // to make room — so each is asserted against the page it now lives on,
  // rather than dropped from the list, which is how a migration quietly
  // becomes a deletion.
  const explore = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'explore.html'), 'utf8');
  // Live-data sections stayed: their loaders also drive the hero, the ticker
  // and the board, and an 11KB inline block cannot be split across two pages
  // without cutting working code in half. Only the static pitches moved.
  const onLanding = ['whyTease', 'provableTease', 'theaterSection', 'boardTease',
    'marketplaceTease', 'arenaTease'];
  const onExplore = ['strengthTease', 'guardianTease', 'duelTease', 'pathTease'];
  for (const id of onLanding) {
    assert.match(html, new RegExp(`id="${id}"`), `${id} must survive on the landing page`);
  }
  for (const id of onExplore) {
    assert.match(explore, new RegExp(`id="${id}"`), `${id} must survive on /explore`);
    assert.ok(!html.includes(`id="${id}"`), `${id} is on BOTH pages — the move duplicated it`);
  }
});

test('the doors sit above the sections they summarise, and below the proof', () => {
  // Order is the finding: a visitor should meet the claim and its proof before
  // being offered a map, and the map before fourteen individual pitches.
  const at = (s) => html.indexOf(s);
  assert.ok(at('id="provableTease"') < at('id="doorsTease"'),
    'the proof of the headline claim comes first');
  // The individual pitches now live on /explore, which the doors link to.
  assert.ok(at('id="doorsTease"') < at('id="pageIndex"'),
    'the map comes before the page contents');
  assert.match(html, /href="\/explore"/, 'the landing must offer the way to them');
});

test('the doors add no new button — that is the point', () => {
  // The page's problem is thirty-two buttons and no obvious next step. A
  // section whose fix for that is a thirty-third button has misread it.
  //
  // Tokenised, not substring-matched: the first version of this test asserted
  // !/class="btn/ and passed when a door was given class="feature btn
  // btn--primary", because the banned token was no longer first in the
  // attribute. A guard that only catches the tidy spelling of a mistake is
  // the mistake it is guarding against, one level up.
  const classes = [...section.matchAll(/class="([^"]*)"/g)]
    .flatMap((m) => m[1].split(/\s+/));
  const buttons = classes.filter((c) => c === 'btn' || c.startsWith('btn--'));
  assert.deepEqual(buttons, [],
    `the doors are plain links; found button classes: ${buttons.join(', ')}`);
  assert.ok(!/<button/.test(section), 'and no <button> element either');
});

test('the doors do not fetch anything', () => {
  // Purely static markup: no loader means no failure state to render
  // honestly, and no new inline script means the CSP hash set is untouched.
  assert.ok(!/fetch\(|XMLHttpRequest|<script/.test(section));
});

// ── they must not weaken the discoverability floors ───────────────────────

test('the pinned surfaces still appear at least twice on the page', () => {
  // discoverability.test.js owns this rule; asserted here too because the
  // doors are the change most likely to tempt someone into thinning the nav.
  for (const href of ['/dashboard', '/track', '/proof', '/leaderboard', '/letter']) {
    const hits = html.split(`href="${href}"`).length - 1;
    assert.ok(hits >= 2, `${href} appears ${hits}x, floor is 2`);
  }
});

test('no dollar figure reaches this public surface', () => {
  // Public pages carry percent, ratio and count only.
  assert.ok(!/\$\s?\d/.test(section), 'no dollar amounts on a public surface');
});
