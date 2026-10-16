'use strict';
/**
 * Sign in with Pi: the App Studio exchange and the route, over every answer.
 *
 * lib/pi_auth.js exchanges the access token from Pi.authenticate with Pi's
 * App Studio (POST /pi/auth/v1/login), the flow Pi requires of every app.
 * Three answers, not two: App Studio vouches (a session), App Studio refuses
 * (401, the caller is not who they say), App Studio cannot be asked or
 * answers nothing usable (503, ours, NOT a refusal). Nothing here reaches the
 * network: App Studio is stood in for by a recording fetch, so what would be
 * sent is checked as well as what comes back, and the route is driven over
 * HTTP on 127.0.0.1 against the real router and the MemoryDB.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const piAuth = require('../lib/pi_auth');
const { codeOnly } = require('./helpers/code_only');

const TOKEN = 'piAccessToken_abcdefghijklmnopqrstuvwxyz0123456789';
const VOUCHED = { sessionToken: 'st_1', user: { uid: 'a1111111-aaaa-bbbb-2222-ccccccc3333d', username: 'pioneer' } };

/** A stand-in for App Studio's login that records what it was asked. */
function fakeStudio(answer) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    const a = typeof answer === 'function' ? answer(url, init) : answer;
    if (a instanceof Error) throw a;
    return {
      ok: a.status >= 200 && a.status < 300,
      status: a.status,
      json: async () => {
        if (a.body === undefined) throw new SyntaxError('not json');
        return a.body;
      },
    };
  };
  return { calls, fetch: fetchImpl };
}

// ── the exchange ──────────────────────────────────────────────────────────

test('a token App Studio vouches for answers its uid, asked the way Pi documents', async () => {
  const studio = fakeStudio({ status: 200, body: VOUCHED });
  const v = await piAuth.verifyAccessToken(TOKEN, { fetch: studio.fetch, authOrigin: 'https://studio.test' });
  assert.deepStrictEqual(v, { ok: true, uid: 'a1111111-aaaa-bbbb-2222-ccccccc3333d', username: 'pioneer' });
  assert.strictEqual(studio.calls.length, 1, 'one exchange per sign-in');
  const [{ url, init }] = studio.calls;
  assert.strictEqual(url, 'https://studio.test/pi/auth/v1/login');
  assert.strictEqual(init.method, 'POST');
  assert.strictEqual(init.headers['content-type'], 'application/json');
  assert.deepStrictEqual(JSON.parse(init.body), { accessToken: TOKEN });
  assert.ok(init.signal, 'a call with no time limit can hold a sign-in open forever');
});

test('the app never calls the Platform\'s /v2/me itself', () => {
  // Pi: "Never call the Pi Platform's /v2/me yourself to authenticate a user".
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'lib', 'pi_auth.js'), 'utf8'));
  assert.doesNotMatch(src, /\/v2\/me|api\.minepi\.com/);
});

for (const status of [401, 403]) {
  test(`a token App Studio answers ${status} for is refused, not unavailable`, async () => {
    const studio = fakeStudio({ status, body: { error: 'invalid' } });
    assert.deepStrictEqual(
      await piAuth.verifyAccessToken(TOKEN, { fetch: studio.fetch }),
      { ok: false, reason: 'token_rejected' });
  });
}

for (const [name, answer] of [
  ['a 500', { status: 500, body: {} }],
  ['a 200 with no user', { status: 200, body: { sessionToken: 'st' } }],
  ['a 200 with no uid', { status: 200, body: { user: { username: 'x' } } }],
  ['a 200 whose uid is not an id', { status: 200, body: { user: { uid: 'a b; drop' } } }],
  ['a 200 with the uid at the top level only', { status: 200, body: { uid: 'u-top' } }],
  ['a body that is not JSON', { status: 200 }],
  ['a network failure', new TypeError('fetch failed')],
]) {
  test(`${name} is unavailable (throws), never a refusal of the person`, async () => {
    const studio = fakeStudio(answer);
    await assert.rejects(() => piAuth.verifyAccessToken(TOKEN, { fetch: studio.fetch }));
  });
}

test('a malformed token is refused without asking App Studio', async () => {
  const studio = fakeStudio({ status: 200, body: VOUCHED });
  for (const bad of ['', 'short', 42, null, undefined, 'has spaces in it and more', 'x'.repeat(3000)]) {
    assert.deepStrictEqual(await piAuth.verifyAccessToken(bad, { fetch: studio.fetch }),
      { ok: false, reason: 'malformed_token' });
  }
  assert.strictEqual(studio.calls.length, 0);
});

test('the origin defaults to App Studio\'s backend and can be pointed elsewhere', () => {
  assert.strictEqual(piAuth.authOrigin({}), 'https://backend.appstudio-u7cm9zhmha0ruwv8.piappengine.com');
  assert.strictEqual(piAuth.authOrigin({ PI_AUTH_ORIGIN: 'https://studio.test///' }), 'https://studio.test');
});

// ── the route, over HTTP ──────────────────────────────────────────────────

