'use strict';
/**
 * /rune — the soulbound collection's public page, honest about all three
 * forge states. Pins:
 * - the three states are distinct and truthful: not-deployed is "cold" (not
 *   an error), deployed-but-unreadable is "unknown, not zero" (never
 *   invented), and only a real count draws a number;
 * - the five facts say what the thing is NOT (soulbound, free, one per
 *   wallet, on-chain art, badge-not-investment) — the §4 posture in copy;
 * - non-custodial doctrine rides the how-to (server never signs / sends /
 *   holds keys);
 * - every rn.* key ships all 14 locales; html keys keep markup parity
 *   (checked at insert time; the count key's {n} slot is pinned here);
 * - the page is wired everywhere a page must be wired: route, sitemap,
 *   llms.txt — and the reveal/ambient treatment loads with the classes.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');
const page = read('public', 'rune.html');
const i18n = read('public', 'js', 'i18n.js');

const RN_KEYS = ['rn.h1', 'rn.lede', 'rn.st_h', 'rn.s_loading', 'rn.s_cold',
  'rn.s_unknown', 'rn.s_count', 'rn.s_contract', 'rn.is_h', 'rn.i1', 'rn.i2',
  'rn.i3', 'rn.i4', 'rn.i5', 'rn.how_h', 'rn.how_1', 'rn.how_2', 'rn.how_3',
  'rn.cta_dash'];

test('the three forge states are distinct and honest', () => {
  assert.match(page, /if \(!d\.deployed\)/, 'cold is its own state, not an error');
  assert.match(page, /d\.minted_count == null/, 'unreadable is its own state');
  assert.match(page, /unknown, not zero/, 'the unreadable copy refuses to invent zero');
  assert.match(page, /rn\.s_count/, 'only a real count draws a number');
  // the same honest-unknown state answers a failed fetch too
  const catches = (page.match(/rn\.s_unknown/g) || []).length;
  assert.ok(catches >= 3, 'unknown copy covers bad response AND thrown fetch');
});

test('the five facts carry the §4 posture in copy', () => {
  for (const k of ['rn.i1', 'rn.i2', 'rn.i3', 'rn.i4', 'rn.i5']) {
    assert.ok(page.includes(`data-i18n-html="${k}"`), `fact ${k} is translated`);
  }
  assert.match(page, /A badge, not an investment\./);
  assert.match(page, /promises nothing, earns nothing, and can never be resold/);
  assert.match(page, /never signs transactions, never sends them, never holds keys/,
    'the non-custodial doctrine rides the how-to');
  // §4: no dollar amounts anywhere on this public page
  assert.ok(!/\$\s?\d/.test(page), 'no $-amount on the rune page');
});

test('every rn key ships all 14 locales, with the count slot everywhere', () => {
  for (const key of RN_KEYS) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n has ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
      'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} has ${loc}`);
    }
  }
  const countLine = i18n.split('\n').find((l) => l.includes("'rn.s_count':"));
  assert.ok((countLine.split('{n}').length - 1) >= 14, '{n} slot in every locale');
  const m = page.match(/i18n\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 81, 'i18n cache-buster >= 81');
});

test('the page is wired everywhere a page must be wired', () => {
  const srv = read('server.js');
  assert.match(srv, /app\.get\('\/rune'/, 'the route exists');
  const sitemap = read('public', 'sitemap.xml');
  assert.match(sitemap, /<loc>https:\/\/pmvc58g2\.mule\.page\/rune<\/loc>/);
  const llms = read('public', 'llms.txt');
  assert.match(llms, /\/rune — the soulbound Rune of Entry collection/);
  // the house treatment loads with the classes it needs
  assert.match(page, /reveal-on-scroll/);
  assert.match(page, /\/js\/reveal\.js\?v=/);
  assert.match(page, /rc-constellation/);
  assert.match(page, /\/js\/ambient\.js\?v=/);
  assert.match(page, /id="rc-hall"/, 'the rune-field hero canvas is there');
  assert.match(page, /\/js\/learn-hall\.js\?v=/);
});
