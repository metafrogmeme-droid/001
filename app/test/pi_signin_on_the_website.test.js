'use strict';
/**
 * "Sign in with Pi" on the website: Pi's implicit OAuth flow (lib/pi_signin.js).
 *
 * The flow, as Pi's Developer Portal issues it (2026-10-17): a client id and
 * no secret; Pi returns an access token in the redirect URI's fragment; the
 * site asks Pi's /v2/me who the token belongs to. What these tests hold:
 *
 *   The identity is Pi's answer about the token, never the request's. A uid
 *   in the body is ignored; a token Pi refuses signs no one in.
 *
 *   The state is this server's, single use, and Pi's. A state nobody issued,
 *   one already spent, or one issued for another provider is refused before
 *   Pi is asked anything.
 *
 *   Pi down is not the person refused. 503, never 401.
 *
 *   The token reaches no log line and no response.
 *
 *   The door is shut until it is configured: no button, a 503 at /start.
 *
 * A stateful stand-in for Pi's /v2/me answers from a table of live tokens.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
process.env.APP_BASE_URL = 'https://app.test';
process.env.PI_OAUTH_CLIENT_ID = 'pi-client-abc';
process.env.DISCORD_CLIENT_ID = 'disc-client';
process.env.DISCORD_CLIENT_SECRET = 'disc-secret';

const test = require('node:test');
const assert = require('node:assert');
const { navigationTarget } = require('./helpers/navigation');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const { codeOnly } = require('./helpers/code_only');

const piSignin = require('../lib/pi_signin');
const callbackPage = require('../routes/pi_signin_page');
const { router: authRouter } = require('../auth');

const APP = path.join(__dirname, '..');

// ── a stand-in for Pi's /v2/me ────────────────────────────────────────────

const pi = {
  tokens: new Map(),   // live token -> uid
  down: false,
  calls: [],
};
function fakePiFetch(url, opts) {
  pi.calls.push({ url, auth: opts && opts.headers && opts.headers.Authorization });
  if (pi.down) return Promise.reject(new TypeError('fetch failed'));
  assert.strictEqual(url, 'https://api.minepi.test/v2/me');
  const m = /^Bearer (.+)$/.exec((opts.headers || {}).Authorization || '');
  const uid = m && pi.tokens.get(m[1]);
  if (!uid) return Promise.resolve(new Response('{"error":"invalid_token"}', { status: 401 }));
  return Promise.resolve(new Response(JSON.stringify({ uid, username: 'pioneer_' + uid }), {
    status: 200, headers: { 'content-type': 'application/json' } }));
}
authRouter._piSigninDeps.fetch = fakePiFetch;
authRouter._piSigninDeps.apiBase = 'https://api.minepi.test/v2';

// ── the auth router, as server.js mounts it ───────────────────────────────

let server;
let base;
test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authRouter);
  app.get('/auth/pi/callback', callbackPage);
  await new Promise((resolve) => { server = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

/** /start, and the state it issued (read from the page that sends the browser to Pi). */
async function start(linkKey) {
  const r = await fetch(base + '/api/auth/oauth/pi/start' + (linkKey ? `?link=${linkKey}` : ''), { redirect: 'manual' });
  return new URL(await navigationTarget(r));
}

