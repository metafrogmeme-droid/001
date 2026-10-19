'use strict';
/**
 * The website Arena shows the Daily Duel: what is open today, by category,
 * and the player's record, with the game itself one click away at /duel.
 *
 * Driven in Chromium against the real page (public/arena.html) with the API
 * answered by fixtures, because the panel is inline in the page's script and
 * a scan could not see what it draws. Four readings, four sentences:
 *
 *   signed out      the invitation, and no read of the card (the route needs
 *                   a session; a 401 drawn as "nothing open" would be a lie);
 *   signed in       the open count and the categories open, the record with
 *                   a dash before anything has settled (never 0%);
 *   card unreadable said to be unreadable, not drawn empty;
 *   record unreadable  left out, the card standing.
 *
 * The link to /duel is on the page in every case: the panel's job is to lead
 * there.
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

const CARD = { day: '2026-10-19', horizon_hours: 24, open: 3, counts_only: true, rounds: [
  { id: 11, kind: 'direction', symbol: 'BTCUSDT', options: ['long', 'short', 'pass'], callable: true },
  { id: 12, kind: 'direction', symbol: 'ETHUSDT', options: ['long', 'short', 'pass'], callable: true },
  { id: 13, kind: 'direction', symbol: 'SOLUSDT', options: ['long', 'short', 'pass'], callable: true, agent_direction: 'long',
    my_call: { pick: 'long', pending: true }, my_result: { state: 'unresolved', marks: 0, beat_agent: false } },
  { id: 14, kind: 'range', symbol: 'BNBUSDT', options: ['break', 'hold', 'pass'], callable: true, threshold_pct: 2 },
  { id: 15, kind: 'mover', symbol: 'XRPUSDT', symbol_b: 'ARBUSDT', options: ['first', 'second', 'pass'], callable: true },
] };
const ME = { handle: null, window_days: 90, accuracy: { pct: null, correct: 0, wrong: 0, push: 0, scored: 0, unresolved: 1, resolved: 0 },
  marks: { marks: 0, pending: 1, beat_agent: 0 }, streak: { current: 1 }, quests: [], history: [], counts_only: true, private: true };

async function serve() {
  const app = express();
  app.get('/arena', (req, res) => res.sendFile(path.join(PUBLIC, 'arena.html')));
  app.use(express.static(PUBLIC));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function context(browser, base, { signedIn = true, today = { status: 200, body: CARD }, me = { status: 200, body: ME } } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1100, height: 900 } });
  if (signedIn) await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
  const sent = [];
  await ctx.route('**/api/**', (route) => {
    const p = new URL(route.request().url()).pathname;
    sent.push(p);
    const json = (status, body) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (p === '/api/duel/today') return json(today.status, today.body);
    if (p === '/api/duel/me') return json(me.status, me.body);
    if (p === '/api/auth/me') return json(signedIn ? 200 : 401, signedIn ? { email: 'p@example.com', plan: 'free' } : { error: 'unauthorized' });
    return json(200, { ok: true, data: {}, rows: [], items: [], signals: [], seasons: [] });
  });
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
  return { ctx, sent };
}

async function open(ctx, base) {
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${base}/arena`, { waitUntil: 'load' });
  await page.waitForSelector('#duelPanel');
  return { page, errors };
}

test('the Arena shows the Daily Duel', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });
  try {
    await t.test('signed out: the invitation, the link, and no read of the card', async () => {
      const { ctx, sent } = await context(browser, base, { signedIn: false });
      const { page } = await open(ctx, base);
      await page.waitForFunction(() => /Sign in to call today/.test(document.querySelector('#duelBody').textContent));
      assert.ok(!sent.includes('/api/duel/today'), 'the card was read without a session');
      assert.strictEqual(await page.getAttribute('#duelPanel a[href="/duel"]', 'href'), '/duel');
      await ctx.close();
    });
    await t.test('signed in: what is open by category, and a record that claims nothing before it can', async () => {
      const { ctx, sent } = await context(browser, base);
      const { page } = await open(ctx, base);
      await page.waitForSelector('#duelBody .duel-rec');
      const body = await page.textContent('#duelBody');
      assert.match(body, /4 of 5 rounds still open today/);
      const chips = await page.$$eval('#duelBody .d-chip', (els) => els.map((e) => e.textContent));
      assert.deepStrictEqual(chips, ['Direction ×2', 'Range', 'Bigger mover']);
      assert.match(body, /Accuracy/);
      assert.match(body, /—/);
      assert.doesNotMatch(body, /0%/, 'nothing has settled, so there is no rate');
      assert.match(body, /1 still running/);
      assert.match(body, /🔥 1/);
      assert.ok(sent.includes('/api/duel/today') && sent.includes('/api/duel/me'));
      await ctx.close();
    });
    await t.test('every round called: said so, with the link still there', async () => {
      const called = { ...CARD, rounds: CARD.rounds.map((r) => ({ ...r, my_call: { pick: 'pass', pending: true } })) };
      const { ctx } = await context(browser, base, { today: { status: 200, body: called } });
      const { page } = await open(ctx, base);
      await page.waitForFunction(() => /called every round today/.test(document.querySelector('#duelBody').textContent));
      assert.strictEqual(await page.$('#duelBody .d-chip'), null);
      assert.ok(await page.$('#duelPanel a[href="/duel"]'));
      await ctx.close();
    });
    await t.test('an unreadable card is said to be, never drawn as nothing open', async () => {
      const { ctx } = await context(browser, base, { today: { status: 503, body: { error: 'The day\'s rounds are unavailable' } } });
      const { page } = await open(ctx, base);
      await page.waitForFunction(() => /could not be loaded/.test(document.querySelector('#duelBody').textContent));
      const body = await page.textContent('#duelBody');
      assert.doesNotMatch(body, /rounds still open|called every round/);
      assert.strictEqual(await page.$('#duelBody .duel-rec'), null, 'no record beside an unreadable card');
      await ctx.close();
    });
    await t.test('an unreadable record is left out; the card stands', async () => {
      const { ctx } = await context(browser, base, { me: { status: 500, body: { error: 'Your record is unavailable' } } });
      const { page } = await open(ctx, base);
      await page.waitForFunction(() => /rounds still open today/.test(document.querySelector('#duelBody').textContent));
      await page.waitForTimeout(150);
      assert.strictEqual(await page.$('#duelBody .duel-rec'), null);
      assert.doesNotMatch(await page.textContent('#duelBody'), /Accuracy|0%/);
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});
