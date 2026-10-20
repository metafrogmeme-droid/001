'use strict';
/**
 * The Pi Browser app in Chromium: the stand-in SDK, our API's fixtures, and
 * the steps every Pi smoke test takes (open, sign in, change tab).
 *
 * Shared by the_pi_app_signs_in_with_pi.smoke.test.js and
 * the_pi_app_says_what_just_happened.smoke.test.js, so the two drive the same
 * page through the same stand-ins: a second hand-written copy would forget
 * the next route the page reads. `context(browser, { routes })` lets a test
 * answer the routes it is about before the shared fixtures do.
 */
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const PUBLIC = path.join(__dirname, '..', '..', 'public');

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
  app.use('/pi', require('../../routes/piapp'));
  app.use('/miniapp', require('../../routes/miniapp'));
  app.use('/doge', require('../../routes/dogeapp'));
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
/** As many hourly bars as the page asked for (the relay's ceiling is 200). */
function candles(limit) {
  const n = Math.min(200, Math.max(1, Number(limit) || 60));
  return { code: '00000', data: Array.from({ length: n }, (_, i) => {
    const ts = NOW - (n - i) * 3600000;
    const o = 64000 + i * 15;
    return [String(ts), String(o), String(o + 80), String(o - 60), String(o + 20), '10', '650000'];
  }) };
}
const ORDER = { orderId: 'order-from-server', amount: 3, memo: 'Support RUNECLAW', metadata: { orderId: 'order-from-server', kind: 'tip' } };
// Today's duel card as /api/duel/today sends it: one round of each category,
// the classic one uncalled, and the record as /api/duel/me sends it.
const DUEL_CARD = { day: '2026-10-19', horizon_hours: 24, open: 4, counts_only: true, rounds: [
  { id: 11, day: '2026-10-19', idx: 0, kind: 'direction', symbol: 'BTCUSDT', options: ['long', 'short', 'pass'], callable: true, seal: 'a'.repeat(64) },
  { id: 12, day: '2026-10-19', idx: 3, kind: 'backfade', symbol: 'SOLUSDT', options: ['back', 'fade', 'pass'], callable: true, agent_direction: 'short' },
  { id: 13, day: '2026-10-19', idx: 4, kind: 'range', symbol: 'BNBUSDT', options: ['break', 'hold', 'pass'], callable: true, threshold_pct: 2 },
  { id: 14, day: '2026-10-19', idx: 5, kind: 'mover', symbol: 'XRPUSDT', symbol_b: 'ARBUSDT', options: ['first', 'second', 'pass'], callable: true },
  { id: 15, day: '2026-10-19', idx: 6, kind: 'sports', symbol: 'FD:441', options: ['home', 'draw', 'away', 'pass'], callable: true,
    match: { home: 'Arsenal FC', away: 'Chelsea FC', home_short: 'Arsenal', away_short: 'Chelsea', competition: 'Premier League', kickoff: '2026-10-19T19:00:00.000Z' } },
], sources: { sports: 'dealt' } };
const DUEL_ME = { handle: null, window_days: 90, accuracy: { pct: null, correct: 0, wrong: 0, push: 0, scored: 0, unresolved: 2, resolved: 0 },
  marks: { marks: 0, pending: 2, beat_agent: 0 }, streak: { current: 2 }, quests: [], history: [], counts_only: true, private: true };
const DUEL_PICKED = { ok: true, round_id: 13, kind: 'range', symbol: 'BNBUSDT', pick: 'hold', entry_price: 600, threshold_pct: 2,
  resolves_at: '2026-10-20T12:00:00.000Z', seal: 'a'.repeat(64) };

