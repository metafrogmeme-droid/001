'use strict';
/**
 * Chips on the /duel page (lib/duel_chips.js), driven in Chromium against the
 * real page with the API answered by fixtures: the page's loader and its click
 * handler are inline, so only a browser sees what it sends and draws.
 *
 *   chips on      each pick on a round dealt with odds shows them, the round
 *                 has a chips field, a round dealt without says so, and the
 *                 note says what chips are;
 *   a call        a value in the field rides the POST as `chips`; an empty
 *                 field sends none;
 *   called        a call's chips read in words (on the table, held, won),
 *                 and only won takes a colour;
 *   the record    the stack sits beside the marks;
 *   chips off     no odds, no field, no note: the page is what it was.
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

const ODDS = { long: 1.9, short: 1.9 };
const STACK = { season: '2026-10', float: 1000, available: 850, on_table: 150, held: 50, net: 0, put: 150,
  calls_with_chips: 2, paid: 0, open: 2, unread: 0, return_pct: null };
const CHIPS = { enabled: true, float: 1000, min: 10, max: 200, hold_hours: 2, odds_source: 'table-2026-10', stack: STACK };
const ROUNDS = [
  { id: 11, kind: 'direction', symbol: 'BTCUSDT', options: ['long', 'short', 'pass'], callable: true, odds: ODDS },
  { id: 12, kind: 'direction', symbol: 'ETHUSDT', options: ['long', 'short', 'pass'], callable: true, odds: ODDS,
    agent_direction: 'short', my_call: { pick: 'long', pending: true, resolves_at: '2099-01-01T00:00:00.000Z',
      chips: { chips: 100, odds: 1.9, state: 'open', payout: null } } },
  { id: 13, kind: 'direction', symbol: 'SOLUSDT', options: ['long', 'short', 'pass'], callable: true, odds: ODDS,
    agent_direction: 'long', my_call: { pick: 'long', outcome: 'up', chips: { chips: 50, odds: 1.9, state: 'won', payout: 45 } },
    my_result: { state: 'correct', marks: 1, beat_agent: false } },
  { id: 14, kind: 'sports', symbol: 'FD:441', options: ['home', 'draw', 'away', 'pass'], callable: true, odds: null,
    match: { home: 'Arsenal FC', away: 'Chelsea FC', home_short: 'Arsenal', away_short: 'Chelsea', competition: 'Premier League', kickoff: '2026-10-19T19:00:00.000Z' } },
];
const card = (chips, rounds = ROUNDS) => ({ day: '2026-10-19', horizon_hours: 24, open: 2, counts_only: true, sources: { sports: 'dealt' }, chips, rounds });
const ME = (chips) => ({ handle: null, window_days: 90, accuracy: { pct: null, correct: 0, wrong: 0, push: 0, scored: 0, unresolved: 0, resolved: 0 },
  marks: { marks: 1, pending: 1, beat_agent: 0 }, streak: { current: 2 }, quests: [], history: [], chips, counts_only: true, private: true });

async function serve() {
  const app = express();
  app.get('/duel', (req, res) => res.sendFile(path.join(PUBLIC, 'duel.html')));
  app.use(express.static(PUBLIC));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function context(browser, base, { chips = CHIPS, rounds = ROUNDS } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1100, height: 900 } });
  await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
  const posted = [];
  await ctx.route('**/api/**', (route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    const json = (status, body) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (p === '/api/duel/pick') { posted.push(JSON.parse(req.postData() || '{}')); return json(200, { ok: true }); }
    if (p === '/api/duel/today') return json(200, card(chips, rounds));
    if (p === '/api/duel/me') return json(200, ME(chips));
    if (p === '/api/public/duel/board' || p === '/api/public/duel/squads') return json(200, { rows: [], warming_up: false });
    return json(200, { ok: true, data: {}, rows: [], items: [] });
  });
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
  return { ctx, posted };
}

async function open(ctx, base) {
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${base}/duel`, { waitUntil: 'load' });
  await page.waitForSelector('#rounds .d-actions');
  return { page, errors };
}

test('chips on the /duel page', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });
  try {
    await t.test('on: odds on the picks, a chips field, a round without odds says so, the note says what chips are', async () => {
      const { ctx } = await context(browser, base);
      const { page, errors } = await open(ctx, base);
      assert.deepStrictEqual(errors, []);
      const buttons = await page.$$eval('#rounds .d-actions[data-round="11"] .d-btn', (els) => els.map((e) => e.textContent.replace(/^[^A-Za-z]+/, '')));
      assert.deepStrictEqual(buttons, ['LONG ×1.9', 'SHORT ×1.9', 'PASS'], 'a pass shows no odds: it takes no chips');
      assert.ok(await page.$('#rounds .d-actions[data-round="11"] .d-chips-in'));
      assert.strictEqual(await page.getAttribute('#rounds .d-actions[data-round="11"] .d-chips-in', 'max'), '200');
      assert.strictEqual(await page.$('#rounds .d-actions[data-round="14"] .d-chips-in'), null);
      assert.match(await page.textContent('#rounds .d-actions[data-round="14"]'), /No chips on this round/);
      assert.strictEqual(await page.$eval('#chipsNote', (e) => e.hidden), false);
      assert.match(await page.textContent('#chipsNote'), /cannot be bought|nobody can buy/i);
      await ctx.close();
    });
    await t.test('a call carries the chips typed beside it, and an empty field carries none', async () => {
      const { ctx, posted } = await context(browser, base);
      const { page } = await open(ctx, base);
      await page.fill('#rounds .d-actions[data-round="11"] .d-chips-in', '50');
      await page.click('#rounds .d-actions[data-round="11"] .d-btn[data-pick="long"]');
      await page.waitForFunction(() => document.querySelectorAll('#rounds .d-actions').length > 0);
      await page.waitForTimeout(100);
      assert.deepStrictEqual(posted[0], { round_id: 11, pick: 'long', chips: '50' });
      await page.click('#rounds .d-actions[data-round="11"] .d-btn[data-pick="short"]');
      await page.waitForTimeout(100);
      assert.deepStrictEqual(posted[1], { round_id: 11, pick: 'short' }, 'no value is no chips, never chips: 0');
      await ctx.close();
    });
    await t.test('a call\'s chips read in words, and only a win is green', async () => {
      const { ctx } = await context(browser, base);
      const { page } = await open(ctx, base);
      const open12 = await page.$eval('#rounds .d-round:nth-child(2) .d-chips-st', (e) => ({ text: e.textContent, cls: e.className }));
      assert.strictEqual(open12.text, '100 chips on the table at ×1.9');
      assert.match(open12.cls, /tone-muted/, 'chips on the table are not a result');
      const won = await page.$eval('#rounds .d-round:nth-child(3) .d-chips-st', (e) => ({ text: e.textContent, cls: e.className }));
      assert.strictEqual(won.text, 'Won 45 chips at ×1.9');
      assert.match(won.cls, /tone-up/);
      await page.waitForSelector('#record .stat');
      const rec = await page.textContent('#record');
      assert.match(rec, /Chips\s*850/);
      assert.match(rec, /150 on the table · 50 held/);
      assert.match(rec, /play money with no cash value/);
      await ctx.close();
    });
    await t.test('switched off mid-season: no odds, no field, no note, and the stack still in the record', async () => {
      const plain = ROUNDS.map(({ odds, ...r }) => r);
      const { ctx } = await context(browser, base, { chips: { ...CHIPS, enabled: false }, rounds: plain });
      const { page } = await open(ctx, base);
      assert.strictEqual(await page.$('#rounds .d-chips-in'), null);
      assert.strictEqual(await page.$eval('#chipsNote', (e) => e.hidden), true, 'chips are not offered, so the note is not said');
      await page.waitForSelector('#record .stat');
      assert.match(await page.textContent('#record'), /Chips\s*850/, 'what was put is still shown');
      await ctx.close();
    });
    await t.test('off: no odds, no field, no note, and no stack in the record', async () => {
      const plain = ROUNDS.map(({ odds, my_call, my_result, ...r }) => r);
      const { ctx } = await context(browser, base, { chips: null, rounds: plain });
      const { page } = await open(ctx, base);
      const buttons = await page.$$eval('#rounds .d-actions[data-round="11"] .d-btn', (els) => els.map((e) => e.textContent.replace(/^[^A-Za-z]+/, '')));
      assert.deepStrictEqual(buttons, ['LONG', 'SHORT', 'PASS']);
      assert.strictEqual(await page.$('#rounds .d-chips-in'), null);
      assert.ok(!/No chips on this round/.test(await page.textContent('#rounds')));
      assert.strictEqual(await page.$eval('#chipsNote', (e) => e.hidden), true);
      await page.waitForSelector('#record .stat');
      assert.ok(!/Chips/.test(await page.textContent('#record')));
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});
