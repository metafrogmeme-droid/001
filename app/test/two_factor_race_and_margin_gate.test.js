'use strict';
/**
 * A spent backup code came back, and raising the margin cap asked for nothing.
 *
 * BACKUP CODES. Login's second factor fell through to backup codes with a
 * plain read-modify-write and no transaction:
 *
 *     backups   = JSON.parse(user.totp_backup_codes)     // read
 *     remaining = totp.consumeBackupCode(code, backups)  // copy, minus one
 *     UPDATE users SET totp_backup_codes = ?             // write
 *
 * `consumeBackupCode` returns a COPY of the list. Two logins racing on one
 * account therefore do not merely both succeed — the second write restores the
 * first one's code:
 *
 *     both read [c1, c2, c3]
 *     A spends c1 -> writes [c2, c3]
 *     B spends c2 -> writes [c1, c3]      <- c1 is valid again
 *
 * A spent code returning to the list is worse than the double-use it starts as,
 * and `/2fa/enable` hands the user these codes with the sentence "each works a
 * single time if you lose your authenticator".
 *
 * MARGIN CAP. `controls.js` carried this comment above its step-up gate:
 *
 *     Disabling live, pausing, and lowering margin stay frictionless so
 *     de-risking is never gated
 *
 * "lowering" is doing a lot of work in a sentence sitting over code that never
 * distinguished lowering from raising. Every margin change went through
 * ungated, including the one that increases how much money the bot may put at
 * risk. The comment described a policy; the code implemented half of it.
 */

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { codeOnly } = require('./helpers/code_only');
const totp = require('../lib/totp');

const AUTH = path.join(__dirname, '..', 'auth.js');
const CONTROLS = path.join(__dirname, '..', 'routes', 'controls.js');

// ── the property, exercised ───────────────────────────────────────────────

test('consumeBackupCode never mutates the list it is given', () => {
  // Not a behaviour change — a demonstration that the racing shape is real.
  // If this ever mutates in place the CAS is still correct, but this file's
  // reasoning would need rewriting.
  const { hashes } = totp.generateBackupCodes();
  const before = hashes.slice();
  const missed = totp.consumeBackupCode('not-a-code', hashes);
  assert.strictEqual(missed, null, 'an unknown code consumes nothing');
  assert.deepStrictEqual(hashes, before, 'the input list is untouched');
});

test('two consumers of the same list each drop only their own code', () => {
  // The race in miniature, without a database: this is exactly what the two
  // request handlers computed before either of them wrote.
  const { codes, hashes } = totp.generateBackupCodes();
  const a = totp.consumeBackupCode(codes[0], hashes);
  const b = totp.consumeBackupCode(codes[1], hashes);
  assert.ok(a.includes(totp.hashBackupCode(codes[1])));
  assert.ok(b.includes(totp.hashBackupCode(codes[0])),
    "B's result still contains the code A just spent — writing that back is "
    + 'what resurrects a used code');
});

// ── the fix, at the call site ─────────────────────────────────────────────

/**
 * The backup-code branch, bounded by its own ROUTE rather than by a character
 * count.
 *
 * The first version sliced `src.slice(i, i + 1800)` and failed on correct
 * code — the explanatory comment beside the fix pushed `res.status(409)` past
 * the window. That is the same fragility this session had already fixed in
 * `test_anomaly_flood_is_not_a_market_event.py`, reintroduced here while
 * writing the test that documents it. A span measured in characters is a
 * distance check wearing a wiring check's clothes: it fails when correct code
 * is inserted, which teaches the next person to widen the number, and the
 * widening after that hides a real break.
 */
function backupWindow() {
  const src = codeOnly(fs.readFileSync(AUTH, 'utf8'));
  const i = src.indexOf('consumeBackupCode');
  assert.ok(i > 0, 'the backup-code branch is gone');
  const rest = src.slice(i);
  const end = rest.indexOf('\nrouter.');       // next route handler
  return end === -1 ? rest : rest.slice(0, end);
}

test('the backup-code write is conditional on the bytes that were read', () => {
  // A wiring check, deliberately: the defect is a property of the SQL and no
  // unit test of the pure helper can see it.
  assert.match(backupWindow(),
    /UPDATE users SET totp_backup_codes = \? WHERE id = \? AND totp_backup_codes <=> \?/,
    'the write is unconditional — a concurrent login can put a spent code back');
  assert.match(backupWindow(), /affectedRows !== 1/,
    'the CAS result is unchecked, so a lost update passes silently');
});

test('a lost update refuses the login rather than serving it', () => {
  assert.match(backupWindow(), /res\.status\(409\)/,
    'a race must fail closed: a concurrent redemption is a double-submit or '
    + 'an attack, and neither earns a session');
});

test('<=> is used, not =, so a NULL backup list still compares', () => {
  // `x = NULL` is NULL in SQL and never true, so `=` would fail the CAS for
  // every user who has no backup codes stored and 409 their logins. The
  // null-safe operator is load-bearing, not stylistic.
  assert.match(backupWindow(), /totp_backup_codes <=> \?/);
});

// ── the margin gate ───────────────────────────────────────────────────────

/** Same treatment as backupWindow: bounded by the route, not by a count. */
function marginWindow() {
  const src = codeOnly(fs.readFileSync(CONTROLS, 'utf8'));
  const i = src.indexOf('max_margin must be a non-negative number');
  assert.ok(i > 0, 'the margin validation is gone');
  const rest = src.slice(i);
  const end = rest.indexOf('\nrouter.');
  return end === -1 ? rest : rest.slice(0, end);
}

test('raising the margin cap now asks for a code', () => {
  assert.match(marginWindow(), /stepUpBlock\(/,
    'a margin raise reaches no step-up gate');
  assert.match(marginWindow(), /margin > known|known === null/,
    'the gate does not distinguish a raise from a lower, so it either gates '
    + 'de-risking or gates nothing');
});

test('lowering, clearing and pausing stay frictionless', () => {
  // De-risking must never need a second factor. The /stop path is never gated
  // and this must not become the exception that teaches people to keep an
  // authenticator to hand before they can reduce exposure.
  assert.match(marginWindow(), /margin !== null && margin > 0/,
    'clearing the cap (0, or omitting it) must not require a code');
});

test('the comment and the code now say the same thing', () => {
  // THE ACTUAL DEFECT was the gap between them: the comment promised that
  // *lowering* was the frictionless case — implying raising was not — and
  // nothing implemented the distinction.
  const raw = fs.readFileSync(CONTROLS, 'utf8');
  assert.match(raw, /RAISING the cap is a money-unlock/,
    'the reasoning belongs where it is enforced');
  assert.match(codeOnly(raw), /controls_margin_raise_2fa/,
    'a security-relevant refusal should be logged like its siblings');
});
