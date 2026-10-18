'use strict';
/**
 * The login limits and the OAuth handoff hold across web instances.
 *
 * THE FINDING (2026-10-11). The web host replaces its instance about every 70
 * seconds, and the outgoing and incoming instances overlap (measured: a new
 * instance answering while one 186 seconds old still did). Three things the
 * login path relies on lived in a Map in one process:
 *
 *   - the per-account failure budget (8 per 15 minutes) shared by login and
 *     every second-factor check (lib/second_factor_lockout.js);
 *   - the per-IP login limit (10 per 15 minutes) in auth.js;
 *   - the OAuth state and PKCE verifier between /start and /callback, and the
 *     one-time link key.
 *
 * So each new instance counted from zero, each overlapping instance kept its
 * own count, and a callback landing on another instance than its /start was
 * refused as "invalid or expired". They live in the database now.
 *
 * "Another instance" here is the real thing as far as a process can make it:
 * every per-process module is loaded again (freshInstance), and only the
 * database, which instances do share, is the same object.
 */

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
process.env.APP_BASE_URL = 'https://app.test';
process.env.DISCORD_CLIENT_ID = 'disc-client';
process.env.DISCORD_CLIENT_SECRET = 'disc-secret';

const test = require('node:test');
const assert = require('node:assert');
const { navigationTarget } = require('./helpers/navigation');
const express = require('express');

const db = require('../db');
const oauth2 = require('../lib/oauth2');

// The provider's two network calls, stubbed so the callback runs offline.
oauth2.exchangeCode = async () => 'stub-access-token';
oauth2.fetchProfile = async () => ({ providerId: 'disc-rotation-1', email: 'rotation@example.com', avatarUrl: null });

const PER_PROCESS = ['../auth', '../lib/durable_attempts', '../lib/second_factor_lockout', '../lib/oauth_state_store', '../lib/stepup'];

/** A second web instance: the same database, every per-process module loaded again. */
function freshInstance() {
  for (const m of PER_PROCESS) delete require.cache[require.resolve(m)];
  return {
    auth: require('../auth'),
    lockout: require('../lib/second_factor_lockout'),
    attempts: require('../lib/durable_attempts'),
    oauthStates: require('../lib/oauth_state_store'),
  };
}

