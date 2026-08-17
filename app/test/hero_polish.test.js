'use strict';
/**
 * WEB3-POLISH surface 3 — the landing hero (public/index.html). Browser-only,
 * so source-asserted: the verified-track-record stat strip now paints an honest
 * number-free shimmer skeleton on first load (instead of a bare headline) and
 * clears it if there's nothing to show; the trust badges gain a staggered
 * entrance + hover lift. §4/honesty: the skeleton carries no numbers.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

test('the hero stat strip paints a shimmer skeleton on first load', () => {
  assert.match(html, /host\.innerHTML = '<span class="hero-stat-skel">/);
  // skeleton is made visible immediately (defeats the [hidden] attribute)
  assert.match(html, /host\.hidden = false; host\.style\.display = 'flex';/);
});

test('the skeleton clears on empty data and on fetch failure (no stranded shimmer)', () => {
  assert.match(html, /function clearSkel\(\) \{ if \(host\) \{ host\.innerHTML = ''; host\.hidden = true;/);
  assert.match(html, /var d = r && r\.ok && r\.data; if \(!d\) return clearSkel\(\);/);
  assert.match(html, /if \(!rows\.length\) return clearSkel\(\);/);
  assert.match(html, /\.catch\(function \(\) \{ clearSkel\(\); \}\);/);
});

test('§4/honesty: the skeleton pills are empty (no numbers stand in for real data)', () => {
  // The skeleton is built only from empty <span class="hero-stat-skel"></span>
  // pills — there is no non-empty variant that could smuggle a fake figure.
  assert.ok(html.includes('<span class="hero-stat-skel"></span>'), 'empty skeleton pill present');
  assert.doesNotMatch(html, /<span class="hero-stat-skel">[^<]/, 'no skeleton pill carries content');
});

test('the hero-stat skeleton is styled and reduced-motion safe', () => {
  assert.match(css, /\.hero-stat-skel \{[\s\S]*animation: shimmer 1\.4s infinite/);
  assert.match(css, /prefers-reduced-motion: reduce\) \{ \.hero-stat-skel \{ animation: none/);
});

test('trust badges gain a staggered entrance and a hover lift', () => {
  assert.match(css, /\.trust-badge \{[\s\S]*animation: rc-badge-in \.5s/);
  assert.match(css, /\.trust-badge:hover \{ color: var\(--text\); transform: translateY\(-1px\)/);
  assert.match(css, /@keyframes rc-badge-in \{ from \{ opacity: 0/);
  // reduced-motion neutralises the entrance
  assert.match(css, /prefers-reduced-motion: reduce\) \{[\s\S]*\.trust-badge \{ animation: none/);
});

test('styles.css cache-buster was bumped so the hero polish ships', () => {
  // A FLOOR, not a range. This was `/styles\.css\?v=1[6-9]/`, which asserts
  // the version is 16-19 — so it passed for exactly four bumps and then failed
  // on the next legitimate one, which is what it did here at v=24. A test that
  // expires teaches people to edit the test to make a build go green, and that
  // habit costs far more than the check is worth.
  const m = html.match(/styles\.css\?v=(\d+)/);
  assert.ok(m, 'index.html no longer versions its stylesheet at all');
  assert.ok(Number(m[1]) >= 16,
    `styles.css is at v=${m[1]}; the hero polish shipped at v=16, so anything `
    + 'below that means a page went back to a sheet without it');
});
