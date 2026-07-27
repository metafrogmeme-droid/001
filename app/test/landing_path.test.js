'use strict';
/**
 * Landing "trader's path" band + ambient parallax. The landing page now sells
 * the practice loop (Study Room → Command Deck → Rune of Entry) with a fully
 * translated section, and the hero constellation leans toward the pointer.
 * Pins:
 * - the band links /learn, /command and /dashboard, every string translated;
 * - all 14 locales ship for every new key;
 * - the parallax is decoration only: eased translate, reset on leave, and the
 *   reduced-motion early return happens BEFORE any pointer listener attaches
 *   (a reduced-motion user gets one static frame and zero motion hooks);
 * - §4: no dollar amounts anywhere in the new public band.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
const ambient = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'ambient.js'), 'utf8');

const PATH_KEYS = ['sec.path_eyebrow', 'sec.path_h', 'sec.path_p',
  'sec.path_f1h', 'sec.path_f1p', 'sec.path_f2h', 'sec.path_f2p',
  'sec.path_f3h', 'sec.path_f3p', 'sec.path_cta'];
const LOCALES = ['en:', 'hi:', 'it:', 'es:', 'zh:', 'pt:', 'fr:', 'de:',
  'nl:', 'ja:', 'ko:', 'ru:', 'tr:', 'ar:'];

function pathBand() {
  const start = index.indexOf('<section class="section reveal-on-scroll" id="pathTease"');
  assert.ok(start > 0, 'the pathTease section exists');
  const sec = index.slice(start);
  return sec.slice(0, sec.indexOf('</section>') + 10);
}

test('the path band links all three stations of the loop', () => {
  const cut = pathBand();
  assert.match(cut, /<a class="feature" href="\/learn"/);
  assert.match(cut, /<a class="feature" href="\/command"/);
  assert.match(cut, /<a class="feature" href="\/rune"/);
  assert.match(cut, /reveal-on-scroll/);
  assert.match(cut, /rc-stagger/);
});

test('every string in the band is translated', () => {
  const cut = pathBand();
  for (const k of PATH_KEYS) {
    assert.ok(cut.includes(`data-i18n="${k}"`), `band carries data-i18n="${k}"`);
  }
});

test('§4: the public band never shows a dollar amount', () => {
  const cut = pathBand();
  assert.ok(!/\$\s?\d/.test(cut), 'no $-amount in the path band');
  // and it is honest about what the rune is NOT
  assert.match(cut, /not an investment/i);
});

test('every path key ships all 14 locales', () => {
  for (const key of PATH_KEYS) {
    const line = i18n.split('\n').find((l) => l.includes(`'${key}':`));
    assert.ok(line, `i18n has ${key}`);
    for (const loc of LOCALES) {
      assert.ok(line.includes(loc), `${key} has ${loc}`);
    }
  }
});

test('cache-busters are bumped for the new copy and the new parallax', () => {
  const m = index.match(/i18n\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 79, 'i18n cache-buster >= 79');
  const a = index.match(/ambient\.js\?v=(\d+)/);
  assert.ok(a && Number(a[1]) >= 2, 'ambient cache-buster >= 2');
});

test('the constellation leans toward the pointer, eased and reversible', () => {
  assert.match(ambient, /pointermove/, 'a pointermove listener exists');
  assert.match(ambient, /pointerleave/, 'the lean resets when the pointer leaves');
  assert.match(ambient, /ctx\.translate\(px, py\)/, 'the whole field translates as one');
  assert.match(ambient, /ctx\.save\(\)/);
  assert.match(ambient, /ctx\.restore\(\)/);
  // eased approach, not a hard snap
  assert.match(ambient, /px \+= \(tx - px\) \* 0\.0?\d+/);
});

test('reduced motion exits before any pointer listener attaches', () => {
  const reduceReturn = ambient.indexOf("if (reduce) { draw(false); return; }");
  const pointerHook = ambient.indexOf("addEventListener('pointermove'");
  assert.ok(reduceReturn > 0, 'the static-frame early return exists');
  assert.ok(pointerHook > 0, 'the pointer hook exists');
  assert.ok(reduceReturn < pointerHook,
    'reduced-motion returns before the parallax listener is registered');
});
