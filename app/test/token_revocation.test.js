'use strict';
/**
 * "Log out" forgot the token and revoked nothing.
 *
 * Express issued `jwt.sign({user_id, email})` with a 30-day default, and
 * `authMiddleware` called `jwt.verify` with no server-side state behind it.
 * `jwt.verify` answers "did we sign this, and is it still inside its
 * lifetime" — and nothing at all about whether the session is still meant to
 * exist. There was no logout route anywhere in `app/`; the client dropped the
 * token from local storage and called it done.
 *
 * So a stolen token was up to a month of account access that could not be
 * taken back, on a platform where an account submits exchange API keys. And a
 * password change — the thing a person does when they believe they are
 * compromised — did not touch the attacker's session at all.
 *
 * `bot/api/token_store.py` has done this correctly since RC-AUD-020: "a
 * per-user token epoch (bumped on logout → invalidates every prior token)".
 * The fix was written, tested, and never crossed the language boundary — the
 * same shape as H5, where `bot/backtest/parity.py` had the right path all
 * along.
 *
 * The test that matters is `a token minted before logout stops working`.
 * Everything else here guards the ways a revocation scheme fails quietly:
 * locking everyone out, locking nobody out, or logging you out of the tab you
 * were securing your account from.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const authModule = require('../auth');
const { pool } = require('../db');

let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  // A route that requires auth, so "is this token still good" is observable.
  app.get('/api/protected', authModule.authMiddleware, (req, res) =>
    res.json({ ok: true, user_id: req.user.user_id }));
  // And one that only IDENTIFIES, to pin the degrade-to-anonymous contract.
  app.get('/api/optional', authModule.optionalAuth, (req, res) =>
    res.json({ identified: !!req.user }));
  await new Promise((r) => { server = app.listen(0, '127.0.0.1', r); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

function req(method, path, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json',
                        'Content-Length': Buffer.byteLength(payload) } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode,
                                    data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

/**
 * Seed a user straight into the database.
 *
 * NOT through POST /register, deliberately. Registration and login share a
 * per-IP rate limiter — added because register was "the only unthrottled auth
 * route" — and a file that makes a dozen accounts from one address exhausts
 * it and starts failing on 429s that have nothing to do with revocation.
 *
 * Loosening the limiter for tests was the other option and would have been
 * the wrong one: it is real protection, and a test that needs it weakened is
 * a test measuring the wrong thing. The HTTP login path is still exercised
 * below, in the two tests where logging in IS the behaviour under test.
 */
let seq = 0;
async function newUser() {
  const bcrypt = require('bcryptjs');
  const email = `rev${Date.now()}_${seq++}@example.com`;
  const password = 'longenoughpassword1';
  const hash = await bcrypt.hash(password, 4);       // low cost: fixture, not a check
  await pool.execute(
    'INSERT INTO users (email, password_hash) VALUES (?, ?)', [email, hash]);
  const [rows] = await pool.execute(
    'SELECT id, email, token_epoch FROM users WHERE email = ?', [email]);
  assert.ok(rows.length, 'the fixture user was not created');
  return {
    email, password, user_id: rows[0].id,
    token: authModule.signToken({ id: rows[0].id, email,
                                  token_epoch: rows[0].token_epoch }),
  };
}


// ── the defect ───────────────────────────────────────────────────────

test('a token minted before logout stops working', async () => {
  const u = await newUser();
  assert.equal((await req('GET', '/api/protected', { token: u.token })).status, 200);

  const out = await req('POST', '/api/auth/logout', { token: u.token });
  assert.equal(out.status, 200, JSON.stringify(out.data));

  const after = await req('GET', '/api/protected', { token: u.token });
  assert.equal(after.status, 401,
    'the token still authenticates after logout — "log out" forgot the token '
    + 'on one device and revoked nothing');
});

test('logout ends OTHER sessions too, not just the one that called it', async () => {
  // The reason a user reaches for logout when they suspect a compromise.
  const u = await newUser();
  const second = await req('POST', '/api/auth/login',
    { body: { email: u.email, password: u.password } });
  assert.equal(second.status, 200);
  const stolen = second.data.token;
  assert.equal((await req('GET', '/api/protected', { token: stolen })).status, 200);

  await req('POST', '/api/auth/logout', { token: u.token });

  assert.equal((await req('GET', '/api/protected', { token: stolen })).status, 401,
    'a second live session survived logout — the copy someone else holds is '
    + 'exactly the one that matters');
});

test('a password change invalidates outstanding tokens', async () => {
  // The audit names this specifically: changing a password is how someone
  // responds to a compromise, and it did not touch the attacker's session.
  const u = await newUser();
  const attacker = (await req('POST', '/api/auth/login',
    { body: { email: u.email, password: u.password } })).data.token;
  assert.equal((await req('GET', '/api/protected', { token: attacker })).status, 200);

  const changed = await req('POST', '/api/auth/change-password', {
    token: u.token,
    body: { current_password: u.password, new_password: 'a-brand-new-password-9' },
  });
  assert.equal(changed.status, 200, JSON.stringify(changed.data));

  assert.equal((await req('GET', '/api/protected', { token: attacker })).status, 401,
    'securing the account left the attacker logged in for up to 30 more days');
});