async function serve(studioAnswer) {
  const router = require('../routes/pi_auth');
  const studio = fakeStudio(studioAnswer);
  router._deps = { fetch: studio.fetch, authOrigin: 'https://studio.test' };
  const app = express();
  app.use(express.json());
  app.use('/api/pi', router);
  app.use('/api/arena', require('../routes/arena'));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const post = (body) => fetch(`${base}/api/pi/signin`, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  }).then(async (r) => ({ status: r.status, body: await r.json().catch(() => null) }));
  return { server, base, studio, post };
}

async function userCount() {
  // The MemoryDB's own rows: no COUNT statement exists for it to answer.
  return require('../db').pool.users.length;
}

test('a vouched token signs in, the session works on the arena, and the same Pi user is one account', async () => {
  const { server, base, post } = await serve({ status: 200, body: { sessionToken: 'st', user: { uid: 'pi-uid-route-1', username: 'p1' } } });
  try {
    const first = await post({ accessToken: TOKEN });
    assert.strictEqual(first.status, 200, JSON.stringify(first.body));
    assert.ok(first.body.token, 'no session token');
    assert.strictEqual(first.body.handle, null, 'a handle was invented for a new account');

    const acct = await fetch(`${base}/api/arena/account`, {
      headers: { authorization: `Bearer ${first.body.token}` } });
    assert.strictEqual(acct.status, 200, 'the session does not open the arena');

    const jwt = require('jsonwebtoken');
    const a = jwt.decode(first.body.token).user_id;
    const second = await post({ accessToken: TOKEN });
    assert.strictEqual(jwt.decode(second.body.token).user_id, a, 'one Pi user became two accounts');
  } finally {
    server.close();
  }
});

test('a token App Studio refuses is a 401 and creates no account', async () => {
  const { server, post } = await serve({ status: 401, body: {} });
  try {
    const before = await userCount();
    const r = await post({ accessToken: TOKEN });
    assert.strictEqual(r.status, 401);
    assert.deepStrictEqual(r.body, { error: 'pi_rejected', reason: 'token_rejected' });
    assert.strictEqual(await userCount(), before);
  } finally {
    server.close();
  }
});

test('App Studio unreachable is a 503, not a refusal, and creates no account', async () => {
  const { server, post } = await serve(new TypeError('fetch failed'));
  try {
    const before = await userCount();
    const r = await post({ accessToken: TOKEN });
    assert.strictEqual(r.status, 503);
    assert.deepStrictEqual(r.body, { error: 'verifier_unavailable' });
    assert.strictEqual(await userCount(), before);
  } finally {
    server.close();
  }
});

test('no token is a 400, and App Studio is not asked', async () => {
  const { server, studio, post } = await serve({ status: 200, body: VOUCHED });
  try {
    for (const body of [{}, { accessToken: '' }, { accessToken: 7 }, { uid: 'pi-uid-claimed' }]) {
      const r = await post(body);
      assert.strictEqual(r.status, 400, JSON.stringify(body));
    }
    assert.strictEqual(studio.calls.length, 0);
  } finally {
    server.close();
  }
});

test('a uid or username in the request is never the identity; App Studio\'s is', async () => {
  // The SDK hands the page a uid and username too. Whatever the request
  // carries beside the token, the account is the one App Studio's answer names.
  const { server, post } = await serve({ status: 200, body: { sessionToken: 'st', user: { uid: 'pi-uid-from-studio', username: 'real' } } });
  try {
    const r = await post({ accessToken: TOKEN, uid: 'pi-uid-someone-else', username: 'impostor', user: { uid: 'pi-uid-someone-else' } });
    assert.strictEqual(r.status, 200);
    const { pool } = require('../db');
    const [claimed] = await pool.execute('SELECT id, email, plan, token_epoch FROM users WHERE pi_uid = ? LIMIT 1', ['pi-uid-someone-else']);
    assert.strictEqual(claimed.length, 0, 'the request chose whose account it signed in to');
    const [stored] = await pool.execute('SELECT id, email, plan, token_epoch FROM users WHERE pi_uid = ? LIMIT 1', ['pi-uid-from-studio']);
    assert.strictEqual(stored.length, 1, 'the identity App Studio returned was not the one stored');
    const jwt = require('jsonwebtoken');
    assert.strictEqual(jwt.decode(r.body.token).user_id, stored[0].id);
  } finally {
    server.close();
  }
});

// ── the shape of the route ────────────────────────────────────────────────

const ROUTE = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'routes', 'pi_auth.js'), 'utf8'));

test('sign-in is rate limited by IP inside the router', () => {
  assert.match(ROUTE, /router\.use\(rateLimit\(\{[^}]*name:\s*'pi_auth'[^}]*key:\s*ipKey/);
});

test('a session is signed once, and only after App Studio vouched', () => {
  assert.strictEqual((ROUTE.match(/signToken\(/g) || []).length, 1);
  assert.ok(ROUTE.indexOf('signToken(') > ROUTE.indexOf('if (!verdict.ok)'),
    'a session is minted before the refusal is checked');
});

test('the provider maps to its own column, and an unknown one is refused', async () => {
  const auth = require('../auth');
  await assert.rejects(() => auth.findOrCreateOAuthUser({ provider: 'nope', providerId: '1' }),
    /unknown provider/, 'an unmapped provider fell back to another provider\'s column');
});
