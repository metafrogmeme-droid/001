'use strict';
/**
 * M14, the other half: the session becomes a cookie the page cannot read.
 *
 * `script-src` no longer admits arbitrary inline script, but the bearer token
 * still sat in localStorage — readable by any script that does run. The audit
 * offers the two measures as independent breaks in the same chain; removing
 * `'unsafe-inline'` was the first, and this is the second.
 *
 * This file covers the SERVER half: issue, read, clear. The browser keeps
 * using its Authorization header until the client half lands, which is exactly
 * why the reading order is header-first — every existing caller (MCP tools,
 * curl, the Telegram link flow) must take the identical path it always did,
 * and the suite passing unchanged is the evidence.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const sc = require('../lib/session_cookie');

// ── the parser ────────────────────────────────────────────────────────────

test('cookie parsing survives the shapes a browser actually sends', () => {
  assert.deepStrictEqual(sc.parseCookies('a=1; b=2'), { a: '1', b: '2' });
  assert.deepStrictEqual(sc.parseCookies('  a = 1 ;b=2  '), { a: '1', b: '2' });
  assert.deepStrictEqual(sc.parseCookies('a="quoted"'), { a: 'quoted' });
  assert.deepStrictEqual(sc.parseCookies('a=x%20y'), { a: 'x y' });
  // First wins — a second `rc_jwt` appended by anything must not shadow ours.
  assert.deepStrictEqual(sc.parseCookies('a=1; a=2'), { a: '1' });
  // Never throws, whatever arrives.
  for (const junk of ['', null, undefined, 'nonsense', '=;=;', 'a', 'a=%E0%A4%A']) {
    assert.strictEqual(typeof sc.parseCookies(junk), 'object');
  }
});

// ── reading order ─────────────────────────────────────────────────────────

const reqWith = (headers) => ({ headers });

test('the Authorization header still wins, so no existing caller moves', () => {
  const req = reqWith({ authorization: 'Bearer header-token',
                        cookie: 'rc_jwt=cookie-token' });
  assert.strictEqual(sc.tokenFromRequest(req), 'header-token');
});

test('the cookie is read when no header is offered', () => {
  assert.strictEqual(
    sc.tokenFromRequest(reqWith({ cookie: 'rc_jwt=cookie-token' })),
    'cookie-token');
});

test('an empty Bearer falls through rather than shadowing the cookie', () => {
  // `Bearer ` with nothing after it used to reach jwt.verify('') and fail as
  // "Invalid token"; it must not now also hide a perfectly good cookie.
  assert.strictEqual(
    sc.tokenFromRequest(reqWith({ authorization: 'Bearer ',
                                  cookie: 'rc_jwt=cookie-token' })),
    'cookie-token');
});

test('no credential at all is null, not empty string', () => {
  // The callers branch on truthiness; '' would be verified as a token and
  // answer "Invalid token" where the truth is "Missing token".
  assert.strictEqual(sc.tokenFromRequest(reqWith({})), null);
  assert.strictEqual(sc.tokenFromRequest(reqWith({ cookie: 'other=1' })), null);
});

// ── issuing ───────────────────────────────────────────────────────────────

function capture(fn, { secure = false } = {}) {
  const headers = {};
  const res = {
    getHeader: (k) => headers[k],
    setHeader: (k, v) => { headers[k] = v; },
  };
  const req = { secure, headers: {} };
  fn(req, res);
  return [].concat(headers['Set-Cookie'] || []);
}

test('a session issues one unreadable cookie and one readable flag', () => {
  const set = capture((req, res) => sc.setSession(req, res, 'tok'));
  assert.strictEqual(set.length, 2);
  const jwtCookie = set.find((c) => c.startsWith('rc_jwt='));
  const flag = set.find((c) => c.startsWith('rc_auth='));

  assert.ok(jwtCookie.includes('HttpOnly'),
    'the token must be unreadable to script — that IS the finding');
  assert.ok(jwtCookie.includes('tok'));

  // The flag exists so LOGGED_IN survives an unreadable token. It must carry
  // nothing else: forging it buys a UI shell whose every request 401s.
  assert.ok(!flag.includes('HttpOnly'), 'the page has to be able to read this one');
  assert.match(flag, /^rc_auth=1;/);
  assert.ok(!flag.includes('tok'), 'the flag must never carry the token');

  for (const c of set) assert.ok(c.includes('SameSite=Lax'), c);
  for (const c of set) assert.ok(c.includes('Path=/'), c);
});

test('Secure is set on https and withheld on plain http', () => {
  // Secure over http means the browser drops the cookie silently, which on a
  // dev box looks exactly like "login is broken" with nothing in any log.
  for (const c of capture((req, res) => sc.setSession(req, res, 't'), { secure: true })) {
    assert.ok(c.includes('Secure'), c);
  }
  for (const c of capture((req, res) => sc.setSession(req, res, 't'))) {
    assert.ok(!c.includes('Secure'), c);
  }
});

test('a proxied https request counts as secure', () => {
  const headers = {};
  const res = { getHeader: (k) => headers[k], setHeader: (k, v) => { headers[k] = v; } };
  sc.setSession({ secure: false, headers: { 'x-forwarded-proto': 'https,http' } },
    res, 't');
  for (const c of [].concat(headers['Set-Cookie'])) assert.ok(c.includes('Secure'), c);
});

test('clearing expires both halves', () => {
  const set = capture((req, res) => sc.clearSession(req, res));
  assert.strictEqual(set.length, 2);
  for (const c of set) {
    assert.match(c, /Max-Age=0/);
    assert.match(c, /Expires=Thu, 01 Jan 1970/);
  }
});

test('setting a session never drops a cookie another handler already set', () => {
  const headers = { 'Set-Cookie': 'unrelated=1' };
  const res = { getHeader: (k) => headers[k], setHeader: (k, v) => { headers[k] = v; } };
  sc.setSession({ secure: true, headers: {} }, res, 'tok');
  const set = [].concat(headers['Set-Cookie']);
  assert.strictEqual(set.length, 3);
  assert.ok(set.includes('unrelated=1'));
});

// ── end to end, through the real middleware ───────────────────────────────

function server() {
  const { authMiddleware, optionalAuth } = require('../auth');
  const app = express();
  app.get('/who', authMiddleware, (req, res) => res.json({ id: req.user.user_id }));
  app.get('/maybe', optionalAuth, (req, res) =>
    res.json({ id: (req.user && req.user.user_id) || null }));
  return http.createServer(app);
}

function get(srv, path, headers) {
  return new Promise((resolve, reject) => {
    http.get({ port: srv.address().port, path, headers }, (res) => {
      let b = '';
      res.on('data', (d) => { b += d; });
      res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(b || '{}') }));
    }).on('error', reject);
  });
}

test('a cookie-only request authenticates through authMiddleware', async () => {
  const srv = server();
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  try {
    const token = jwt.sign({ user_id: 4242, email: 'c@test.io', epoch: 0 },
      process.env.JWT_SECRET, { expiresIn: '1h' });

    const viaCookie = await get(srv, '/who', { Cookie: `rc_jwt=${token}` });
    assert.strictEqual(viaCookie.status, 200, 'the cookie was not accepted');
    assert.strictEqual(viaCookie.body.id, 4242);

    // …and the header path is untouched.
    const viaHeader = await get(srv, '/who', { Authorization: `Bearer ${token}` });
    assert.strictEqual(viaHeader.status, 200);

    // No credential is still 401 with the same message as before.
    const none = await get(srv, '/who', {});
    assert.strictEqual(none.status, 401);
    assert.strictEqual(none.body.error, 'Missing token');

    // A garbage cookie is an invalid token, not an anonymous caller.
    const junk = await get(srv, '/who', { Cookie: 'rc_jwt=not-a-jwt' });
    assert.strictEqual(junk.status, 401);
    assert.strictEqual(junk.body.error, 'Invalid token');

    // optionalAuth degrades rather than refusing, from the cookie too.
    assert.strictEqual((await get(srv, '/maybe', { Cookie: `rc_jwt=${token}` })).body.id, 4242);
    assert.strictEqual((await get(srv, '/maybe', { Cookie: 'rc_jwt=junk' })).body.id, null);
    assert.strictEqual((await get(srv, '/maybe', {})).body.id, null);
  } finally {
    srv.close();
  }
});

// ── the wiring that makes it reachable ────────────────────────────────────

test('every session funnel issues the cookie, and logout clears it', () => {
  const src = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'auth.js'), 'utf8');
  // One funnel, for the same reason sessionResponse is one: a cookie set at
  // five of six mint sites is a login that works everywhere except the path
  // nobody tested.
  assert.match(src, /async function sendSession\(req, res, user/);
  assert.ok(!/res\.json\(await sessionResponse\(/.test(src),
    'a mint site still answers without setting the cookie');
  assert.match(src, /clearSession\(req, res\);/);
  // The OAuth callback redirects rather than returning JSON, so it sets the
  // cookie explicitly — and is the easiest one to forget.
  assert.match(src, /setSession\(req, res, body\.token\);/);
});

// ── the regression that would have shipped ────────────────────────────────

test('a Bearer of the literal string "null" does not shadow the cookie', () => {
  // Eleven call sites across seven pages build the header as
  // `'Bearer ' + tok`. Once the token leaves localStorage, `tok` is null and
  // they send the four characters `null` — truthy, accepted as the
  // credential, and it hid a perfectly good cookie underneath. Every one of
  // those pages would have 401'd for exactly the sessions this migration
  // creates.
  for (const junk of ['null', 'undefined', 'false', '']) {
    assert.strictEqual(
      sc.tokenFromRequest(reqWith({ authorization: `Bearer ${junk}`,
                                    cookie: 'rc_jwt=good' })),
      'good', `Bearer ${junk} shadowed the cookie`);
  }
});

test('a real but invalid token is still an invalid token', () => {
  // The fall-through above must not swallow genuine garbage: "we could not
  // read your credential" and "you sent none" are different answers, and the
  // 401 body says so.
  assert.strictEqual(
    sc.tokenFromRequest(reqWith({ authorization: 'Bearer abc.def.ghi' })),
    'abc.def.ghi');
});

test('no page writes a session token to localStorage any more', () => {
  // The finding in one line. A readable copy anywhere hands the session back
  // to any script that runs, which is what the HttpOnly cookie exists to stop.
  const fs = require('node:fs');
  const path = require('node:path');
  const dir = path.join(__dirname, '..', 'public');
  const offenders = [];
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const full = path.join(d, e.name);
      if (e.isDirectory()) { walk(full); continue; }
      if (!/\.(html|js)$/.test(e.name)) continue;
      const src = fs.readFileSync(full, 'utf8')
        .replace(/<!--[\s\S]*?-->/g, ' ')
        .replace(/\/\*[\s\S]*?\*\//g, ' ')
        .replace(/^[ \t]*\/\/[^\n]*$/gm, '');
      // Storing a user_id is fine — it names nobody's session and unlocks
      // nothing. Storing a `token` is the defect.
      for (const m of src.matchAll(/localStorage\.setItem\(\s*['"]([\w.]+)['"]\s*,([^;]*)/g)) {
        const [, key, value] = m;
        if (/token/i.test(key) || /\btoken\b/.test(value)) {
          offenders.push(`${path.relative(dir, full)} → ${key}`);
        }
      }
    }
  };
  walk(dir);
  assert.deepStrictEqual(offenders, [],
    'a session token is being written somewhere a script can read it back');
});