test('changing your password does not log you out of the tab you did it in', async () => {
  // A fix that locks the owner out while locking the attacker out is a fix
  // people work around by never changing their password.
  const u = await newUser();
  const changed = await req('POST', '/api/auth/change-password', {
    token: u.token,
    body: { current_password: u.password, new_password: 'another-good-password-7' },
  });
  assert.ok(changed.data.token, 'no replacement token was issued');
  assert.equal((await req('GET', '/api/protected', { token: changed.data.token })).status,
    200, 'the freshly issued token was born already revoked');
});


// ── the ways a revocation scheme fails quietly ───────────────────────

test('you can log back in after logging out', async () => {
  // THE regression this design invites. The epoch is stamped into the token
  // at issue, so if any issuing path forgets to read the CURRENT epoch it
  // mints tokens at 0 — which verify rejects the moment a revocation has
  // happened. Symptom: logout works once and the account is bricked.
  const u = await newUser();
  await req('POST', '/api/auth/logout', { token: u.token });

  const back = await req('POST', '/api/auth/login',
    { body: { email: u.email, password: u.password } });
  assert.equal(back.status, 200, JSON.stringify(back.data));
  assert.equal((await req('GET', '/api/protected', { token: back.data.token })).status,
    200, 'the account cannot be logged into again after a single logout');
});

test('two logouts in a row still leave the account usable', async () => {
  const u = await newUser();
  await req('POST', '/api/auth/logout', { token: u.token });
  const a = (await req('POST', '/api/auth/login',
    { body: { email: u.email, password: u.password } })).data.token;
  await req('POST', '/api/auth/logout', { token: a });
  const b = await req('POST', '/api/auth/login',
    { body: { email: u.email, password: u.password } });
  assert.equal((await req('GET', '/api/protected', { token: b.data.token })).status, 200);
});

test('one user logging out does not touch another user', async () => {
  // A global epoch instead of a per-user one would pass every test above and
  // log out the entire platform on any logout.
  const a = await newUser();
  const b = await newUser();
  await req('POST', '/api/auth/logout', { token: a.token });
  assert.equal((await req('GET', '/api/protected', { token: b.token })).status, 200,
    'logging one user out ended another user\'s session');
});

test('logout requires a valid token', async () => {
  assert.equal((await req('POST', '/api/auth/logout')).status, 401);
  assert.equal((await req('POST', '/api/auth/logout', { token: 'not-a-jwt' })).status, 401);
});

test('a revoked token identifies nobody on an optional-auth route', async () => {
  // optionalAuth must DEGRADE, not sail through: `req.user` only ever adds to
  // a response, and a logged-out session must stop adding.
  const u = await newUser();
  assert.equal((await req('GET', '/api/optional', { token: u.token })).data.identified,
    true);
  await req('POST', '/api/auth/logout', { token: u.token });
  assert.equal((await req('GET', '/api/optional', { token: u.token })).data.identified,
    false, 'a revoked token still identified its holder to a public surface');
});

test('optional auth still serves anonymous callers', async () => {
  const r = await req('GET', '/api/optional');
  assert.equal(r.status, 200);
  assert.equal(r.data.identified, false);
});


// ── the shape of the token ───────────────────────────────────────────

test('issued tokens carry the epoch', async () => {
  const jwt = require('jsonwebtoken');
  const u = await newUser();
  const claims = jwt.verify(u.token, process.env.JWT_SECRET);
  assert.equal(typeof claims.epoch, 'number',
    'no epoch in the token — nothing to compare against, so nothing is revocable');
});

test('a token predating this feature is honoured until an actual revocation', async () => {
  // Every token in the wild right now has no epoch claim. Reading that as
  // "revoked" would log the entire user base out on deploy; reading it as 0
  // keeps them in until something real bumps the epoch above 0.
  const jwt = require('jsonwebtoken');
  const u = await newUser();
  const legacy = jwt.sign({ user_id: u.user_id, email: u.email },
                          process.env.JWT_SECRET, { expiresIn: '30d' });
  assert.equal((await req('GET', '/api/protected', { token: legacy })).status, 200,
    'the deploy that adds revocation would have logged everybody out');

  await req('POST', '/api/auth/logout', { token: u.token });
  assert.equal((await req('GET', '/api/protected', { token: legacy })).status, 401,
    'a legacy token survived an explicit revocation');
});

test('revocation is per-user and monotonic', async () => {
  const u = await newUser();
  const first = await req('POST', '/api/auth/logout', { token: u.token });
  const relogin = (await req('POST', '/api/auth/login',
    { body: { email: u.email, password: u.password } })).data.token;
  const second = await req('POST', '/api/auth/logout', { token: relogin });
  assert.ok(second.data.epoch > first.data.epoch,
    'the epoch did not advance — a second logout revoked nothing');
});

test('the revocation helper is exported for other surfaces to use', () => {
  // So the next place that needs "end this user's sessions" reaches for the
  // one implementation instead of writing a second one.
  assert.strictEqual(typeof authModule.revokeUserTokens, 'function');
  assert.strictEqual(typeof authModule.tokenIsCurrent, 'function');
});
