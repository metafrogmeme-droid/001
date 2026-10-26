'use strict';
/**
 * Sign in with Pi: the Platform check and the route, over every answer.
 *
 * lib/pi_auth.js checks the access token from Pi.authenticate with the Pi
 * Platform (GET https://api.minepi.com/v2/me, Authorization: Bearer), the
 * call Pi documents for a self-hosted app. The same call serves the Testnet
 * host and www. Three answers, not two: Pi vouches (a session), Pi refuses
 * (401, the caller is not who they say), Pi cannot be asked or answers
 * nothing usable (503, ours, NOT a refusal). Nothing here reaches the
 * network: the Platform is stood in for by a recording fetch, so what would
 * be sent is checked as well as what comes back, and the route is driven
 * over HTTP on 127.0.0.1 against the real router and the MemoryDB.
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
/** UserDTO, as the Platform API documents it: uid at the top, username optional. */
const VOUCHED = {
  uid: 'a1111111-aaaa-bbbb-2222-ccccccc3333d',
  username: 'pioneer',
  credentials: { scopes: ['username', 'payments'] },
};

/** A stand-in for the Platform that records what it was asked. */
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

test('a token Pi vouches for answers its uid, asked the way the Platform documents', async () => {
  const studio = fakeStudio({ status: 200, body: VOUCHED });
  const v = await piAuth.verifyAccessToken(TOKEN, { fetch: studio.fetch, apiBase: 'https://api.minepi.test/v2' });
  assert.deepStrictEqual(v, { ok: true, uid: 'a1111111-aaaa-bbbb-2222-ccccccc3333d', username: 'pioneer' });
  assert.strictEqual(studio.calls.length, 1, 'one check per sign-in');
  const [{ url, init }] = studio.calls;
  assert.strictEqual(url, 'https://api.minepi.test/v2/me');
  assert.strictEqual(init.method, 'GET');
  assert.strictEqual(init.body, undefined, 'the token must not travel in a body');
  assert.strictEqual(init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.strictEqual(init.headers.accept, 'application/json');
  assert.doesNotMatch(url, new RegExp(TOKEN), 'the token must not travel in the URL');
  assert.ok(init.signal, 'a call with no time limit can hold a sign-in open forever');
});

test('the check is GET https://api.minepi.com/v2/me, and the old origin knob is ignored', async () => {
  const src = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'lib', 'pi_auth.js'), 'utf8'));
  assert.match(src, /https:\/\/api\.minepi\.com\/v2/);
  assert.match(src, /Authorization:\s*`Bearer \$\{accessToken\}`/);
  assert.match(src, /method:\s*'GET'/);
  assert.doesNotMatch(src, /appstudio|piappengine|\/pi\/auth\/v1\/login|PI_AUTH_ORIGIN/);
  assert.doesNotMatch(src, /PI_NETWORK_API_KEY|PI_TESTNET_API_KEY|PI_PROXY_SECRET|Authorization:\s*`Key /);
  const prev = process.env.PI_AUTH_ORIGIN;
  process.env.PI_AUTH_ORIGIN = 'https://backend.appstudio-u7cm9zhmha0ruwv8.piappengine.com';
  const studio = fakeStudio({ status: 200, body: VOUCHED });
  try {
    await piAuth.verifyAccessToken(TOKEN, { fetch: studio.fetch, authOrigin: 'https://evil.example' });
    assert.strictEqual(studio.calls[0].url, 'https://api.minepi.com/v2/me');
    assert.strictEqual(piAuth.meUrl({}), 'https://api.minepi.com/v2/me');
    assert.strictEqual(piAuth.API_BASE, 'https://api.minepi.com/v2');
  } finally {
    if (prev === undefined) delete process.env.PI_AUTH_ORIGIN;
    else process.env.PI_AUTH_ORIGIN = prev;
  }
});

test('Pi\'s real refusal is an empty 401, and it is a refusal', async () => {
  // Measured 2026-10-26: an invalid bearer gets 401, content-type text/html,
  // content-length 0, and WWW-Authenticate invalid_token. Parsing that body
  // would throw and the route would say unavailable.
  const fetchImpl = async () => new Response('', {
    status: 401,
    headers: {
      'content-type': 'text/html',
      'www-authenticate': 'Bearer realm="Doorkeeper", error="invalid_token"',
    },
  });
  assert.deepStrictEqual(
    await piAuth.verifyAccessToken(TOKEN, { fetch: fetchImpl }),
    { ok: false, reason: 'token_rejected' });
});

for (const status of [401, 403]) {
  test(`a token Pi answers ${status} for is refused, not unavailable`, async () => {
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
  ['a 200 whose uid is not an id', { status: 200, body: { uid: 'a b; drop', username: 'x' } }],
  ['a 200 whose uid is nested under user, App Studio\'s old shape', { status: 200, body: { user: { uid: 'nested-uid', username: 'x' } } }],
  ['a body that is not JSON', { status: 200 }],
  ['a network failure', new TypeError('fetch failed')],
]) {
  test(`${name} is unavailable (throws), never a refusal of the person`, async () => {
    const studio = fakeStudio(answer);
    await assert.rejects(() => piAuth.verifyAccessToken(TOKEN, { fetch: studio.fetch }));
  });
}

test('a malformed token is refused without asking Pi', async () => {
  const studio = fakeStudio({ status: 200, body: VOUCHED });
  for (const bad of ['', 'short', 42, null, undefined, 'has spaces in it and more', 'x'.repeat(3000)]) {
    assert.deepStrictEqual(await piAuth.verifyAccessToken(bad, { fetch: studio.fetch }),
      { ok: false, reason: 'malformed_token' });
  }
  assert.strictEqual(studio.calls.length, 0);
});

test('a thrown error names the status and never the token', async () => {
  const studio = fakeStudio({ status: 500, body: { accessToken: TOKEN, detail: TOKEN } });
  await assert.rejects(
    () => piAuth.verifyAccessToken(TOKEN, { fetch: studio.fetch }),
    (err) => {
      assert.match(err.message, /500/);
      assert.ok(!err.message.includes(TOKEN) && !(err.stack || '').includes(TOKEN));
      return true;
    });
});

// ── the route, over HTTP ──────────────────────────────────────────────────

async function serve(studioAnswer) {
  const router = require('../routes/pi_auth');
  const studio = fakeStudio(studioAnswer);
  router._deps = { fetch: studio.fetch, apiBase: 'https://api.minepi.test/v2' };
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
  const { server, base, studio, post } = await serve({ status: 200, body: { uid: 'pi-uid-route-1', username: 'p1' } });
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
    assert.ok(studio.calls.every((c) => c.url === 'https://api.minepi.test/v2/me' && c.init.method === 'GET'));
  } finally {
    server.close();
  }
});

test('a new account is offered the username Pi verified as a board name; nothing is saved, and a named account is offered none', async () => {
  const { server, post } = await serve({ status: 200, body: { uid: 'pi-uid-suggest-1', username: 'Pi.o-neer!' } });
  try {
    // The page's own copy of the username is never the suggestion.
    const first = await post({ accessToken: TOKEN, username: 'impostor' });
    assert.strictEqual(first.status, 200, JSON.stringify(first.body));
    assert.strictEqual(first.body.handle, null);
    assert.strictEqual(first.body.suggested_handle, 'Pioneer', 'the suggestion is not Pi\'s username cut to the board\'s rule');
    const jwt = require('jsonwebtoken');
    const id = jwt.decode(first.body.token).user_id;
    const row = require('../db').pool.users.find((u) => u.id === id);
    assert.strictEqual(row.leaderboard_handle == null, true, 'the username was saved without the person choosing it');
    // Once a name is chosen, sign-in answers it and offers nothing.
    row.leaderboard_handle = 'chosen_one';
    const second = await post({ accessToken: TOKEN });
    assert.strictEqual(second.body.handle, 'chosen_one');
    assert.strictEqual(second.body.suggested_handle, null);
  } finally {
    server.close();
  }
});

test('the suggestion is cut to what the board accepts, or there is none', () => {
  const { handleSuggestion } = require('../lib/pi_auth');
  assert.strictEqual(handleSuggestion('pioneer'), 'pioneer');
  assert.strictEqual(handleSuggestion('a'.repeat(30)), 'a'.repeat(20));
  assert.strictEqual(handleSuggestion('x.y'), null, 'two characters is not a name');
  assert.strictEqual(handleSuggestion('日本語'), null);
  assert.strictEqual(handleSuggestion(null), null);
  assert.strictEqual(handleSuggestion(42), null);
  // Whatever it offers, the board's own rule accepts (routes/leaderboard.js).
  for (const u of ['pioneer', 'Pi.o-neer!', 'a'.repeat(30), 'under_score_9']) {
    assert.match(handleSuggestion(u), /^[A-Za-z0-9_]{3,20}$/);
  }
});

test('a token Pi refuses is a 401 and creates no account', async () => {
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

test('Pi unreachable is a 503, not a refusal, and creates no account', async () => {
  const { server, post } = await serve(new TypeError('fetch failed'));
  const lines = [];
  const saved = console.error;
  console.error = (...a) => lines.push(a.map(String).join(' '));
  try {
    const before = await userCount();
    const r = await post({ accessToken: TOKEN });
    assert.strictEqual(r.status, 503);
    assert.deepStrictEqual(r.body, { error: 'verifier_unavailable' });
    assert.strictEqual(await userCount(), before);
    assert.ok(lines.some((l) => /Pi \/v2\/me unavailable/.test(l)));
    assert.ok(lines.every((l) => !l.includes(TOKEN)), 'the token was logged');
    assert.ok(!JSON.stringify(r.body).includes(TOKEN));
  } finally {
    console.error = saved;
    server.close();
  }
});

test('a 500 whose body repeats the token is a 503 and the token stays out of the log', async () => {
  const { server, post } = await serve({ status: 500, body: { leak: TOKEN } });
  const lines = [];
  const saved = console.error;
  console.error = (...a) => lines.push(a.map(String).join(' '));
  try {
    const r = await post({ accessToken: TOKEN });
    assert.strictEqual(r.status, 503);
    assert.deepStrictEqual(r.body, { error: 'verifier_unavailable' });
    assert.ok(lines.every((l) => !l.includes(TOKEN)));
  } finally {
    console.error = saved;
    server.close();
  }
});

test('no token is a 400, and Pi is not asked', async () => {
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

test('a uid or username in the request is never the identity; Pi\'s is', async () => {
  // The SDK hands the page a uid and username too. Whatever the request
  // carries beside the token, the account is the one Pi's UserDTO names.
  const { server, post } = await serve({ status: 200, body: { uid: 'pi-uid-from-platform', username: 'real' } });
  try {
    const r = await post({ accessToken: TOKEN, uid: 'pi-uid-someone-else', username: 'impostor', user: { uid: 'pi-uid-someone-else' } });
    assert.strictEqual(r.status, 200);
    const { pool } = require('../db');
    const [claimed] = await pool.execute('SELECT id, email, plan, token_epoch FROM users WHERE pi_uid = ? LIMIT 1', ['pi-uid-someone-else']);
    assert.strictEqual(claimed.length, 0, 'the request chose whose account it signed in to');
    const [stored] = await pool.execute('SELECT id, email, plan, token_epoch FROM users WHERE pi_uid = ? LIMIT 1', ['pi-uid-from-platform']);
    assert.strictEqual(stored.length, 1, 'the identity Pi returned was not the one stored');
    const jwt = require('jsonwebtoken');
    assert.strictEqual(jwt.decode(r.body.token).user_id, stored[0].id);
  } finally {
    server.close();
  }
});

test('App Studio\'s nested user is not an identity and creates no account', async () => {
  const { server, post } = await serve({ status: 200, body: { sessionToken: 'st', user: { uid: 'pi-uid-nested', username: 'nested' } } });
  const saved = console.error;
  console.error = () => {};
  try {
    const before = await userCount();
    const r = await post({ accessToken: TOKEN });
    assert.strictEqual(r.status, 503);
    assert.deepStrictEqual(r.body, { error: 'verifier_unavailable' });
    assert.strictEqual(await userCount(), before);
  } finally {
    console.error = saved;
    server.close();
  }
});

// ── the shape of the route ────────────────────────────────────────────────

const ROUTE = codeOnly(fs.readFileSync(path.join(__dirname, '..', 'routes', 'pi_auth.js'), 'utf8'));

test('sign-in is rate limited by IP inside the router', () => {
  assert.match(ROUTE, /router\.use\(rateLimit\(\{[^}]*name:\s*'pi_auth'[^}]*key:\s*ipKey/);
});

test('a session is signed once, and only after Pi vouched', () => {
  assert.strictEqual((ROUTE.match(/signToken\(/g) || []).length, 1);
  assert.ok(ROUTE.indexOf('signToken(') > ROUTE.indexOf('if (!verdict.ok)'),
    'a session is minted before the refusal is checked');
});

test('the provider maps to its own column, and an unknown one is refused', async () => {
  const auth = require('../auth');
  await assert.rejects(() => auth.findOrCreateOAuthUser({ provider: 'nope', providerId: '1' }),
    /unknown provider/, 'an unmapped provider fell back to another provider\'s column');
});
