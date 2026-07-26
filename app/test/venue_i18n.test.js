'use strict';
// The exchange-connection panel is the highest-stakes text on the site.
//
// These sentences tell a user which API permissions to grant, that withdrawal
// permission is never needed, and — for the two on-chain venues — to create an
// agent wallet and use ITS private key, never their main wallet key. Shipping
// that in English to someone who does not read English is not a polish
// problem: it is the difference between a scoped trading key and a wallet
// drain.
//
// The copy lives in lib/venues.js (server-side, published via /config), so the
// client cannot translate it by looking at markup. It translates by KEY,
// derived from the venue id and the field key — both already stable
// identifiers the credential route validates against. That means adding a
// venue needs no schema change, and these tests are what stop it shipping
// untranslated.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const i18n = require('../public/js/i18n.js');
const { VENUES } = require('../lib/venues');
const codes = i18n.LANGS.map((l) => l.code);
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

const full = (k) => {
  const e = i18n.STRINGS[k];
  assert.ok(e, `${k} is missing from the dictionary`);
  for (const c of codes) {
    assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
  return e;
};

test('every connectable venue has translated help in all 14 languages', () => {
  assert.ok(VENUES.length >= 8, `expected the full venue catalogue, found ${VENUES.length}`);
  for (const v of VENUES) {
    assert.ok(v.help && v.help.trim(), `${v.id} ships no help text at all`);
    full(`venue.help.${v.id}`);
  }
});

test('every credential field label is translated in all 14 languages', () => {
  const keys = new Set();
  for (const v of VENUES) for (const f of v.fields || []) keys.add(f.key);
  assert.ok(keys.size >= 5, `expected the full field set, found ${keys.size}`);
  for (const k of keys) full(`venue.f.${k}`);
});

test('the venue BRAND name is never translated', () => {
  // "Bitget" is what the user sees on Bitget's own site. Translating it would
  // send them looking for a product that does not exist under that name.
  for (const v of VENUES) {
    assert.ok(!i18n.STRINGS[`venue.label.${v.id}`],
      `${v.id}'s brand name has a translation key — brand names are identifiers`);
  }
  // The client interpolates the label rather than keying it.
  assert.match(dash, /TF\('venue\.connect_x', 'Connect \{venue\}', \{ venue: v\.label \}\)/);
});

test('the safety instruction survives translation in every language', () => {
  // The two on-chain venues say: use the AGENT wallet key, never your main
  // wallet key. A translation that loses the "never your main key" clause is
  // worse than English, because it reads as authoritative guidance.
  for (const id of ['hyperliquid', 'paradex']) {
    const e = full(`venue.help.${id}`);
    for (const c of codes) {
      const v = String(e[c]);
      assert.ok(/never|nunca|jamais|niemals|nooit|絶대|絶対|никогда|asla|مطلقًا|kabhi|कभी|絕不|절대|mai /i.test(v)
        || v.includes('لا تستخدم'),
        `venue.help.${id}:${c} appears to have dropped the "never your main wallet key" warning: ${v}`);
    }
  }
  // And the CEX venues keep the withdrawals-disabled instruction.
  for (const id of ['bitget', 'okx', 'gate', 'kucoin']) {
    const e = full(`venue.help.${id}`);
    assert.match(e.en, /withdrawals disabled/i, `${id} lost its withdrawal warning in English`);
  }
});

test('the client translates help and field labels by key, not by markup', () => {
  assert.match(dash, /T\('venue\.help\.' \+ v\.id, v\.help/,
    'venue help is rendered raw — server-side copy cannot be translated by apply()');
  assert.match(dash, /T\('venue\.f\.' \+ f\.key, f\.label\)/,
    'field labels are rendered raw');
});

test('the connection chrome is translated too', () => {
  for (const k of ['venue.connected', 'venue.not_connected', 'venue.applying',
    'venue.disconnect', 'venue.connect_x', 'venue.encrypt_note', 'venue.unavailable',
    'venue.encrypting', 'venue.queued', 'venue.failed']) full(k);
  // The two states a user reads to know whether their key took effect.
  assert.match(dash, /T\('venue\.connected'/);
  assert.match(dash, /T\('venue\.not_connected'/);
  assert.match(dash, /T\('venue\.queued'/);
});

test('templated venue strings keep their slot in every language', () => {
  for (const k of ['venue.applying', 'venue.connect_x']) {
    const e = full(k);
    for (const c of codes) {
      assert.match(String(e[c]), /\{venue\}/,
        `${k}:${c} dropped {venue} — the message would not say which exchange`);
    }
  }
});

test('the field-key guard covers every venue, including future ones', () => {
  // No synthetic "add a fake venue and watch my own loop throw" test here —
  // that would assert the test, not the code. The real guard is the first two
  // tests: they iterate the live VENUES catalogue, so a venue or field added to
  // lib/venues.js without a dictionary entry fails them by construction.
  // What IS worth pinning is that the client derives its key from the same
  // identifier the guard checks — otherwise the guard could pass while the
  // browser looked up a different key and silently fell back to English.
  const helpKeysChecked = VENUES.map((v) => `venue.help.${v.id}`);
  assert.match(dash, /T\('venue\.help\.' \+ v\.id/,
    'the client must key off v.id, the same identifier the guard iterates');
  for (const k of helpKeysChecked) assert.ok(i18n.STRINGS[k], `${k} missing`);

  const fieldKeys = new Set();
  for (const v of VENUES) for (const f of v.fields || []) fieldKeys.add(f.key);
  assert.match(dash, /T\('venue\.f\.' \+ f\.key/,
    'the client must key off f.key, the same identifier the guard iterates');
  for (const k of fieldKeys) assert.ok(i18n.STRINGS[`venue.f.${k}`], `venue.f.${k} missing`);
});