const servers = [];
async function serve(instance) {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', instance.auth.router);
  const server = await new Promise((resolve) => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  servers.push(server);
  return `http://127.0.0.1:${server.address().port}`;
}
test.after(() => { for (const s of servers) s.close(); });

// Every counter starts empty; the IP counter in auth.js is not exported, so the shared rows are cleared directly.
test.beforeEach(() => {
  for (const k of Object.keys(db.pool.authAttempts)) delete db.pool.authAttempts[k];
});

const post = (base, route, body) => fetch(base + route, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

// ── the counter, between two instances ────────────────────────────────────

test('an account locked out on one instance is locked out on the next', async () => {
  const a = freshInstance();
  const k = a.lockout.uidKey(901);
  for (let i = 0; i < a.lockout.MAX_FAILURES; i++) await a.lockout.recordAccountFailure(k);
  const b = freshInstance();
  assert.strictEqual(await b.lockout.checkAccountLockout(k), false,
    'a fresh instance handed the account a new budget');
  assert.strictEqual(await b.lockout.checkAccountLockout(b.lockout.uidKey(902)), true,
    'an account with no failures must stay open on the new instance');
  // and a success on the second instance forgives it on the first
  await b.lockout.clearAccountFailures(k);
  assert.strictEqual(await a.lockout.checkAccountLockout(k), true);
});

test('failures on two overlapping instances add up to one budget', async () => {
  const a = freshInstance();
  const b = freshInstance();
  const k = a.lockout.uidKey(903);
  for (let i = 0; i < a.lockout.MAX_FAILURES - 1; i++) await a.lockout.recordAccountFailure(k);
  assert.strictEqual(await a.lockout.checkAccountLockout(k), true, 'one short of the budget is still open');
  await b.lockout.recordAccountFailure(k);
  assert.strictEqual(await a.lockout.checkAccountLockout(k), false,
    'the failure counted on the other instance did not reach this one');
});

test('the shared table holds a hash, never an email or an IP address', async () => {
  const inst = freshInstance();
  await inst.lockout.recordAccountFailure('alice@example.com');
  const ip = inst.attempts.attemptCounter({ name: 'ip', windowMs: 60_000, max: 10, lockoutMs: 60_000 });
  await ip.record('203.0.113.9');
  const keys = Object.keys(db.pool.authAttempts);
  assert.strictEqual(keys.length, 2);
  for (const k of keys) {
    assert.match(k, /^(acct|ip):[0-9a-f]{64}$/);
    assert.ok(!k.includes('alice') && !k.includes('203.0.113'), k);
  }
});

test('while the database does not answer, this process still counts', async () => {
  const inst = freshInstance();
  const c = inst.attempts.attemptCounter({ name: 'outage', windowMs: 60_000, max: 3, lockoutMs: 60_000 });
  const real = db.pool.execute;
  db.pool.execute = async (sql, params) => {
    if (/auth_attempts/i.test(sql)) { const e = new Error('connect ECONNREFUSED 10.0.0.1:4000'); e.code = 'ECONNREFUSED'; throw e; }
    return real.call(db.pool, sql, params);
  };
  try {
    assert.strictEqual(await c.check('k'), true);
    for (let i = 0; i < 3; i++) await c.record('k');
    assert.strictEqual(await c.check('k'), false, 'an outage made the limit disappear');
    assert.strictEqual(await c.check('other'), true, 'an outage locked a key with no failures');
  } finally {
    db.pool.execute = real;
  }
  // The database is back and holds nothing for 'k'; this process's outage count still refuses.
  assert.strictEqual(await c.check('k'), false);
  // Stated, not hidden: counts made during an outage never reached the database, so a new instance does not see them.
  const other = freshInstance().attempts.attemptCounter({ name: 'outage', windowMs: 60_000, max: 3, lockoutMs: 60_000 });
  assert.strictEqual(await other.check('k'), true);
});

// ── the login routes, between two instances ───────────────────────────────

test('a password guesser locked out on one instance is refused by the next', async () => {
  const A = await serve(freshInstance());
  const reg = await post(A, '/api/auth/register', { email: 'rot-a@example.com', password: 'longenough1' });
  assert.strictEqual(reg.status, 200);
  for (let i = 0; i < 8; i++) {
    const r = await post(A, '/api/auth/login', { email: 'rot-a@example.com', password: 'wrong-password' });
    assert.strictEqual(r.status, 401);
  }
  assert.strictEqual((await post(A, '/api/auth/login', { email: 'rot-a@example.com', password: 'wrong-password' })).status, 429);
  const B = await serve(freshInstance());
  const r = await post(B, '/api/auth/login', { email: 'rot-a@example.com', password: 'longenough1' });
  assert.strictEqual(r.status, 429, 'the next instance gave the locked account a fresh budget');
  // the other arm: a different account from another address is not caught by it.
  // Every request here comes from 127.0.0.1, and the shared per-IP limit (10)
  // has also been reached by now, which the next test covers; clearing the IP
  // rows stands in for "another address".
  for (const k of Object.keys(db.pool.authAttempts)) if (k.startsWith('ip:')) delete db.pool.authAttempts[k];
  const reg2 = await post(B, '/api/auth/register', { email: 'rot-b@example.com', password: 'longenough1' });
  assert.strictEqual(reg2.status, 200);
  const ok = await post(B, '/api/auth/login', { email: 'rot-b@example.com', password: 'longenough1' });
  assert.strictEqual(ok.status, 200);
});

test('the per-IP login limit is shared too', async () => {
  const A = await serve(freshInstance());
  for (let i = 0; i < 10; i++) {
    // ten different accounts, so only the per-IP limit can stop the eleventh
    const r = await post(A, '/api/auth/login', { email: `nobody${i}@example.com`, password: 'x' });
    assert.strictEqual(r.status, 401);
  }
  const B = await serve(freshInstance());
  const r = await post(B, '/api/auth/login', { email: 'nobody-else@example.com', password: 'x' });
  assert.strictEqual(r.status, 429, 'the next instance reset the per-IP limit');
});

// ── the OAuth handoff, between two instances ──────────────────────────────

async function startOn(base) {
  const r = await fetch(base + '/api/auth/oauth/discord/start', { redirect: 'manual' });
  return new URL(await navigationTarget(r)).searchParams.get('state');
}

test('a login started on one instance completes on another, once', async () => {
  const A = await serve(freshInstance());
  const B = await serve(freshInstance());
  const state = await startOn(A);
  const r = await fetch(B + `/api/auth/oauth/discord/callback?code=abc&state=${state}`, { redirect: 'manual' });
  const to = await navigationTarget(r);
  assert.ok(to.startsWith('/#oauth='), to);
  // the same state replayed, on either instance, is refused
  for (const base of [A, B]) {
    const again = await fetch(base + `/api/auth/oauth/discord/callback?code=abc&state=${state}`, { redirect: 'manual' });
    assert.ok((await navigationTarget(again)).startsWith('/#oauth_error='), 'a state was accepted twice');
  }
});

test('a link key minted on one instance is honoured on another, and only as a link key', async () => {
  const inst = freshInstance();
  await inst.oauthStates.put('link', 'k'.repeat(48), { userId: 77 }, Date.now() + 60_000);
  const other = freshInstance().oauthStates;
  assert.strictEqual(await other.take('flow', 'k'.repeat(48)), null, 'a link key was accepted as a login state');
  assert.deepStrictEqual(await other.take('link', 'k'.repeat(48)), { userId: 77 }, 'the wrong-kind attempt must not spend it');
  assert.strictEqual(await inst.oauthStates.take('link', 'k'.repeat(48)), null, 'spent on one instance, accepted on another');
});

test('an expired record is refused, and memory answers only while the database does not', async () => {
  const s = freshInstance().oauthStates;
  await s.put('flow', 'e'.repeat(40), { provider: 'discord' }, Date.now() - 1);
  assert.strictEqual(await s.take('flow', 'e'.repeat(40)), null);
  const real = db.pool.execute;
  db.pool.execute = async (sql, params) => {
    if (/oauth_states/i.test(sql)) throw Object.assign(new Error('down'), { code: 'ECONNREFUSED' });
    return real.call(db.pool, sql, params);
  };
  try {
    await s.put('flow', 'm'.repeat(40), { provider: 'discord' }, Date.now() + 60_000);
    assert.deepStrictEqual(await s.take('flow', 'm'.repeat(40)), { provider: 'discord' });
    assert.strictEqual(await s.take('flow', 'm'.repeat(40)), null, 'single-use in memory too');
  } finally {
    db.pool.execute = real;
  }
});

test('two callbacks racing on one state: exactly one wins', async () => {
  const a = freshInstance().oauthStates;
  const b = freshInstance().oauthStates;
  await a.put('flow', 'r'.repeat(40), { provider: 'discord' }, Date.now() + 60_000);
  const got = await Promise.all([a.take('flow', 'r'.repeat(40)), b.take('flow', 'r'.repeat(40))]);
  assert.strictEqual(got.filter(Boolean).length, 1, `both racers got the state: ${JSON.stringify(got)}`);
});

test('a failure after a stale count starts a fresh one', async () => {
  const inst = freshInstance();
  const k = inst.lockout.uidKey(904);
  await inst.lockout._seed(k, {
    count: inst.lockout.MAX_FAILURES,
    firstAttempt: Date.now() - (inst.lockout.WINDOW_MS + 1000),
    lockedUntil: Date.now() - 1000,
  });
  await inst.lockout.recordAccountFailure(k);
  assert.strictEqual(await inst.lockout.checkAccountLockout(k), true,
    'one new failure after a forgiven window locked the account at once');
  const [row] = Object.values(db.pool.authAttempts);
  assert.strictEqual(Number(row.fails), 1);
});

test('the failure that reaches the budget starts the lockout clock', async () => {
  const inst = freshInstance();
  const k = inst.lockout.uidKey(905);
  const before = Date.now();
  for (let i = 0; i < inst.lockout.MAX_FAILURES - 1; i++) await inst.lockout.recordAccountFailure(k);
  assert.strictEqual(Object.values(db.pool.authAttempts)[0].locked_until, null, 'locked before the budget was spent');
  await inst.lockout.recordAccountFailure(k);
  const lockedUntil = Number(Object.values(db.pool.authAttempts)[0].locked_until);
  assert.ok(lockedUntil >= before + inst.lockout.LOCKOUT_MS, `locked_until ${lockedUntil} is not a lockout from now`);
});

test('a record that expired since another instance stored it is refused', async () => {
  // put() prunes expired rows, so this plants one directly, as another
  // instance would have left it with no prune since: only the TTL check in
  // take() stands between it and a login.
  await db.pool.execute(
    'REPLACE INTO oauth_states (state_key, kind, payload, expires_at) VALUES (?, ?, ?, ?)',
    ['x'.repeat(40), 'flow', JSON.stringify({ provider: 'discord' }), Date.now() - 1]);
  assert.strictEqual(await freshInstance().oauthStates.take('flow', 'x'.repeat(40)), null);
  // the other arm: the same row, unexpired, is accepted
  await db.pool.execute(
    'REPLACE INTO oauth_states (state_key, kind, payload, expires_at) VALUES (?, ?, ?, ?)',
    ['y'.repeat(40), 'flow', JSON.stringify({ provider: 'discord' }), Date.now() + 60_000]);
  assert.deepStrictEqual(await freshInstance().oauthStates.take('flow', 'y'.repeat(40)), { provider: 'discord' });
});
