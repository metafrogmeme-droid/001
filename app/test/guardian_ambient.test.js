'use strict';
/**
 * Guardian-suite ambient identity — the five safety pages get the landing's
 * living constellation as a full-viewport backdrop, each tinted with its own
 * hue so every room in the suite has a color of its own. Pins:
 * - ambient.js reads data-hue (finite-guarded, landing-blue default) and
 *   data-viewport (fixed full-viewport sizing, window-level pointer);
 * - every page ships the canvas defensively: behind content (z-index:-1),
 *   click-through (pointer-events:none), invisible to screen readers, quiet
 *   (opacity well under the landing's);
 * - the five hues are all distinct — identity, not wallpaper.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const pub = (...p) => fs.readFileSync(path.join(__dirname, '..', 'public', ...p), 'utf8');
const ambient = pub('js', 'ambient.js');

const PAGES = ['escape.html', 'firewall.html', 'intent.html', 'stress.html', 'sentinel.html', 'flight.html'];

test('ambient.js is parameterized, with safe defaults', () => {
  assert.match(ambient, /parseFloat\(c\.getAttribute\('data-hue'\)\)/);
  assert.match(ambient, /if \(!isFinite\(hue\)\) hue = 202;/, 'garbage hue falls back to landing blue');
  assert.match(ambient, /c\.hasAttribute\('data-viewport'\)/);
  // both link strokes and node fills follow the hue
  assert.match(ambient, /strokeStyle = 'hsla\(' \+ hue/);
  assert.match(ambient, /NODE_FILL = 'hsla\(' \+ hue/);
  // fixed mode sizes to the viewport and listens on window
  assert.match(ambient, /window\.innerWidth/);
  assert.match(ambient, /var pt = fixed \? window : host;/);
});

test('every safety page ships a defensive, tinted backdrop', () => {
  const hues = new Set();
  for (const page of PAGES) {
    const html = pub(page);
    const m = html.match(/<canvas id="rc-constellation" data-viewport data-hue="(\d+)"[^>]*>/);
    assert.ok(m, `${page} has the backdrop canvas`);
    hues.add(m[1]);
    assert.match(m[0], /aria-hidden="true"/, `${page}: hidden from screen readers`);
    assert.match(m[0], /z-index:-1/, `${page}: behind all content`);
    assert.match(m[0], /pointer-events:none/, `${page}: click-through`);
    const op = m[0].match(/opacity:\.(\d+)/);
    assert.ok(op && Number('0.' + op[1]) <= 0.4, `${page}: quiet backdrop, not wallpaper`);
    assert.ok(html.includes('/js/ambient.js?v='), `${page} loads ambient.js`);
  }
  assert.strictEqual(hues.size, PAGES.length, 'six pages, six distinct hues');
});

test('the landing still gets the tuned build', () => {
  const index = pub('index.html');
  const m = index.match(/ambient\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 3, 'landing ambient buster >= 3');
  // the landing canvas takes the default hue — no data-hue attribute
  assert.match(index, /<canvas id="rc-constellation" aria-hidden="true">/);
});

test('the flight ledger reveals its records as they arrive', () => {
  const html = pub('flight.html');
  // records are fetch-built — the class must ride the template AND the
  // MutationObserver-equipped observer must be loaded
  assert.match(html, /class=\"rec reveal-on-scroll\"/);
  assert.ok(html.includes('/js/reveal.js?v='), 'flight loads reveal.js');
});
