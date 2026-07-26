'use strict';
// A signed Solana address and a pasted one are not the same claim.
//
// The server had this right all along: POST /wallet/solana ed25519-verifies a
// signature when one is supplied, and answers { verified: true|false }. Then it
// threw that answer away — only the address was stored. So on the next page
// load a wallet whose owner had proved possession and a string someone typed
// were indistinguishable, and the panel called both of them "Solana watch
// address".
//
// That understates one and overstates the other. Pasting an address proves only
// that someone can type; the panel must not let it read as proof of ownership.

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const i18n = require('../public/js/i18n.js');
const codes = i18n.LANGS.map((l) => l.code);
const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');
const dash = read('public', 'js', 'dashboard.js');
const auth = read('auth.js');
const walletRoute = read('routes', 'wallet.js');
const db = read('db.js');

test('the verification result is persisted, not discarded', () => {
  assert.match(db, /ADD COLUMN sol_verified TINYINT\(1\) NOT NULL DEFAULT 0/,
    'there is nowhere to record whether the address was proven');
  assert.match(auth, /SET sol_address = \?, sol_verified = \?/,
    'the ed25519 result is computed and then thrown away');
});

test('unlinking clears the claim, so a later paste cannot inherit it', () => {
  // Without this, unlink → paste a different address would show the NEW,
  // unproven address wearing the OLD address's verified badge.
  assert.match(auth, /SET sol_address = \?, sol_verified = 0 WHERE id = \?/,
    'unlink leaves sol_verified set');
});

test('the API tells the client which claim it is', () => {
  assert.match(walletRoute, /sol_verified: solVerified/,
    'the client cannot distinguish proven from pasted');
  assert.match(walletRoute, /!!Number\(u\.sol_verified \|\| 0\)/,
    'a NULL/absent column must read as UNVERIFIED, never as verified');
});

test('the panel labels the two states differently', () => {
  assert.match(dash, /d\.sol_verified/, 'the panel ignores the distinction');
  assert.match(dash, /dd\.s_verified/);
  assert.match(dash, /dd\.s_watch_only/);
  // The old copy called every linked address a "watch address".
  assert.doesNotMatch(dash, /◎ Solana watch address/,
    'the undifferentiated "watch address" label is back');
});

test('an unverified address says plainly that it proves nothing', () => {
  // Shown only in the unverified case — a proven address does not need it.
  assert.match(dash, /d\.sol_verified \? '' : /,
    'the caveat must not be shown for an address that WAS proven');
  const e = i18n.STRINGS['dd.s_watch_note'];
  assert.match(e.en, /not proof of ownership/i);
});

test('the not-proof warning survives translation in every language', () => {
  // Matched by each language's own phrasing: a length check would call the
  // CJK translations truncated purely because they are denser than Latin.
  const e = i18n.STRINGS['dd.s_watch_note'];
  const proof = {
    en: /not proof of ownership/i, hi: /प्रमाण नहीं/, it: /non prova la proprietà/i,
    es: /no prueba la propiedad/i, zh: /不能證明所有權/, pt: /não prova a propriedade/i,
    fr: /pas une preuve de propriété/i, de: /kein Eigentumsnachweis/i,
    nl: /geen eigendomsbewijs/i, ja: /所有権の証明にはなりません/,
    ko: /소유권 증명이 아닙니다/, ru: /не доказательство владения/i,
    tr: /sahiplik kanıtı değildir/i, ar: /ليس إثباتًا للملكية/,
  };
  for (const c of codes) {
    assert.match(String(e[c]), proof[c],
      `dd.s_watch_note:${c} dropped the "this is not proof of ownership" warning`);
  }
});

test('every Solana string exists in all 14 languages', () => {
  for (const k of ['dd.s_verified', 'dd.s_watch_only', 'dd.s_watch_note',
    'dd.s_mirror', 'dd.s_stop']) {
    const e = i18n.STRINGS[k];
    assert.ok(e, `${k} missing from the dictionary`);
    for (const c of codes) assert.ok(String(e[c] || '').trim().length, `${k} is missing ${c}`);
  }
});

test('the read-only promise is unchanged by any of this', () => {
  // §4: the Solana side has never had a signing surface beyond the login
  // message, and verifying ownership must not read as granting anything.
  assert.match(i18n.STRINGS['dd.s_mirror'].en, /read-only/i);
  assert.match(auth, /Either way the address never touches a signing surface here/);
});

test('the in-memory shim honours BOTH shapes of the sol_address update', () => {
  // Caught by the suite, not by review: adding sol_verified changed the SQL
  // from `SET sol_address = ?` to `SET sol_address = ?, sol_verified = ?`, and
  // the shim matched on the statement prefix while reading params[1] as the id.
  // It silently wrote nothing. The same class of divergence — a shim that
  // accepts what MySQL would not, or ignores what MySQL would honour — has
  // already cost this repo real bugs, so pin both shapes.
  assert.match(db, /const withVerified = params\.length === 3;/,
    'the shim assumes a single parameter shape again');
  assert.match(db, /params\[withVerified \? 2 : 1\]/,
    'the id is read from a fixed position, so one shape targets the wrong row');
});

test('a fresh row reads as unverified, never as verified', () => {
  // Every user that existed before this column has no claim to a proven
  // address. The default and the coercion both have to say so.
  assert.match(db, /sol_verified TINYINT\(1\) NOT NULL DEFAULT 0/);
  assert.match(walletRoute, /!!Number\(u\.sol_verified \|\| 0\)/);
});
