'use strict';
/**
 * Nav coherence — /rune and /gas exist but the related rooms didn't link
 * them. Now: the Study Room and Command Deck link the Rune (the loop those
 * three form is the landing's whole path band), and the Escape Agent links
 * Live Gas (the page whose floor numbers it embeds). Every link is a
 * translated key, both keys ship all 14 locales.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', 'public', ...p), 'utf8');

test('the rooms link each other', () => {
  for (const page of ['learn.html', 'command.html']) {
    const nav = read(page).match(/<nav class="topnav">[\s\S]*?<\/nav>/)[0];
    assert.match(nav, /href="\/rune" data-i18n="nav\.rune"/, `${page} links the Rune`);
  }
  const esc = read('escape.html').match(/<nav class="topnav">[\s\S]*?<\/nav>/)[0];
  assert.match(esc, /href="\/gas" data-i18n="nav\.gas"/, 'escape links Live Gas');
});

test('both nav keys ship all 14 locales', () => {
  const i18n = read('js', 'i18n.js');
  for (const key of ['nav.rune', 'nav.gas']) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n has ${key}`);
    for (const loc of ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
      'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} has ${loc}`);
    }
  }
  const m = read('learn.html').match(/i18n(?:-core)?\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 85, 'buster >= 85');
});
