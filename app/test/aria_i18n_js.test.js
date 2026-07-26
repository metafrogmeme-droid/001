'use strict';
// The half of the a11y sweep the attribute path could not reach.
//
// aria_i18n.test.js localized the 87 aria-labels that live in static markup,
// via data-i18n-attr. It says in its own header that labels built inside
// <script> are out of its scope — and it was right, because apply() rewrites
// attributes on markup that already exists, and these strings are assembled
// into HTML that does not exist yet.
//
// That left 39 labels announced in English no matter what language the page
// was in: every alert control, every chart, the note editor, the API-key
// fields, the trade share/post-mortem buttons, the chat composer. A blind
// reader in Japanese heard "Delete alert" and "Stake per trade" in English,
// inside a page that was otherwise entirely Japanese.
//
// They are localized at build time through T() / TF() instead. This file is
// the guard the attribute sweep could not be.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);

const FILES = ['dashboard.js', 'chat.js', 'chartread.js', 'theater.js', 'strengthmap.js', 'app.js'];
const read = (f) => {
  try { return fs.readFileSync(path.join(__dirname, '..', 'public', 'js', f), 'utf8'); }
  catch (e) { return null; }
};
const ariaKeys = () => Object.keys(i18n.STRINGS).filter((k) => k.startsWith('aria.'));

test('no script-built aria-label is a bare English string', () => {
  const offenders = [];
  for (const f of FILES) {
    const src = read(f);
    if (!src) continue;
    // A literal aria-label="Some Words" with no interpolation of any kind.
    // Both build styles count as translated and must NOT be flagged:
    //   template literal:  aria-label="${T('aria.x', 'X')}"
    //   concatenation:     aria-label="' + T('aria.x', 'X') + '"
    // so the class excludes ${} as well as the quote/plus/newline that only a
    // concatenation can contain.
    for (const m of src.matchAll(/aria-label="([^"${}'+\n]*[A-Za-z]{2}[^"${}'+\n]*)"/g)) {
      offenders.push(`${f}: aria-label="${m[1]}"`);
    }
  }
  assert.deepEqual(offenders, [],
    'these are announced in English regardless of the reader\'s language:\n  '
      + offenders.join('\n  '));
});

test('every aria.* key exists in all 14 languages', () => {
  const keys = ariaKeys();
  assert.ok(keys.length >= 39, `expected the full JS aria sweep, found ${keys.length}`);
  const missing = [];
  for (const k of keys) {
    for (const c of codes) if (!i18n.STRINGS[k][c]) missing.push(`${k}:${c}`);
  }
  assert.deepEqual(missing, [], 'missing aria translations: ' + missing.join(', '));
});

test('every aria.* key referenced in JS actually resolves', () => {
  const used = new Set();
  for (const f of FILES) {
    const src = read(f);
    if (!src) continue;
    for (const m of src.matchAll(/\bTF?\('(aria\.[a-z_0-9]+)'/g)) used.add(m[1]);
  }
  assert.ok(used.size >= 30, `expected the aria keys to be wired, found ${used.size}`);
  for (const k of used) {
    assert.ok(i18n.STRINGS[k], `${k} is used in JS but missing from the dictionary`);
  }
});

test('a label can never be blank — English is the floor, not a possibility', () => {
  for (const k of ariaKeys()) {
    for (const c of codes) {
      assert.ok(String(i18n.STRINGS[k][c] || '').trim().length > 0,
        `${k}:${c} is blank — an unnamed control is worse than an English one`);
    }
  }
});

test('templated labels keep the SAME slots in every language', () => {
  // Drop {sym} and the label silently loses the thing it was naming
  // ("Share  trade"). Rename it and a screen reader reads a literal "{sym}"
  // aloud. Both are worse than leaving the label in English.
  const bad = [];
  for (const k of ariaKeys()) {
    const e = i18n.STRINGS[k];
    const want = (e.en.match(/\{\w+\}/g) || []).sort().join(',');
    for (const c of codes) {
      const got = (String(e[c]).match(/\{\w+\}/g) || []).sort().join(',');
      if (got !== want) bad.push(`${k}:${c} has [${got}] but en has [${want}]`);
    }
  }
  assert.deepEqual(bad, [], bad.join(' | '));
});

test('the slot is movable, which is the whole reason it is a slot', () => {
  assert.equal(i18n.fill('Share {sym} trade', { sym: 'BTC' }), 'Share BTC trade');
  // Turkish puts the value first and the verb last. A label concatenated
  // around a value could only ever have produced English word order.
  assert.equal(i18n.fill(i18n.STRINGS['aria.remove_x'].tr, { x: 'ETH' }),
    'ETH sembolünü kaldır');
  assert.equal(i18n.fill(i18n.STRINGS['aria.share_trade_x'].ja, { sym: 'SOL' }),
    'SOL のトレードを共有');
});

test('an unknown slot stays visible rather than silently vanishing', () => {
  // A blanked slot looks fine in review and quietly drops the value it named.
  // Leaving {sym} visible makes a broken key obvious instead.
  assert.equal(i18n.fill('Share {sym} trade', {}), 'Share {sym} trade');
  assert.equal(i18n.fill('Share {sym} trade', null), 'Share {sym} trade');
  assert.equal(i18n.fill('no slots here', { sym: 'X' }), 'no slots here');
});

test('TF() fills slots itself when i18n.js never loaded', () => {
  // A label that throws takes the whole render down with it, so TF() must not
  // depend on RCI18N being present — same contract T() already had.
  for (const f of ['dashboard.js', 'theater.js']) {
    const src = read(f);
    assert.match(src, /const TF = \(key, en, map\) =>/, `${f} defines TF()`);
    assert.match(src, /\.replace\(\/\\\{\(\\w\+\)\\\}\/g/,
      `${f}'s TF() must fill slots locally when RCI18N is absent`);
  }
});

test('the files that build labels can all resolve a translation', () => {
  // chartread.js and theater.js had no T() at all — a key referenced there
  // without one would be a ReferenceError at render, not a missing translation.
  for (const f of ['dashboard.js', 'chat.js', 'chartread.js', 'theater.js']) {
    const src = read(f);
    assert.match(src, /RCI18N\.translate\(key, /,
      `${f} builds aria-labels but has no way to translate a key`);
  }
});