async function token(body) {
  const r = await fetch(base + '/api/auth/oauth/pi/token', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return { status: r.status, cookie: r.headers.get('set-cookie'), body: await r.json() };
}

const payloadOf = (b) => JSON.parse(Buffer.from(b.oauth, 'base64').toString());
let n = 0;
/** A token Pi will vouch for, as `uid`. */
function piToken(uid) {
  const t = `piTok_${uid}_${++n}_${'x'.repeat(24)}`;
  pi.tokens.set(t, uid);
  return t;
}

// ── the library ───────────────────────────────────────────────────────────

test('the authorize URL is Pi\'s implicit flow, with our client, our callback page and the least scope', () => {
  const u = new URL(piSignin.authorizeUrl({ state: 'st-1' }));
  assert.strictEqual(u.origin + u.pathname, 'https://accounts.pinet.com/oauth/authorize');
  assert.deepStrictEqual(Object.fromEntries(u.searchParams), {
    response_type: 'token', client_id: 'pi-client-abc',
    redirect_uri: 'https://app.test/auth/pi/callback', scope: 'username', state: 'st-1',
  });
  assert.strictEqual(piSignin.redirectUri({ APP_BASE_URL: 'https://www.humanoid-traders.com/' }),
    'https://www.humanoid-traders.com/auth/pi/callback', 'the URI to register in the Developer Portal');
});

test('configured only with a client id', () => {
  assert.strictEqual(piSignin.configured({ PI_OAUTH_CLIENT_ID: '  ' }), false);
  assert.strictEqual(piSignin.configured({}), false);
  assert.strictEqual(piSignin.configured({ PI_OAUTH_CLIENT_ID: 'abc' }), true);
});

test('identify: Pi\'s three answers, and a malformed token never leaves the server', async () => {
  const deps = { fetch: fakePiFetch, apiBase: 'https://api.minepi.test/v2' };
  const t = piToken('uid-lib');
  assert.deepStrictEqual(await piSignin.identify(t, deps), { ok: true, uid: 'uid-lib' });
  assert.strictEqual(pi.calls[pi.calls.length - 1].auth, `Bearer ${t}`);
  assert.deepStrictEqual(await piSignin.identify('piTok_nobody_knows_this_one', deps),
    { ok: false, reason: 'token_rejected' });
  const before = pi.calls.length;
  assert.deepStrictEqual(await piSignin.identify('bad token with spaces', deps),
    { ok: false, reason: 'malformed_token' });
  assert.strictEqual(pi.calls.length, before, 'a malformed token is not sent to Pi');
  pi.down = true;
  await assert.rejects(piSignin.identify(t, deps));
  pi.down = false;
  const noUid = { fetch: async () => new Response('{"username":"x"}', { status: 200 }), apiBase: 'x' };
  await assert.rejects(piSignin.identify(t, noUid), /usable uid/, 'a 200 with no uid is unavailable, not a person');
  const err500 = { fetch: async () => new Response('', { status: 500 }), apiBase: 'x' };
  await assert.rejects(piSignin.identify(t, err500), /500/);
});

// ── the door ──────────────────────────────────────────────────────────────

test('the login page offers Pi only when it is configured, and /start is shut without it', async () => {
  const on = await fetch(base + '/api/auth/config').then((r) => r.json());
  assert.ok(on.oauth_providers.includes('pi'), JSON.stringify(on.oauth_providers));
  const saved = process.env.PI_OAUTH_CLIENT_ID;
  delete process.env.PI_OAUTH_CLIENT_ID;
  try {
    const off = await fetch(base + '/api/auth/config').then((r) => r.json());
    assert.ok(!off.oauth_providers.includes('pi'));
    assert.strictEqual((await fetch(base + '/api/auth/oauth/pi/start', { redirect: 'manual' })).status, 503);
    assert.strictEqual((await token({ access_token: piToken('u'), state: 'x' })).status, 503);
  } finally {
    process.env.PI_OAUTH_CLIENT_ID = saved;
  }
  const page = fs.readFileSync(path.join(APP, 'public', 'index.html'), 'utf8');
  assert.match(page, /pi:\{label:'Continue with Pi'/, 'the button the config advertises has a label');
});

// ── signing in ────────────────────────────────────────────────────────────

test('a token Pi vouches for signs in the account Pi names, and the same uid is the same account', async () => {
  const loc = await start();
  const first = await token({ access_token: piToken('uid-alice'), state: loc.searchParams.get('state') });
  assert.strictEqual(first.status, 200, JSON.stringify(first.body));
  const p1 = payloadOf(first.body);
  assert.ok(p1.token);
  assert.strictEqual(p1.provider, 'pi');
  assert.strictEqual(p1.linked, false);
  assert.match(first.cookie || '', /rc_/, 'the session cookie is set on this response');
  assert.match(p1.email, /^pi-uid-alice@pi\.runeclaw\.local$/, 'the placeholder every Pi account gets');

  const again = await token({ access_token: piToken('uid-alice'), state: (await start()).searchParams.get('state') });
  assert.strictEqual(payloadOf(again.body).user_id, p1.user_id, 'one Pi uid, one account');
  const { pool } = require('../db');
  const [rows] = await pool.execute('SELECT id FROM users WHERE pi_uid = ?', ['uid-alice']);
  assert.strictEqual(rows.length, 1);
});

test('a uid in the request is ignored: the account is the one Pi names', async () => {
  const state = (await start()).searchParams.get('state');
  const r = await token({ access_token: piToken('uid-real'), state, uid: 'uid-claimed', user_id: 1 });
  assert.strictEqual(r.status, 200);
  const { pool } = require('../db');
  const [claimed] = await pool.execute('SELECT id FROM users WHERE pi_uid = ?', ['uid-claimed']);
  assert.strictEqual(claimed.length, 0);
  const [real] = await pool.execute('SELECT id FROM users WHERE pi_uid = ?', ['uid-real']);
  assert.strictEqual(real.length, 1);
  assert.strictEqual(payloadOf(r.body).user_id, real[0].id);
});

test('a token Pi refuses is a 401 that creates nothing', async () => {
  const state = (await start()).searchParams.get('state');
  const r = await token({ access_token: 'piTok_forged_by_someone_long_enough', state });
  assert.strictEqual(r.status, 401);
  assert.deepStrictEqual(r.body, { error: 'pi_rejected' });
  assert.strictEqual(r.cookie, null, 'no session for a refused token');
});

test('Pi unreachable is a 503, never a refusal of the person', async () => {
  const state = (await start()).searchParams.get('state');
  pi.down = true;
  const logged = [];
  const orig = console.error;
  console.error = (...a) => logged.push(a.join(' '));
  let r;
  try {
    r = await token({ access_token: piToken('uid-patient'), state });
  } finally {
    console.error = orig;
    pi.down = false;
  }
  assert.strictEqual(r.status, 503);
  assert.deepStrictEqual(r.body, { error: 'pi_unavailable' });
  assert.strictEqual(r.cookie, null);
  assert.ok(logged.some((l) => /Pi sign-in unavailable/.test(l)));
});

// ── the state ─────────────────────────────────────────────────────────────

test('the state must be one this server issued, for Pi, and is good once', async () => {
  const t = piToken('uid-state');
  const callsBefore = pi.calls.length;
  assert.strictEqual((await token({ access_token: t, state: 'never-issued' })).status, 400);
  assert.strictEqual((await token({ access_token: t })).status, 400, 'no state');
  assert.strictEqual((await token({ state: 'x' })).status, 400, 'no token');
  // A state issued for Discord's code flow is not a Pi state.
  const disc = await fetch(base + '/api/auth/oauth/discord/start', { redirect: 'manual' });
  const discState = new URL(await navigationTarget(disc)).searchParams.get('state');
  assert.strictEqual((await token({ access_token: t, state: discState })).status, 400);
  assert.strictEqual(pi.calls.length, callsBefore, 'Pi is not asked about a state we did not issue');

  const state = (await start()).searchParams.get('state');
  assert.strictEqual((await token({ access_token: t, state })).status, 200);
  const replay = await token({ access_token: piToken('uid-state'), state });
  assert.strictEqual(replay.status, 400, 'a spent state signs no one in');
  assert.deepStrictEqual(replay.body, { error: 'invalid or expired login attempt' });
});

// ── linking to a signed-in account ────────────────────────────────────────

test('link mode attaches the Pi uid to the account that asked, and creates no other', async () => {
  const reg = await fetch(base + '/api/auth/register', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'pilinker@example.com', password: 'longenough1' }),
  }).then((r) => r.json());
  const lk = await fetch(base + '/api/auth/oauth-link-token', {
    method: 'POST', headers: { Authorization: `Bearer ${reg.token}`, 'Content-Type': 'application/json' }, body: '{}',
  }).then((r) => r.json());
  const state = (await start(lk.link_key)).searchParams.get('state');
  const r = await token({ access_token: piToken('uid-linked'), state });
  assert.strictEqual(r.status, 200);
  const p = payloadOf(r.body);
  assert.strictEqual(p.linked, true);
  assert.strictEqual(p.user_id, reg.user_id);
  assert.strictEqual(p.email, 'pilinker@example.com');
  const { pool } = require('../db');
  const [rows] = await pool.execute('SELECT id FROM users WHERE pi_uid = ?', ['uid-linked']);
  assert.deepStrictEqual(rows.map((x) => x.id), [reg.user_id]);
});

