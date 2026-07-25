'use strict';
// Action feedback has to speak the user's language too.
//
// The dashboard's static markup and its panel states were swept; its TOASTS
// were not. All 41 static toast() calls were hardcoded English, so a user
// reading in Japanese or Arabic got "Trade confirmed." / "Authority revoked."
// / "Could not save your preference" in English — the product reverted to
// English the moment they DID something, which is the moment feedback matters
// most.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const DASH = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const i18n = require('../public/js/i18n');

test('no toast ships a bare English string literal', () => {
  // toast('...') with a literal and no T() wrapper. Variables and template
  // literals are excluded: those carry server text or interpolated values,
  // which this test cannot localize and must not pretend to.
  const offenders = DASH.split('\n')
    .map((line, i) => ({ line: line.trim(), n: i + 1 }))
    .filter(({ line }) => /toast\('/.test(line))
    .filter(({ line }) => !/toast\(T\(/.test(line));
  assert.deepStrictEqual(offenders.map((o) => `dashboard.js:${o.n}  ${o.line.slice(0, 80)}`), [],
    'these toasts are hardcoded English');
});

test('every toast key resolves in all twelve languages', () => {
  const keys = [...new Set([...DASH.matchAll(/toast\(T\('([\w.]+)'/g)].map((m) => m[1]))];
  assert.ok(keys.length >= 39, `expected the full toast sweep, found ${keys.length}`);
  for (const k of keys) {
    assert.ok(i18n.STRINGS[k], `${k} missing from the dictionary`);
    for (const l of i18n.LANGS) {
      const v = i18n.STRINGS[k][l.code];
      assert.ok(typeof v === 'string' && v.length, `${k} missing ${l.code}`);
    }
  }
});

test('a translated toast never silently drops to blank', () => {
  // The inline English is the fallback, so T() must always be called WITH it.
  const naked = [...DASH.matchAll(/toast\(T\('[\w.]+'\)\)/g)];
  assert.deepStrictEqual(naked.map((m) => m[0]), [],
    'T() needs its English fallback as the second argument');
});

test('T is declared before every use, not merely before its first call', () => {
  // T was defined at line ~2089 while the earliest toast sat at ~813. That only
  // worked because those callers run in deferred callbacks — a const in the TDZ
  // is a runtime error, not a hoisting convenience. Declaring it above every
  // use removes the ordering question entirely.
  const decl = DASH.indexOf('const T = (key, en) =>');
  assert.ok(decl > 0, 'dashboard.js declares T');
  const firstUse = DASH.indexOf("T('");
  assert.ok(decl < firstUse,
    `T is declared at index ${decl} but first used at ${firstUse} — declare it above every use`);
});
