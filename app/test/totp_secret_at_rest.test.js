'use strict';
/**
 * The 2FA seed was plaintext base32 in the users table.
 *
 * A database leak handed over every enrolled secret, and a TOTP seed is worse
 * to lose than most things in that table. A password hash costs an attacker
 * time; a session expires. A seed is a permanent second factor that generates
 * valid codes forever, and the user cannot tell it has been copied.
 *
 * `app/lib/creds_crypto.js` — AES-256-GCM, shared with the Python bot — has
 * existed for exchange keys the whole time. 2FA never used it.
 *
 * WHY THE FIX IS AT `verifyTotp` AND NOT AT SEVEN CALL SITES. The secret is
 * read in seven places across five files (auth.js, controls.js, staking.js,
 * webtrade.js) and every one of them hands it to `verifyTotp` or to
 * `stepUpBlock`, which calls `verifyTotp`. Opening at that boundary is what
 * makes the change complete; opening at each caller is what makes the eighth
 * caller a plaintext read nobody notices. Same reason `_fmt_price(None)`
 * returns an em dash at the boundary rather than a dozen call sites each
 * remembering to check.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const totp = require('../lib/totp');

const KEY_A = Buffer.alloc(32, 7).toString('base64');
const KEY_B = Buffer.alloc(32, 9).toString('base64');

function withKey(key, fn) {
  const before = process.env.WEB_CREDS_KEY;
  if (key === null) delete process.env.WEB_CREDS_KEY;
  else process.env.WEB_CREDS_KEY = key;
  try { return fn(); } finally {
    if (before === undefined) delete process.env.WEB_CREDS_KEY;
    else process.env.WEB_CREDS_KEY = before;
  }
}

// ── the seed no longer lands in the clear ─────────────────────────────────

test('a sealed secret does not contain the secret', () => {
  withKey(KEY_A, () => {
    const s = totp.generateSecret();
    const sealed = totp.sealSecret(s);
    assert.notStrictEqual(sealed, s);
    assert.ok(!sealed.includes(s),
      'the base32 seed appears verbatim inside the stored value — a leak of the '
      + 'users table still hands over every enrolled second factor');
    assert.strictEqual(totp.openSecret(sealed), s, 'it must still round-trip');
  });
});

test('a code verifies against the sealed value exactly as against the plain one', () => {
  withKey(KEY_A, () => {
    const s = totp.generateSecret();
    const now = 1_700_000_000_000;
    const code = totp.hotp(s, Math.floor(now / 30_000));
    assert.strictEqual(totp.verifyTotp(s, code, now), true, 'plain still works');
    assert.strictEqual(totp.verifyTotp(totp.sealSecret(s), code, now), true,
      'a sealed secret no longer verifies — every 2FA user is locked out');
  });
});

// ── the migration, which is the half that breaks accounts ─────────────────

test('a legacy plaintext row still verifies', () => {
  // THE ROWS THAT ALREADY EXIST. Nobody re-enrols on deploy day, so a change
  // that only understands the new shape logs out every enrolled account —
  // strictly worse than the defect it fixes.
  withKey(KEY_A, () => {
    const s = totp.generateSecret();
    const now = 1_700_000_000_000;
    const code = totp.hotp(s, Math.floor(now / 30_000));
    assert.strictEqual(totp.verifyTotp(s, code, now), true,
      'a pre-migration plaintext secret stopped working');
    assert.strictEqual(totp.openSecret(s), s);
  });
});

test('with no key configured, nothing breaks and nothing pretends', () => {
  // Refusing to run without WEB_CREDS_KEY would lock every 2FA user out of an
  // app that worked a minute ago. So it degrades — and reports that it did.
  withKey(null, () => {
    const s = totp.generateSecret();
    assert.strictEqual(totp.sealSecret(s), s, 'nothing to encrypt with');
    assert.strictEqual(totp.secretsAreSealed(), false,
      'an unconfigured deployment must not claim encryption at rest');
    const now = 1_700_000_000_000;
    assert.strictEqual(totp.verifyTotp(s, totp.hotp(s, Math.floor(now / 30_000)), now), true);
  });
  withKey(KEY_A, () => {
    assert.strictEqual(totp.secretsAreSealed(), true);
  });
});

// ── failing closed, which is where this could have gone quietly wrong ─────

test('a wrong or rotated key refuses rather than trying the ciphertext', () => {
  // NULL, NOT THE ENVELOPE. Falling through to "compare the code against the
  // ciphertext" produces a plain authentication failure, which sends an
  // operator hunting for a user error while the real cause is a key. A second
  // factor that cannot be read must refuse, and must be diagnosable.
  const sealed = withKey(KEY_A, () => totp.sealSecret(totp.generateSecret()));
  withKey(KEY_B, () => {
    assert.strictEqual(totp.openSecret(sealed), null);
    assert.strictEqual(totp.verifyTotp(sealed, '000000'), false);
  });
});

test('a truncated or corrupt envelope is null, not a lucky match', () => {
  const sealed = withKey(KEY_A, () => totp.sealSecret(totp.generateSecret()));
  withKey(KEY_A, () => {
    // THE SECOND CASE USED TO BE `replace(/"ct":"."/, ...)`, WHICH MATCHED
    // NOTHING — ct is many characters, so the "corrupt" envelope was the intact
    // one and the test failed on correct code. A mutation that does not reach
    // the thing it mutates proves nothing, which is the second time today.
    const flipped = sealed.replace(/"ct":"(.)/, (_, c) => `"ct":"${c === 'A' ? 'B' : 'A'}`);
    assert.notStrictEqual(flipped, sealed, 'the corruption did not take');
    for (const bad of [sealed.slice(0, -5), flipped, '{"ct":}']) {
      assert.strictEqual(totp.openSecret(bad), null, `corrupt envelope opened: ${bad.slice(0, 40)}`);
    }
  });
});

test('an empty secret is null, and an unenrolled account is not gated', () => {
  assert.strictEqual(totp.openSecret(''), null);
  assert.strictEqual(totp.openSecret(null), null);
  assert.strictEqual(totp.verifyTotp(null, '123456'), false);
});

// ── the wiring: every reader must go through the boundary ─────────────────

test('no route reads a secret without going through verifyTotp or stepUpBlock', () => {
  // The property that makes the boundary fix complete. A route that compares a
  // code itself, or hands the column to anything else, is a plaintext
  // assumption that the seven known call sites do not cover.
  const { codeOnly } = require('./helpers/code_only');
  const roots = [path.join(__dirname, '..', 'routes'), path.join(__dirname, '..', 'lib')];
  const files = [path.join(__dirname, '..', 'auth.js')];
  for (const d of roots) {
    for (const f of fs.readdirSync(d)) if (f.endsWith('.js')) files.push(path.join(d, f));
  }
  const offenders = [];
  for (const f of files) {
    const src = codeOnly(fs.readFileSync(f, 'utf8'));
    if (!src.includes('totp_secret')) continue;
    // Each use must be a SELECT of the column, a write, or a hand-off to the
    // two functions that open it.
    for (const m of src.matchAll(/[\w.[\]']*totp_secret[\w.[\]']*/g)) {
      const line = src.slice(src.lastIndexOf('\n', m.index) + 1,
        src.indexOf('\n', m.index) === -1 ? undefined : src.indexOf('\n', m.index));
      // A BARE STRING LITERAL IS A COLUMN NAME, NOT A READ. account_erasure.js
      // lists 'totp_secret' among the columns it clears, which is exactly what
      // it should do and is not a plaintext assumption. The first version of
      // this scan flagged it — not every match is a defect, including in a
      // scan written ten minutes ago.
      if (/^\s*'totp_secret',?\s*$|'totp_secret',/.test(line.trim()) && !/totp_secret\s*=/.test(line)) continue;
      // A PRESENCE TEST IS NOT A READ. `if (!user.totp_secret)` and
      // `!!rows[0].totp_secret` ask whether a secret EXISTS, and a sealed
      // envelope is a non-empty string exactly like a plaintext seed — both
      // are correct before and after this change.
      //
      // That is the third exemption this scan needed, after the column-name
      // literal and the two-line SQL. The pattern is worth naming: a guard
      // that asks "does this identifier appear outside an approved context"
      // over-flags by construction, and every exemption has to be judged
      // rather than added to make the red go away. All three here were correct
      // code; none was a defect.
      if (/[!(]\s*$/.test(src.slice(Math.max(0, m.index - 4), m.index))) continue;
      // A WINDOW, not one line. SQL and its params array are two lines apart,
      // and judging the params line alone reported a correct write as a raw
      // read. Anchored on the statement rather than a character count.
      const from = src.lastIndexOf(';', m.index);
      const to = src.indexOf(';', m.index);
      const stmt = src.slice(from === -1 ? 0 : from, to === -1 ? src.length : to + 1);
      const ok = /SELECT|UPDATE|INSERT|verifyTotp|stepUpBlock|sealSecret|openSecret/.test(stmt);
      if (!ok) offenders.push(`${path.basename(f)}: ${line.trim().slice(0, 90)}`);
    }
  }
  assert.deepStrictEqual(offenders, [],
    'a totp_secret is used somewhere that does not open it — that read assumes '
    + 'plaintext and will silently fail against a sealed row');
});

test('the enrolment response tells the user whether the seed is encrypted', () => {
  const { codeOnly } = require('./helpers/code_only');
  const auth = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'auth.js'), 'utf8'));
  assert.match(auth, /encrypted_at_rest: totp\.secretsAreSealed\(\)/,
    'a user enrolling a permanent second factor is not told whether the seed '
    + 'is encrypted where it lands');
  assert.match(auth, /totp\.sealSecret\(secret\)/,
    'the freshly generated secret is stored without being sealed');
});
