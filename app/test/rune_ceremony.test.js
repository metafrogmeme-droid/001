'use strict';
/**
 * Rune ceremony + study-hall field — the contract under test:
 * - The on-chain SVG is NEVER inlined: extractLines() whitelist-parses it
 *   and the card is rebuilt from numeric coordinates only. Hostile payloads
 *   (script tags, event handlers, foreignObject) yield nothing.
 * - Both animations honor prefers-reduced-motion, and the hall pauses when
 *   hidden or off-screen (source-pinned house rules).
 * - The hall's glyph geometry IS the contract's: its branch table is
 *   cross-pinned byte-for-byte against RuneOfEntry.sol's BRANCHES constant.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { extractLines } = require('../public/js/rune-card.js');
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');

const b64 = (s) => 'data:image/svg+xml;base64,' + Buffer.from(s).toString('base64');

test('extractLines: the contract-shaped SVG parses to its exact lines', () => {
  const svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 350 350'>"
    + "<rect width='350' height='350' fill='#0a0b10'/>"
    + "<g stroke='#e6c34b' stroke-width='7' stroke-linecap='round' fill='none'>"
    + "<line x1='175' y1='40' x2='175' y2='310'/>"
    + "<line x1='175' y1='127' x2='107' y2='207'/>"
    + '</g></svg>';
  const lines = extractLines(b64(svg));
  assert.deepEqual(lines, [
    { x1: 175, y1: 40, x2: 175, y2: 310 },
    { x1: 175, y1: 127, x2: 107, y2: 207 },
  ]);
});

test('extractLines: hostile payloads yield nothing that can reach the page', () => {
  const hostile = b64("<svg><script>alert(1)</script>"
    + "<line x1='1' y1='2' x2='3' y2='4' onclick='steal()'/>"
    + "<foreignObject><body onload='x()'></body></foreignObject>"
    + "<line x1='javascript:1' y1='2' x2='3' y2='4'/></svg>");
  assert.deepEqual(extractLines(hostile), [],
    'a line dressed with extra attributes or non-numeric coords is dropped whole');
  assert.deepEqual(extractLines('data:image/svg+xml;utf8,<svg/>'), [], 'base64 data URIs only');
  assert.deepEqual(extractLines('not a uri'), []);
  // Cap: a payload of 100 lines yields at most 32.
  const many = b64('<svg>' + Array.from({ length: 100 }, () => "<line x1='1' y1='2' x2='3' y2='4'/>").join('') + '</svg>');
  assert.equal(extractLines(many).length, 32);
});

test('the card animates honestly: draw-on + tilt, both bowing to reduced motion', () => {
  const src = read('public', 'js', 'rune-card.js');
  assert.match(src, /prefers-reduced-motion/);
  assert.match(src, /strokeDasharray/);
  assert.match(src, /pointermove/);
  assert.match(src, /perspective\(/);
  assert.match(src, /inlined raw/i, 'the safety rule is stated where the code lives');
});

test('cross-pin: the hall’s branch table IS the contract’s BRANCHES, byte for byte', () => {
  const sol = fs.readFileSync(path.join(__dirname, '..', '..', 'contracts', 'rune', 'RuneOfEntry.sol'), 'utf8');
  const hexes = [...sol.matchAll(/hex"([0-9A-Fa-f]{8})"/g)].map((m) => m[1]);
  assert.equal(hexes.length, 12, 'the contract draws from 12 branches');
  const contractTable = hexes.map((h) => [0, 1, 2, 3].map((i) => parseInt(h.slice(i * 2, i * 2 + 2), 16)));
  const hall = read('public', 'js', 'learn-hall.js');
  const arr = hall.match(/var BRANCHES = \[([\s\S]*?)\];/)[1];
  const hallTable = [...arr.matchAll(/\[(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\]/g)]
    .map((m) => [+m[1], +m[2], +m[3], +m[4]]);
  assert.deepEqual(hallTable, contractTable,
    'the ambient glyphs and the on-chain art must stay one family');
});

test('the hall honors the house rules for ambient canvases', () => {
  const src = read('public', 'js', 'learn-hall.js');
  assert.match(src, /prefers-reduced-motion/);
  assert.match(src, /One static frame — no loop, ever/i);
  assert.match(src, /visibilitychange/);
  assert.match(src, /IntersectionObserver/);
  assert.match(src, /devicePixelRatio/);
  assert.match(src, /not anyone.s token/i, 'decorative seeds are said to be decorative');
});

test('the pages wire the ceremony: hall canvas on /learn, rune card on the dashboard', () => {
  const learn = read('public', 'learn.html');
  assert.match(learn, /id="rc-hall"/);
  assert.match(learn, /learn-hall\.js/);
  assert.match(learn, /aria-hidden="true"/, 'the hall is decoration, invisible to readers');
  const dash = read('public', 'js', 'dashboard.js');
  assert.match(dash, /RCRuneCard\.mount\(holder, p\.minted_image/);
  assert.match(dash, /<div id="runeCard"><img /, 'the plain <img> stays as the no-ceremony fallback');
  assert.match(read('public', 'dashboard.html'), /rune-card\.js/);
});
