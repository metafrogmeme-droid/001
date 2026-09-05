'use strict';
/**
 * Landing page — the 3D Strength Map showcase band. Brings the flagship
 * market-intelligence visual onto the front door with a clear CTA into the live
 * /strengthmap. Public-market-data framing only (§4).
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

// Reads explore.html: this content moved off the landing page when
// /explore was split out. The assertions below are about the CONTENT
// existing and being correct, not about which document holds it.
const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'explore.html'), 'utf8');
const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');

test('the landing has a Strength Map band linking to the live 3D map', () => {
  const sec = index.slice(index.indexOf('id="strengthTease"'));
  const cut = sec.slice(0, sec.indexOf('</section>') + 10);
  assert.match(cut, /data-i18n="sec\.strength_h"/);
  assert.match(cut, /href="\/strengthmap"/);
  assert.match(cut, /data-i18n="sec\.strength_cta"/);
  // the factor vocabulary is shown as pills
  assert.match(cut, /Momentum/);
  assert.match(cut, /Funding/);
});

test('the Strength Map copy is translated across all six locales + cache-buster bumped', () => {
  for (const key of ['sec.strength_h', 'sec.strength_p', 'sec.strength_cta', 'sec.strength_eyebrow']) {
    assert.match(i18n, new RegExp("'" + key.replace('.', '\\.') + "'"), `has ${key}`);
  }
  const line = i18n.split('\n').find(l => l.includes("'sec.strength_p'"));
  for (const loc of ['en:', 'es:', 'zh:', 'pt:', 'fr:', 'ar:']) assert.ok(line.includes(loc), `strength_p has ${loc}`);
  assert.match(index, /i18n(?:-core)?\.js\?v=\d+/);
});
