/**
 * RC-2026-025 — the 2FA step-up and the money move must address one subject.
 *
 * Every money path here has the same shape: the step-up reads
 * `totp_enabled`/`totp_secret` for `req.user.user_id`, and the action is then
 * performed as a `telegram_id`. Those are the same subject only while nothing
 * can put ANOTHER account's telegram_id on your row — a property three route
 * files depended on and none of them stated.
 *
 * LATENT, NOT LIVE, and that is why it is worth a test rather than a panic.
 * RC-2026-001 closed the unauthenticated bind, the bot-secret route refuses an
 * id already held by another row, and `idx_users_telegram_id` makes the
 * collision impossible at the storage layer. There is no path today. The next
 * route that writes telegram_id, or a migration repairing rows by hand,
 * re-opens a 2FA bypass on a money path with nothing to catch it — so the
 * invariant is asserted where it is relied upon.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');

// ── the helper, driven directly ──────────────────────────────────────────
//
// `foreignIdentityBlock` takes the pool from lib/db, so the rows it sees are
// stubbed by replacing that module's `execute` for the duration of a check.
const { pool } = require(path.join(ROOT, 'db'));
const { foreignIdentityBlock } = require(path.join(ROOT, 'lib', 'identity'));

async function withRows(rows, fn) {
  const real = pool.execute;
  pool.execute = async () => [rows];
  try { return await fn(); } finally { pool.execute = real; }
}

test('the caller who owns the telegram_id is allowed', async () => {
  const blk = await withRows([{ id: 42 }], () => foreignIdentityBlock('555', 42));
  assert.strictEqual(blk, null);
});

test('a telegram_id owned by ANOTHER account is refused', async () => {
  // The planted state: caller is user 42, but 555 belongs to user 99.
  const blk = await withRows([{ id: 99 }], () => foreignIdentityBlock('555', 42));
  assert.ok(blk, 'the action would have been performed as another account');
  assert.strictEqual(blk.status, 403);
  assert.strictEqual(blk.body.error, 'identity_mismatch');
});

test('a telegram_id owned by NOBODY is refused', async () => {
  // An id on your row that no user row claims is not evidence you own it.
  const blk = await withRows([], () => foreignIdentityBlock('555', 42));
  assert.ok(blk);
  assert.strictEqual(blk.status, 403);
});

test('id types are compared as strings, not by ===', async () => {
  // MySQL returns BIGINT columns as strings in some driver configurations;
  // a strict === between 42 and "42" would refuse every legitimate request.
  const blk = await withRows([{ id: '42' }], () => foreignIdentityBlock('555', 42));
  assert.strictEqual(blk, null, 'a string id from the driver locked the owner out');
});

test('a web-only identity needs no lookup — it IS the caller', async () => {
  // resolveBotIdentity built `web:<uid>` from this very uid, so there is no
  // second subject. It must also not hit the database.
  const real = pool.execute;
  let queried = false;
  pool.execute = async () => { queried = true; return [[]]; };
  try {
    assert.strictEqual(await foreignIdentityBlock('web:42', 42), null);
    assert.strictEqual(queried, false, 'a web identity caused a needless query');
  } finally { pool.execute = real; }
});

test('an absent identity is not treated as a mismatch', async () => {
  assert.strictEqual(await foreignIdentityBlock('', 42), null);
  assert.strictEqual(await foreignIdentityBlock(null, 42), null);
});

// ── the wiring: a guard that exists is not a guard that is reached ────────

const GUARDED = [
  ['routes/staking.js', 'staking /fixed — locks funds'],
  ['routes/webtrade.js', 'webtrade confirm — places a live order'],
  ['routes/controls.js', 'controls submit — unlocks live trading / raises margin'],
];

for (const [rel, what] of GUARDED) {
  test(`${rel} consults the guard (${what})`, () => {
    const src = fs.readFileSync(path.join(ROOT, rel), 'utf8');
    assert.match(src, /foreignIdentityBlock\(/,
      `${rel} performs a money move as a telegram_id without asserting that `
      + 'the account presenting 2FA owns it');
    assert.match(src, /require\(['"]\.\.\/lib\/identity['"]\)/,
      `${rel} does not import the shared helper`);
  });
}

test('every step-up route is in the guarded list', () => {
  // The list above is the claim; this is the check that it is complete. A new
  // route that gates on stepUpBlock and then acts as a telegram_id has the
  // same defect and must be added here.
  const dir = path.join(ROOT, 'routes');
  const offenders = [];
  for (const f of fs.readdirSync(dir).filter((n) => n.endsWith('.js'))) {
    const src = fs.readFileSync(path.join(dir, f), 'utf8');
    if (!/stepUpBlock\(/.test(src)) continue;
    if (!/telegram_id/.test(src)) continue;
    if (!/foreignIdentityBlock\(/.test(src)) offenders.push(f);
  }
  assert.deepStrictEqual(offenders, [],
    `these routes gate on a 2FA step-up and then act as a telegram_id without `
    + `checking the two agree: ${offenders.join(', ')}`);
});

test('/stop is deliberately NOT gated, and says so', () => {
  // De-risking is never gated in this file — a 403 on the emergency stop would
  // block a user closing their own book to close a hole the unique index
  // already closes. Recorded so the omission reads as a decision.
  const src = fs.readFileSync(path.join(ROOT, 'routes', 'controls.js'), 'utf8');
  assert.match(src, /NOT applied to \/stop/,
    'the reason /stop is ungated is no longer stated, so it reads as an oversight');
});
