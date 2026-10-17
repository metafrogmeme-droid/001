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
window.__pi = { inits: [], scopes: null, initDone: false, authBeforeInit: false, payments: [] };
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
    // A payment left unfinished last time, reported during sign-in as Pi does.
    if (window.__PI_UNFINISHED__) onIncomplete({ identifier: window.__PI_UNFINISHED__, amount: 999, user_uid: 'uid-the-page-claims' });
    return Promise.resolve({ accessToken: 'piAccessToken_fromTheStandInSdk123', user: { uid: 'uid-the-page-claims', username: 'pioneer' } });
  },
  // Drives the callbacks in Pi's order: approval, then completion, or a cancel.
  createPayment: function (data, cb) {
    window.__pi.payments.push(data);
    setTimeout(function () {
      if (window.__PI_CANCEL__) return cb.onCancel('pay-123');
      cb.onReadyForServerApproval('pay-123');
      setTimeout(function () { cb.onReadyForServerCompletion('pay-123', 'abc123txid'); }, 30);
    }, 30);
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

const TIPS = { enabled: true, amounts: [1, 3, 5], memo: 'Support RUNECLAW' };
const NOW = Date.now();
const LIVE_CALL = {
  signal_key: 'k-live-1', symbol: 'BTC/USDT:USDT', direction: 'LONG', confidence: 0.71, score: 71,
  pattern: 'breakout', regime: 'trending', entry_price: 65000, stop_loss: 64000, take_profit: 67000,
  rr: 2, net_rr: 1.9, rr_fee_losing: 0, thesis: 'test', status: 'NEW', outcome: null,
  created_at: new Date(NOW - 5 * 60000).toISOString(), resolved_at: null, seal: null,
  expires_at: new Date(NOW + 3600000).toISOString(),
};
// What an anonymous viewer is sent for a live call (lib/signal_access.js).
// A signed-in Pi user never should be, but if one arrives it must not become
// embed-row.js's card, which links to the site.
const LOCKED_CALL = { signal_key: 'k-locked', symbol: 'ETH/USDT:USDT', status: 'NEW', locked: true, outcome: null,
  created_at: new Date(NOW - 60000).toISOString(), expires_at: new Date(NOW + 3600000).toISOString(), seal: null };
// Bitget's relay shape: rows at `data`, [ts, o, h, l, c, baseVol, quoteVol].
const CANDLES = { code: '00000', data: Array.from({ length: 60 }, (_, i) => {
  const ts = NOW - (60 - i) * 3600000;
  const o = 64000 + i * 15;
  return [String(ts), String(o), String(o + 80), String(o - 60), String(o + 20), '10', '650000'];
}) };
const ORDER = { orderId: 'order-from-server', amount: 3, memo: 'Support RUNECLAW', metadata: { orderId: 'order-from-server', kind: 'tip' } };

async function context(browser, {
  sdk = true, signin = { status: 200, body: { token: 'session-jwt-1', handle: null } },
  tips = TIPS, unfinished = null, cancel = false, signals = null,
} = {}) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 800 } });
  await ctx.addInitScript(({ u, c }) => { window.__PI_UNFINISHED__ = u; window.__PI_CANCEL__ = c; },
    { u: unfinished, c: cancel });
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
    if (p === '/api/signals' && signals) {
      return route.fulfill({ status: signals.status, contentType: 'application/json', body: JSON.stringify(signals.body) });
    }
    if (p.startsWith('/api/market/candles/')) {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CANDLES) });
    }
    if (p === '/api/arena/open-signal') {
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ ok: true, filled: { symbol: 'BTCUSDT', direction: 'LONG', entry: 65010, dropped: [] }, virtual: true }) });
    }
    if (p.startsWith('/api/pi/payments/')) {
      const ok = req.headers().authorization === 'Bearer session-jwt-1';
      if (!ok) return route.fulfill({ status: 401, contentType: 'application/json', body: '{"error":"unauthorized"}' });
      const reply = (b) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) });
      if (p === '/api/pi/payments/tips') return reply(req.method() === 'GET' ? tips : ORDER);
      if (p === '/api/pi/payments/approve') return reply({ ok: true, status: 'approved' });
      if (p === '/api/pi/payments/complete' || p === '/api/pi/payments/incomplete') return reply({ ok: true, status: 'completed' });
    }
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
      assert.deepStrictEqual(pi.scopes, ['username', 'payments'], 'more was asked of the person than the app uses');
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

    await t.test('a tip pays exactly the order the server issued, and is settled by id', async () => {
      const { ctx, sent } = await context(browser);
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await page.waitForSelector('.p-tip');
      const labels = await page.$$eval('.p-tip', (bs) => bs.map((b) => b.textContent));
      assert.deepStrictEqual(labels, ['1 π', '3 π', '5 π']);
      await page.click('.p-tip[data-tip="3"]');
      await page.waitForFunction(() => /your tip arrived/.test(document.querySelector('#root').textContent));
      const order = sent.find((s) => s.path === '/api/pi/payments/tips' && s.method === 'POST');
      assert.deepStrictEqual(JSON.parse(order.body), { amount: 3 });
      assert.strictEqual(order.auth, 'Bearer session-jwt-1');
      const pi = await page.evaluate(() => window.__pi);
      assert.deepStrictEqual(pi.payments, [{ amount: 3, memo: 'Support RUNECLAW', metadata: { orderId: 'order-from-server', kind: 'tip' } }],
        'createPayment got something other than the server\'s order');
      const approve = sent.find((s) => s.path === '/api/pi/payments/approve');
      assert.deepStrictEqual(JSON.parse(approve.body), { paymentId: 'pay-123' });
      const done = sent.find((s) => s.path === '/api/pi/payments/complete');
      assert.deepStrictEqual(JSON.parse(done.body), { paymentId: 'pay-123', txid: 'abc123txid' });
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('a payment left unfinished is finished after sign-in, by its id alone', async () => {
      const { ctx, sent } = await context(browser, { unfinished: 'pay-old' });
      const { page } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await page.waitForFunction(() => /earlier tip was completed/.test(document.querySelector('#root').textContent));
      const inc = sent.find((s) => s.path === '/api/pi/payments/incomplete');
      assert.deepStrictEqual(JSON.parse(inc.body), { paymentId: 'pay-old' }, 'only the id; the server reads the rest from Pi');
      assert.strictEqual(inc.auth, 'Bearer session-jwt-1');
      await ctx.close();
    });

    await t.test('a cancelled tip says nothing was paid, and tips switched off show nothing', async () => {
      const { ctx } = await context(browser, { cancel: true });
      const { page } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await page.waitForSelector('.p-tip');
      await page.click('.p-tip[data-tip="1"]');
      await page.waitForFunction(() => /Nothing was paid/.test(document.querySelector('#root').textContent));
      await ctx.close();

      const off = await context(browser, { tips: { enabled: false } });
      const o = await open(off.ctx, `${base}/pi`);
      const text = await signInAndSettle(o.page);
      await o.page.waitForTimeout(200);
      assert.strictEqual(await o.page.$('.p-tip'), null);
      assert.doesNotMatch(await o.page.textContent('#root'), /Support RUNECLAW|Tips could not/);
      assert.match(text, /No leaderboard handle yet/);
      await off.ctx.close();
    });

    await t.test('signed in, the engine\'s calls show with the site\'s chart, and one opens on paper', async () => {
      const { ctx, sent } = await context(browser, { signals: { status: 200, body: { signals: [LIVE_CALL, LOCKED_CALL] } } });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await page.waitForSelector('.p-signals .e-row');
      assert.match(await page.textContent('.p-signals .e-row--locked'), /Sign in again to see its side and levels/);
      const card = await page.textContent('.p-signals .e-row:not(.e-row--locked)');
      assert.match(card, /LONG/);
      assert.match(card, /BTC/);
      // The site's TradingView chart, drawn from our own candle relay.
      await page.waitForSelector('.p-signals .e-chart canvas', { timeout: 5000 });
      const sig = sent.find((s) => s.path === '/api/signals');
      assert.strictEqual(sig.auth, 'Bearer session-jwt-1', 'signals were read anonymously, so live calls would be locked');
      assert.ok(sent.some((s) => s.path.startsWith('/api/market/candles/BTCUSDT')), 'the chart asked about the wrong market');

      await page.click('.p-sig-open');
      await page.waitForFunction(() => /Opened BTCUSDT LONG at 65010/.test(document.querySelector('#root').textContent));
      const opened = sent.find((s) => s.path === '/api/arena/open-signal');
      assert.deepStrictEqual(JSON.parse(opened.body), { signal_key: 'k-live-1', margin: 200, leverage: 2 });
      assert.strictEqual(opened.auth, 'Bearer session-jwt-1');

      // Nothing on the screen links back to the site, which carries the presale.
      const own = await page.$$eval('#root a[href]', (as) => as.map((a) => a.href)
        .filter((h) => new URL(h, location.href).origin === location.origin));
      assert.deepStrictEqual(own, []);
      assert.doesNotMatch(await page.textContent('body'), /presale|RCLAW|smithii/i);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('signals that cannot be read say so, and none is not an error', async () => {
      const down = await context(browser, { signals: { status: 503, body: { error: 'signal_stream_unavailable' } } });
      const d = await open(down.ctx, `${base}/pi`);
      await signInAndSettle(d.page);
      await d.page.waitForFunction(() => /Signals could not be loaded/.test(document.querySelector('#root').textContent));
      await down.ctx.close();

      const none = await context(browser, { signals: { status: 200, body: { signals: [] } } });
      const n = await open(none.ctx, `${base}/pi`);
      await signInAndSettle(n.page);
      await n.page.waitForFunction(() => /No signals right now/.test(document.querySelector('#root').textContent));
      assert.doesNotMatch(await n.page.textContent('#root'), /could not be loaded/);
      await none.ctx.close();
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