async function context(browser, {
  sdk = true, signin = { status: 200, body: { token: 'session-jwt-1', handle: null } },
  tips = TIPS, unfinished = null, cancel = false, signals = null,
  account = ACCOUNT, close = null, openSignal = null,
  duel = { status: 200, body: DUEL_CARD }, duelMe = { status: 200, body: DUEL_ME }, duelPick = null,
  routes = null, locale = null,
} = {}) {
  // `locale` is the browser's language: Playwright sends it as Accept-Language.
  const ctx = await browser.newContext({ viewport: { width: 390, height: 800 }, ...(locale ? { locale } : {}) });
  await ctx.addInitScript(({ u, c }) => { window.__PI_UNFINISHED__ = u; window.__PI_CANCEL__ = c; },
    { u: unfinished, c: cancel });
  const sent = [];
  await ctx.route('https://sdk.minepi.com/**', (route) => (sdk
    ? route.fulfill({ status: 200, contentType: 'application/javascript', body: PI_STUB })
    : route.abort()));
  await ctx.route('**/api/**', (route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    sent.push({ path: p, query: new URL(req.url()).search, method: req.method(), auth: req.headers().authorization || '', body: req.postData() });
    // A test's own answers first, for the routes it is about.
    if (routes && typeof routes[p] === 'function') return routes[p](route, req);
    if (p === '/api/pi/signin') return route.fulfill({ status: signin.status, contentType: 'application/json', body: JSON.stringify(signin.body) });
    if (p === '/api/arena/season') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SEASON) });
    if (p === '/api/signals' && signals) {
      return route.fulfill({ status: signals.status, contentType: 'application/json', body: JSON.stringify(signals.body) });
    }
    if (p.startsWith('/api/market/candles/')) {
      const limit = new URL(req.url()).searchParams.get('limit');
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(limit ? candles(limit) : CANDLES) });
    }
    if (p === '/api/duel/today') return route.fulfill({ status: duel.status, contentType: 'application/json', body: JSON.stringify(duel.body) });
    if (p === '/api/duel/me') return route.fulfill({ status: duelMe.status, contentType: 'application/json', body: JSON.stringify(duelMe.body) });
    if (p === '/api/duel/pick') {
      return route.fulfill(duelPick || { status: 200, contentType: 'application/json', body: JSON.stringify(DUEL_PICKED) });
    }
    if (p === '/api/arena/open-signal' && openSignal) return route.fulfill(openSignal);
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
      return route.fulfill({ status: ok ? 200 : 401, contentType: 'application/json', body: JSON.stringify(ok ? account : { error: 'unauthorized' }) });
    }
    if (p === '/api/arena/close' && close) return route.fulfill(close);
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

/**
 * The signed-in screen is one tab at a time behind a bottom bar; open one.
 * About is not in the bar: the header's button opens it.
 */
async function showTab(page, name) {
  await page.click('.p-tab-b[data-tab="' + name + '"], .p-off-b[data-tab="' + name + '"]');
  await page.waitForSelector('.p-panel[data-tab="' + name + '"]:not([hidden])');
}

/**
 * Wait for the n-th request to `path` to be recorded, or fail with `why`.
 *
 * `sent` is filled by the page's request event, which lands a few
 * milliseconds after the page issued the call. A block that shows its toast
 * BEFORE it re-reads (the duel's pick handler does, on purpose: the person
 * sees "Call recorded" without waiting on the next read) can therefore be
 * observed with the toast up and the read not yet recorded, and a snapshot
 * assertion taken at that instant said "the card was not read again" about
 * a read that arrived 20 ms later. It did, once, on a loaded box. The
 * other read-again checks in this file wait for a state the page reaches
 * only after its reads (the Refresh button re-enabling); this one has no
 * such state to wait on, so it waits on the record itself.
 */
async function readAgain(sent, path, n, why, ms = 5000) {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (sent.filter((s) => s.path === path).length >= n) return;
    await new Promise((r) => setTimeout(r, 25));
  }
  assert.fail(why);
}

module.exports = {
  pw, CHROMIUM, SKIP, PI_STUB, SEASON, ACCOUNT, TIPS, NOW, LIVE_CALL, LOCKED_CALL, CANDLES, candles, ORDER,
  DUEL_CARD, DUEL_ME, DUEL_PICKED, serve, context, open, signInAndSettle, showTab, readAgain,
};