// ── the token stays out of logs and answers ───────────────────────────────

test('the access token reaches no log line and no response', async () => {
  const lines = [];
  const saved = { log: console.log, error: console.error, warn: console.warn };
  for (const k of Object.keys(saved)) console[k] = (...a) => lines.push(a.map(String).join(' '));
  const seen = [];
  try {
    for (const [uid, mode] of [['uid-log-ok', 'ok'], ['x', 'refused'], ['uid-log-down', 'down']]) {
      const state = (await start()).searchParams.get('state');
      const t = mode === 'refused' ? 'piTok_refused_and_secret_value_123' : piToken(uid);
      if (mode === 'down') pi.down = true;
      const r = await token({ access_token: t, state });
      pi.down = false;
      seen.push([t, JSON.stringify(r.body)]);
    }
  } finally {
    Object.assign(console, saved);
    pi.down = false;
  }
  for (const [t, body] of seen) {
    assert.ok(!lines.some((l) => l.includes(t)), 'the token was logged');
    assert.ok(!body.includes(t) && !Buffer.from(body).toString().includes(t), 'the token was answered');
  }
});

// ── the callback page ─────────────────────────────────────────────────────

test('the redirect URI is a page with one script of ours, uncached, sending no referrer', async () => {
  const r = await fetch(base + '/auth/pi/callback');
  assert.strictEqual(r.status, 200);
  assert.match(r.headers.get('cache-control'), /no-store/);
  assert.strictEqual(r.headers.get('referrer-policy'), 'no-referrer');
  const html = await r.text();
  const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)];
  assert.deepStrictEqual(scripts.map((m) => (m[1].match(/src="([^"]+)"/) || [])[1]), [callbackPage.CALLBACK_SCRIPT]);
  assert.ok(scripts.every((m) => m[2].trim() === ''), 'no inline script');
  const file = path.join(APP, 'public', callbackPage.CALLBACK_SCRIPT.replace(/\?.*$/, ''));
  assert.ok(fs.existsSync(file), file);
  const server = codeOnly(fs.readFileSync(path.join(APP, 'server.js'), 'utf8'));
  assert.match(server, /app\.get\('\/auth\/pi\/callback', require\('\.\/routes\/pi_signin_page'\)\)/);
  assert.strictEqual(new URL(piSignin.redirectUri()).pathname, '/auth/pi/callback', 'the page is at the URI Pi is sent to');
});

test('the callback script clears the fragment first and copies no text from the URL into a message', () => {
  const src = codeOnly(fs.readFileSync(path.join(APP, 'public', 'js', 'pi-signin-callback.js'), 'utf8'));
  const cleared = src.indexOf('history.replaceState(null, \'\', location.pathname)');
  assert.ok(cleared > 0 && cleared < src.indexOf('fetch('), 'the token leaves the address bar before anything else runs');
  assert.match(src, /'\/api\/auth\/oauth\/pi\/token'/);
  assert.doesNotMatch(src, /fail\([^)]*error\s*\+|fail\(\s*error\s*\)/, 'Pi\'s error code is mapped to our sentence, not echoed');
  assert.doesNotMatch(src, /localStorage|sessionStorage|document\.cookie/);
});
