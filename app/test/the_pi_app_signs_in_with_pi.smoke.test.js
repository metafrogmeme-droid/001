'use strict';
/**
 * The Pi Browser app, driven in Chromium: sign in with Pi, see the arena.
 *
 * Pi's SDK is the one thing a test cannot load (it lives on sdk.minepi.com and
 * talks to the Pi Browser), so its script is answered by a stand-in exposing
 * the two calls the page makes, Pi.init and Pi.authenticate, and recording
 * what it was asked. Our own API is answered by fixtures that record what the
 * page sent. Everything the page itself does is real: the router's HTML, the
 * scripts it loads, and the shared session (arena-session.js).
 *
 * Every outcome is a different sentence, and each is asserted: signed in, Pi
 * refusing the token, Pi unreachable, and no Pi Browser at all. The Farcaster
 * Mini App, which shares the session, is rendered too, so the refactor that
 * made it shared cannot break it unseen.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const PUBLIC = path.join(__dirname, '..', 'public');

function findChromium() {
  const cands = [process.env.RC_SMOKE_CHROMIUM].filter(Boolean);
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try { for (const d of fs.readdirSync(root)) if (/^chromium-\d+$/.test(d)) cands.push(path.join(root, d, 'chrome-linux', 'chrome')); } catch (e) { /* none */ }
  return cands.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }) || null;
}
let pw = null; try { pw = require('playwright-core'); } catch (e) { pw = null; }
const CHROMIUM = findChromium();
const SKIP = !pw ? 'playwright-core is not installed' : !CHROMIUM ? 'no Chromium binary found' : null;

const PI_STUB = `
window.__pi = { inits: [], scopes: null, initDone: false, authBeforeInit: false };
window.Pi = {
  // Resolves a beat later, as the real one can: authenticate must wait for it.
  init: function (o) {
    window.__pi.inits.push(o);
    return new Promise(function (r) { setTimeout(function () { window.__pi.initDone = true; r(); }, 50); });
  },
  authenticate: function (scopes, onIncomplete) {
    if (!window.__pi.initDone) window.__pi.authBeforeInit = true;
    window.__pi.scopes = scopes;
    window.__pi.hasCallback = typeof onIncomplete === 'function';
    return Promise.resolve({ accessToken: 'piAccessToken_fromTheStandInSdk123', user: { uid: 'uid-the-page-claims', username: 'pioneer' } });
  }
};`;

const SEASON = { season: { name: 'Season Test', starts_at: '2026-10-01T00:00:00Z', ends_at: '2026-12-01T00:00:00Z', live: true, rows: [] } };
const ACCOUNT = { equity: 10000, balance: 10000, return_pct: 0, limits: { min_margin: 10, max_leverage: 20 }, positions: [] };

async function serve() {
  delete process.env.DATABASE_URL;
  process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
  const app = express();
  // server.js's order: static files first (public/pi/ holds App Studio's
  // validation file, so a bare /pi is redirected to /pi/), then the routers.
  app.use(express.static(PUBLIC));
  app.use('/pi', require('../routes/piapp'));
  app.use('/miniapp', require('../routes/miniapp'));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function context(browser, { sdk = true, signin = { status: 200, body: { token: 'session-jwt-1', handle: null } } } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 800 } });
  const sent = [];
  await ctx.route('https://sdk.minepi.com/**', (route) => (sdk
    ? route.fulfill({ status: 200, contentType: 'application/javascript', body: PI_STUB })
    : route.abort()));
  await ctx.route('**/api/**', (route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    sent.push({ path: p, method: req.method(), auth: req.headers().authorization || '', body: req.postData() });
    if (p === '/api/pi/signin') return route.fulfill({ status: signin.status, contentType: 'application/json', body: JSON.stringify(signin.body) });
    if (p === '/api/arena/season') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SEASON) });
    if (p === '/api/arena/account') {
      const ok = req.headers().authorization === 'Bearer session-jwt-1';
      return route.fulfill({ status: ok ? 200 : 401, contentType: 'application/json', body: JSON.stringify(ok ? ACCOUNT : { error: 'unauthorized' }) });
    }
    return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
  });
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1|sdk\.minepi\.com)/, (route) => route.abort());
  return { ctx, sent };
}

async function open(ctx, url) {
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(url, { waitUntil: 'load' });
  await page.waitForSelector('.m-signin');
  return { page, errors };
}

async function signInAndSettle(page) {
  await page.click('.m-signin');
  await page.waitForFunction(() => !document.querySelector('.e-state--load'));
  await page.waitForTimeout(200);
  return page.textContent('#root');
}

test('the Pi app signs in with Pi and shows the arena, and says why when it cannot', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });
  try {
    await t.test('signed out: the gate names Pi, and there is no presale anywhere on it', async () => {
      const { ctx } = await context(browser);
      const { page, errors } = await open(ctx, `${base}/pi`);
      assert.match(await page.textContent('.m-signin'), /Sign in with Pi/);
      assert.strictEqual(await page.$('#presale-bar'), null);
      assert.doesNotMatch(await page.textContent('body'), /presale|RCLAW|smithii/i);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('signing in sends Pi\'s token, not a uid, and the arena opens on the session', async () => {
      const { ctx, sent } = await context(browser);
      const { page, errors } = await open(ctx, `${base}/pi`);
      const text = await signInAndSettle(page);
      const signin = sent.find((s) => s.path === '/api/pi/signin');
      assert.ok(signin, 'the page never asked our server');
      assert.deepStrictEqual(JSON.parse(signin.body), { accessToken: 'piAccessToken_fromTheStandInSdk123' });
      const acct = sent.find((s) => s.path === '/api/arena/account');
      assert.strictEqual(acct && acct.auth, 'Bearer session-jwt-1');
      assert.match(text, /No leaderboard handle yet/);
      const pi = await page.evaluate(() => window.__pi);
      assert.deepStrictEqual(pi.inits, [{ version: '2.0' }], 'init got more than its version');
      assert.strictEqual(pi.authBeforeInit, false, 'authenticate ran before init had finished');
      assert.deepStrictEqual(pi.scopes, ['username'], 'more was asked of the person than the app uses');
      assert.strictEqual(pi.hasCallback, true);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('a token Pi refused is named as that', async () => {
      const { ctx } = await context(browser, { signin: { status: 401, body: { error: 'pi_rejected', reason: 'token_rejected' } } });
      const { page } = await open(ctx, `${base}/pi`);
      assert.match(await signInAndSettle(page), /Pi did not confirm that sign-in/);
      await ctx.close();
    });

    await t.test('Pi unreachable is ours, not the person\'s', async () => {
      const { ctx } = await context(browser, { signin: { status: 503, body: { error: 'verifier_unavailable' } } });
      const { page } = await open(ctx, `${base}/pi`);
      assert.match(await signInAndSettle(page), /unavailable right now — this is on our side/);
      await ctx.close();
    });

    await t.test('outside the Pi Browser, it says where to open it', async () => {
      const { ctx, sent } = await context(browser, { sdk: false });
      const { page } = await open(ctx, `${base}/pi`);
      assert.match(await signInAndSettle(page), /Open this in the Pi Browser to sign in/);
      assert.ok(!sent.some((s) => s.path === '/api/pi/signin'), 'a sign-in was attempted with no SDK');
      await ctx.close();
    });

    await t.test('the Farcaster Mini App, which shares the session, still renders its own gate', async () => {
      const { ctx } = await context(browser);
      const { page, errors } = await open(ctx, `${base}/miniapp/arena`);
      assert.match(await page.textContent('.m-signin'), /Sign in with Farcaster/);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});
