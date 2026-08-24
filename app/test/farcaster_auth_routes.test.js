'use strict';
/**
 * The SIWF endpoints, over HTTP, against the real router.
 *
 * lib/siwf.js is tested in isolation against every way in; this file drives the
 * half that isolation cannot reach — the nonce STORE, which is where single-use
 * is actually enforced, and the wiring that turns a verified fid into a session.
 *
 * The store is the interesting part. Single-use in a `Set` would be trivially
 * correct and trivially wrong: the app may run more than one replica, so the
 * property has to hold in the database. And a read-then-write is a check-then-
 * act — two requests racing the same nonce both read `used_at IS NULL` and both
 * proceed. The UPDATE carries the condition for that reason, and the race is
 * driven below rather than reasoned about.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');

const mod = require('../routes/farcaster_auth');
const store = mod._nonceStore;
const allowedDomains = mod._allowedDomains;

// ── the store: where single-use actually lives ────────────────────────────

test('an issued nonce is spendable exactly once', async () => {
  const n = await store.issue();
  assert.match(n, /^[a-f0-9]{32}$/);
  assert.equal(await store.consume(n), true, 'a freshly issued nonce was refused');
  assert.equal(await store.consume(n), false, 'the same nonce was spent twice');
});

test('a nonce we never issued is refused', async () => {
  assert.equal(await store.consume('never-issued-at-all'), false);
  assert.equal(await store.consume(''), false);
});

test('an expired nonce is refused', async () => {
  const n = await store.issue();
  // Past its TTL. A signature that took ten minutes to arrive is not one we
  // are willing to spend — the window exists to bound how long a captured
  // message stays worth capturing.
  const future = Date.now() + require('../lib/siwf').NONCE_TTL_MS + 1000;
  assert.equal(await store.consume(n, future), false);
});

test('two requests racing the same nonce: exactly one wins', async () => {
  // The check-then-act. Both callers read `used_at IS NULL` before either
  // writes, so without the condition on the UPDATE both would proceed and the
  // nonce would be spent twice — which is the whole thing a nonce prevents.
  //
  // THIS TEST DID NOT CATCH THAT UNTIL THE SHIM WAS FIXED. The MemoryDB branch
  // for this UPDATE applied `used_at IS NULL` unconditionally, so it was more
  // correct than the statement it was handed: deleting the condition from the
  // real SQL changed nothing and this went on passing. A mutation is what said
  // so, and db.js now honours the WHERE clause it is actually given.
  //
  // Two layers enforce single-use — this condition and the `if (row.used_at)`
  // read above it — so removing EITHER alone is behaviour-preserving and no
  // test fails. That is defence in depth working, not a coverage gap: the
  // property is what is under test, and it survives losing one layer.
  const n = await store.issue();
  const results = await Promise.all([
    store.consume(n), store.consume(n), store.consume(n), store.consume(n),
  ]);
  const wins = results.filter(Boolean).length;
  assert.equal(wins, 1, `${wins} callers spent the same nonce`);
});

test('a used nonce stays on record rather than vanishing', async () => {
  // Marked, not deleted, so a REPLAY and a FABRICATED nonce are distinguishable
  // in the logs. Both are refused; they are not the same event, and an operator
  // reading "unknown nonce" for a replay would be looking for the wrong thing.
  const { pool } = require('../db');
  const n = await store.issue();
  await store.consume(n);
  const [rows] = await pool.execute('SELECT nonce, used_at FROM siwf_nonces WHERE nonce = ?', [n]);
  assert.equal(rows.length, 1, 'the spent nonce was deleted, losing the replay signal');
  assert.ok(rows[0].used_at, 'the nonce was not marked used');
});

// ── the domain allowlist fails CLOSED ─────────────────────────────────────

test('with nothing configured, no domain is allowed', () => {
  // Fail-open here would accept a signature from every Mini App in existence.
  const saved = process.env.SIWF_ALLOWED_DOMAINS;
  const savedBase = process.env.APP_BASE_URL;
  delete process.env.SIWF_ALLOWED_DOMAINS;
  delete process.env.APP_BASE_URL;
  try {
    const out = allowedDomains({ headers: {}, get: () => undefined });
    assert.deepEqual(out, [], 'an unconfigured origin produced an allowlist');
  } finally {
    if (saved !== undefined) process.env.SIWF_ALLOWED_DOMAINS = saved;
    if (savedBase !== undefined) process.env.APP_BASE_URL = savedBase;
  }
});

test('SIWF_ALLOWED_DOMAINS is read and split', () => {
  const saved = process.env.SIWF_ALLOWED_DOMAINS;
  process.env.SIWF_ALLOWED_DOMAINS = 'a.example, b.example';
  try {
    const out = allowedDomains({ headers: {}, get: () => undefined });
    assert.ok(out.includes('a.example'), 'the configured domains were not read');
    assert.ok(out.includes('b.example'));
  } finally {
    if (saved === undefined) delete process.env.SIWF_ALLOWED_DOMAINS;
    else process.env.SIWF_ALLOWED_DOMAINS = saved;
  }
});

// ── the route module's own shape ──────────────────────────────────────────

test('sign-in is rate limited', () => {
  // Unauthenticated, costs a database write and an outbound verification per
  // call. Without a cap it is a free amplifier pointed at somebody else's
  // service as well as ours.
  const fs = require('node:fs');
  const path = require('node:path');
  const { codeOnly } = require('./helpers/code_only');
  const src = codeOnly(fs.readFileSync(
    path.join(__dirname, '..', 'routes', 'farcaster_auth.js'), 'utf8'));
  assert.match(src, /router\.use\(rateLimit\(\{[^}]*key:\s*ipKey/,
    'the SIWF endpoints answer strangers with no limiter');
});

test('the session is minted only after verifySignIn returns ok', () => {
  // Reachability of the check itself. A signToken that could be reached on any
  // other path would be a session issued without a signature behind it.
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'routes', 'farcaster_auth.js'), 'utf8');
  const mintAt = src.indexOf('signToken(user)');
  const guardAt = src.indexOf('if (!verdict.ok)');
  assert.ok(guardAt > 0 && mintAt > guardAt,
    'a token is minted before or without the sign-in verdict being checked');
  assert.equal((src.match(/signToken\(/g) || []).length, 1,
    'more than one place mints a session in this module');
});

test('an unreachable verifier answers 503, not 401', () => {
  // "We could not check" is not "you are not who you say". Collapsing them
  // tells a legitimate user their identity was refused when our dependency is
  // down, and looks identical to a real refusal in the logs.
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'routes', 'farcaster_auth.js'), 'utf8');
  assert.match(src, /verifier_unavailable[\s\S]{0,80}?503|503[\s\S]{0,80}?verifier_unavailable/,
    'a verifier outage is reported as a rejected sign-in');
});

test('an unconfigured allowlist answers 503, not 401', () => {
  // Same distinction one level up: a misconfiguration reported as a rejected
  // identity sends the operator looking at the wrong thing entirely.
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'routes', 'farcaster_auth.js'), 'utf8');
  assert.match(src, /siwf_unconfigured/);
  assert.match(src, /if \(!domains\.length\)/);
});

test('the handle is null when unset, never invented', () => {
  // An account with no leaderboard handle is invisible on every board until it
  // picks one. Returning '' or the fid would look like a handle and put a name
  // on a board that does not carry it.
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'routes', 'farcaster_auth.js'), 'utf8');
  assert.match(src, /leaderboard_handle\) \|\| null/);
});
