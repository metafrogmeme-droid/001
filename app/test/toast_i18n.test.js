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
const CHAT = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'chat.js'), 'utf8');
// Both files raise toasts; both must speak the user's language.
const SURFACES = [{ name: 'dashboard.js', src: DASH }, { name: 'chat.js', src: CHAT }];
const i18n = require('../public/js/i18n');

test('no toast ships a bare English string literal', () => {
  // toast('...') with a literal and no T() wrapper. Variables and template
  // literals are excluded: those carry server text or interpolated values,
  // which this test cannot localize and must not pretend to.
  const offenders = SURFACES.flatMap(({ name, src }) => src.split('\n')
    .map((line, i) => ({ line: line.trim(), n: i + 1, name }))
    .filter(({ line }) => /toast\('/.test(line))
    .filter(({ line }) => !/toast\(T\(/.test(line)));
  assert.deepStrictEqual(offenders.map((o) => `${o.name}:${o.n}  ${o.line.slice(0, 80)}`), [],
    'these toasts are hardcoded English');
});

test('every toast key resolves in all twelve languages', () => {
  const keys = [...new Set(SURFACES.flatMap(({ src }) =>
    [...src.matchAll(/toast\(T\('([\w.]+)'/g)].map((m) => m[1])))];
  assert.ok(keys.length >= 45, `expected the full toast sweep, found ${keys.length}`);
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

test('chat.js localizes its visible controls too, not just its toasts', () => {
  // The chat drawer builds its own buttons and labels in JS, so they never
  // passed through the static data-i18n sweep.
  for (const k of ['dd.ct_trade_this', 'dd.ct_cancel', 'dd.ct_read_chart',
    'dd.ct_attach_aria', 'dd.ct_setting_up']) {
    assert.match(CHAT, new RegExp(`T\\('${k.replace('.', '\\.')}'`), `${k} is wired in chat.js`);
    assert.ok(i18n.STRINGS[k], `${k} is in the dictionary`);
    for (const l of i18n.LANGS) assert.ok(i18n.STRINGS[k][l.code], `${k} missing ${l.code}`);
  }
});

test('T is declared before every use, not merely before its first call', () => {
  // T was defined at line ~2089 while the earliest toast sat at ~813. That only
  // worked because those callers run in deferred callbacks — a const in the TDZ
  // is a runtime error, not a hoisting convenience. Declaring it above every
  // use removes the ordering question entirely.
  for (const { name, src } of SURFACES) {
    const decl = src.indexOf('const T = (key, en) =>');
    assert.ok(decl > 0, `${name} declares T`);
    const firstUse = src.indexOf("T('");
    assert.ok(decl < firstUse,
      `${name}: T declared at ${decl} but first used at ${firstUse} — declare it above every use`);
  }
});
