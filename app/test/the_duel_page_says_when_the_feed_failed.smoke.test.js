'use strict';
/**
 * The /duel page, driven in Chromium against the real page (public/duel.html)
 * with the API answered by fixtures, because the page's loader is inline and
 * a scan cannot see what it draws or what it gates on.
 *
 *   signed in by the session cookie alone   the card is drawn. The session is
 *                   an HttpOnly cookie (M14) and `rc_auth` is the readable flag
 *                   that one exists; a page gating on the legacy localStorage
 *                   token showed "Sign in" to every cookie session, which is
 *                   every session, one click from the Arena's own link;
 *   the feed failed   the football note is shown, the market rounds stand;
 *   no match today    the note is NOT shown: a readable feed with nothing on,
 *                   or no feed at all, is not a failure to report;
 *   signed out        the invitation, and no read of the card.
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

const MARKET = [
  { id: 11, kind: 'direction', symbol: 'BTCUSDT', options: ['long', 'short', 'pass'], callable: true },
  { id: 12, kind: 'direction', symbol: 'ETHUSDT', options: ['long', 'short', 'pass'], callable: true },
  { id: 13, kind: 'backfade', symbol: 'SOLUSDT', options: ['back', 'fade', 'pass'], callable: true, agent_direction: 'long' },
  { id: 14, kind: 'range', symbol: 'BNBUSDT', options: ['break', 'hold', 'pass'], callable: true, threshold_pct: 2 },
  { id: 15, kind: 'mover', symbol: 'XRPUSDT', symbol_b: 'ARBUSDT', options: ['first', 'second', 'pass'], callable: true },
];
const MATCH = { id: 16, kind: 'sports', symbol: 'FD:441', options: ['home', 'draw', 'away', 'pass'], callable: true,
  match: { home: 'Arsenal FC', away: 'Chelsea FC', home_short: 'Arsenal', away_short: 'Chelsea', competition: 'Premier League', kickoff: '2026-10-19T19:00:00.000Z' } };
const card = (rounds, sources) => Object.assign({ day: '2026-10-19', horizon_hours: 24, open: rounds.length, counts_only: true, rounds },
  sources === undefined ? {} : { sources });
const ME = { handle: null, window_days: 90, accuracy: { pct: null, correct: 0, wrong: 0, push: 0, scored: 0, unresolved: 0, resolved: 0 },
  marks: { marks: 0, pending: 0, beat_agent: 0 }, streak: { current: 0 }, quests: [], history: [], counts_only: true, private: true };

async function serve() {
  const app = express();
  app.get('/duel', (req, res) => res.sendFile(path.join(PUBLIC, 'duel.html')));
  app.use(express.static(PUBLIC));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function context(browser, base, { signedIn = true, today = card(MARKET.concat([MATCH]), { sports: 'dealt' }) } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1100, height: 900 } });
  if (signedIn) await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
  const sent = [];
  await ctx.route('**/api/**', (route) => {
    const p = new URL(route.request().url()).pathname;
    sent.push(p);
    const json = (status, body) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (p === '/api/duel/today') return json(signedIn ? 200 : 401, signedIn ? today : { error: 'unauthorized' });
    if (p === '/api/duel/me') return json(signedIn ? 200 : 401, signedIn ? ME : { error: 'unauthorized' });
    if (p === '/api/public/duel/board' || p === '/api/public/duel/squads') return json(200, { rows: [], warming_up: false });
    return json(200, { ok: true, data: {}, rows: [], items: [] });
  });
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
  return { ctx, sent };
}

async function open(ctx, base) {
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${base}/duel`, { waitUntil: 'load' });
  await page.waitForSelector('#rounds', { state: 'attached' });   // hidden until the card is drawn
  return { page, errors };
}

const hidden = (page, id) => page.$eval('#' + id, (e) => e.hidden);

test('the /duel page', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });
  try {
    await t.test('signed in by the session cookie alone: the card is drawn, the match on it, no note', async () => {
      const { ctx, sent } = await context(browser, base);
      const { page, errors } = await open(ctx, base);
      await page.waitForSelector('#rounds .d-actions');
      assert.deepStrictEqual(errors, []);
      assert.strictEqual(await hidden(page, 'cardPanel'), false);
      assert.strictEqual(await hidden(page, 'notice'), true, 'a cookie session is signed in; the page must not ask again');
      assert.ok(sent.includes('/api/duel/today'));
      assert.strictEqual(await page.$$eval('#rounds .d-actions', (els) => els.length), 6);
      const picks = await page.$$eval('#rounds .d-actions[data-round="16"] .d-btn', (els) => els.map((e) => e.getAttribute('data-pick') + ':' + e.textContent));
      assert.deepStrictEqual(picks, ['home:ARSENAL', 'draw:DRAW', 'away:CHELSEA', 'pass:PASS']);
      assert.match(await page.textContent('#rounds'), /Arsenal vs Chelsea — who wins\? Premier League/);
      assert.strictEqual(await hidden(page, 'sportsNote'), true, 'a dealt match is nothing to apologise for');
      await ctx.close();
    });
    await t.test('the feed failed: the note is shown and the market rounds stand', async () => {
      const { ctx } = await context(browser, base, { today: card(MARKET, { sports: 'unreadable' }) });
      const { page } = await open(ctx, base);
      await page.waitForSelector('#rounds .d-actions');
      assert.strictEqual(await page.$$eval('#rounds .d-actions', (els) => els.length), 5);
      assert.strictEqual(await hidden(page, 'sportsNote'), false);
      assert.match(await page.textContent('#sportsNote'), /could not be read/);
      await ctx.close();
    });
    await t.test('no match today, or no feed: nothing is said about football', async () => {
      for (const sources of [{ sports: 'none' }, { sports: 'off' }, { sports: 'unknown' }, undefined]) {
        const { ctx } = await context(browser, base, { today: card(MARKET, sources) });
        const { page } = await open(ctx, base);
        await page.waitForSelector('#rounds .d-actions');
        assert.strictEqual(await hidden(page, 'sportsNote'), true, JSON.stringify(sources));
        await ctx.close();
      }
    });
    await t.test('signed out: the invitation, and no read of the card', async () => {
      const { ctx, sent } = await context(browser, base, { signedIn: false });
      const { page } = await open(ctx, base);
      await page.waitForFunction(() => /Sign in to make your calls/.test(document.querySelector('#notice').textContent));
      assert.strictEqual(await hidden(page, 'cardPanel'), true);
      assert.ok(!sent.includes('/api/duel/today'), 'the card was read without a session');
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});
