'use strict';
/**
 * Flags and the operator's review queue on the /duel page (lib/duel_review.js),
 * driven in Chromium against the real page with the API answered by fixtures:
 * the flag handler and the queue are inline, so only a browser sees what they
 * send and draw.
 *
 *   a flaggable call   shows a flag button; a click POSTs its round and the
 *                      page redraws from the server
 *   flagged, reviewed  read in words, the review with its receipt link
 *   the queue          hidden for a 403 (everyone but the operator), drawn for
 *                      the operator, and said to be unread on any other
 *                      failure: never an empty queue for a dead read
 *   a decision         POSTs the pick, the decision, the evidence typed, and
 *                      the outcome chosen only for a correction
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

const SEAL = 'e'.repeat(64);
const settled = (over) => ({ pick: 'long', outcome: 'down', settle_seal: 'd'.repeat(64), flagged: false, review: null, ...over });
const ROUNDS = [
  { id: 21, kind: 'direction', symbol: 'BTCUSDT', options: ['long', 'short', 'pass'], callable: false, agent_direction: 'short',
    my_call: settled({ flaggable: true }), my_result: { state: 'wrong', marks: 0, beat_agent: false } },
  { id: 22, kind: 'direction', symbol: 'ETHUSDT', options: ['long', 'short', 'pass'], callable: false, agent_direction: 'short',
    my_call: settled({ flagged: true, flaggable: false }), my_result: { state: 'wrong', marks: 0, beat_agent: false } },
  { id: 23, kind: 'direction', symbol: 'SOLUSDT', options: ['long', 'short', 'pass'], callable: false, agent_direction: 'short',
    my_call: settled({ flagged: true, flaggable: false, review: { state: 'corrected', outcome: 'up', seal: SEAL } }),
    my_result: { state: 'correct', marks: 2, beat_agent: true } },
  { id: 24, kind: 'direction', symbol: 'XRPUSDT', options: ['long', 'short', 'pass'], callable: false, agent_direction: 'short',
    my_call: settled({ flaggable: false }), my_result: { state: 'wrong', marks: 0, beat_agent: false } },
];
const CARD = { day: '2026-10-22', horizon_hours: 24, open: 0, counts_only: true, sources: {}, chips: null, rounds: ROUNDS };
const ME = { handle: null, window_days: 90, accuracy: { pct: null, correct: 0, wrong: 0, push: 0, scored: 0, unresolved: 0, resolved: 0 },
  marks: { marks: 0, pending: 0, beat_agent: 0 }, streak: { current: 0 }, quests: [], history: [], chips: null, counts_only: true, private: true };
const ITEM = { pick_id: 7, round_id: 21, day: '2026-10-21', kind: 'direction', symbol: 'BTCUSDT', symbol_b: null, pick: 'long',
  entry_price: 60000, settle_price: 59000, settle_price_b: null, second_price: 61000, outcome: 'down', result: 'wrong',
  outcomes: ['up', 'down', 'flat'], disputed: true, flagged: true, chips: 50, settled_at: '2026-10-22T09:00:00.000Z', settle_seal: 'd'.repeat(64) };

async function serve() {
  const app = express();
  app.get('/duel', (req, res) => res.sendFile(path.join(PUBLIC, 'duel.html')));
  app.use(express.static(PUBLIC));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

/** `queue`: an HTTP status for GET /api/duel/review, or a body to answer 200 with. */
async function context(browser, base, { queue = 403 } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1100, height: 900 } });
  await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
  const posted = []; const reads = { today: 0, review: 0 };
  let q = queue;
  await ctx.route('**/api/**', (route) => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    const json = (status, body) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (req.method() === 'POST') {
      posted.push({ path: p, body: JSON.parse(req.postData() || '{}') });
      if (p === '/api/duel/review') q = { items: [], counts_only: true };
      return json(200, { ok: true });
    }
    if (p === '/api/duel/today') { reads.today++; return json(200, CARD); }
    if (p === '/api/duel/me') return json(200, ME);
    if (p === '/api/duel/review') {
      reads.review++;
      return typeof q === 'number' ? json(q, { error: 'x' }) : json(200, q);
    }
    if (p === '/api/public/duel/board' || p === '/api/public/duel/squads') return json(200, { rows: [], warming_up: false });
    return json(200, { ok: true, data: {}, rows: [], items: [] });
  });
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
  return { ctx, posted, reads };
}

async function open(ctx, base) {
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${base}/duel`, { waitUntil: 'load' });
  await page.waitForSelector('#rounds .d-round');
  return { page, errors };
}

test('flags and reviews on the /duel page', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });
  try {
    await t.test('a flaggable call has a flag button, and a click posts its round and redraws', async () => {
      const { ctx, posted, reads } = await context(browser, base);
      const { page, errors } = await open(ctx, base);
      assert.deepStrictEqual(errors, []);
      const flags = await page.$$eval('#rounds .d-flag', (els) => els.map((e) => e.getAttribute('data-flag')));
      assert.deepStrictEqual(flags, ['21'], 'only the flaggable call: not the flagged, the reviewed, or the closed one');
      assert.strictEqual(await page.textContent('#rounds .d-flag'), 'Flag this result');
      const before = reads.today;
      await page.click('#rounds .d-flag');
      await page.waitForTimeout(150);
      assert.deepStrictEqual(posted, [{ path: '/api/duel/flag', body: { round_id: 21 } }]);
      assert.ok(reads.today > before, 'the page redraws from the server, not from a guess');
      await ctx.close();
    });
    await t.test('flagged and reviewed calls read in words, the review with its receipt', async () => {
      const { ctx } = await context(browser, base);
      const { page } = await open(ctx, base);
      const text = (n) => page.$eval(`#rounds .d-round:nth-child(${n}) .d-review`, (e) => e.textContent);
      assert.strictEqual(await text(2), 'Flagged: waiting for a review');
      assert.match(await text(3), /^Reviewed: corrected to UP Review eeeeeeee…$/);
      assert.strictEqual(await page.getAttribute('#rounds .d-round:nth-child(3) .d-review a.d-rc', 'href'), `/call/review:${SEAL}`);
      assert.strictEqual(await page.$('#rounds .d-round:nth-child(4) .d-review'), null, 'a closed window shows nothing');
      await ctx.close();
    });
    await t.test('the queue: hidden for everyone but the operator', async () => {
      for (const status of [401, 403]) {
        const { ctx, reads } = await context(browser, base, { queue: status });
        const { page } = await open(ctx, base);
        await page.waitForTimeout(150);
        assert.ok(reads.review >= 1, 'the page asked');
        assert.strictEqual(await page.$eval('#reviewPanel', (e) => e.hidden), true, `${status}: hidden`);
        await ctx.close();
      }
    });
    await t.test('the queue: an unreadable one is said to be unread, never drawn empty', async () => {
      const { ctx } = await context(browser, base, { queue: 503 });
      const { page } = await open(ctx, base);
      await page.waitForFunction(() => !document.getElementById('reviewPanel').hidden);
      const q = await page.textContent('#reviewQueue');
      assert.match(q, /could not be read/);
      assert.ok(!/Nothing is waiting/.test(q));
      await ctx.close();
    });
    await t.test('the queue: drawn for the operator, and a decision posts what was typed and chosen', async () => {
      const { ctx, posted } = await context(browser, base, { queue: { items: [ITEM], counts_only: true } });
      const { page } = await open(ctx, base);
      await page.waitForSelector('#reviewQueue .rv-row');
      const row = await page.textContent('#reviewQueue .rv-row');
      assert.match(row, /BTCUSDT/); assert.match(row, /called LONG/); assert.match(row, /settled down \(wrong\)/);
      assert.match(row, /disputed/); assert.match(row, /flagged/); assert.match(row, /50 chips/); assert.match(row, /second source 61000/);
      const options = await page.$$eval('#reviewQueue .rv-out option', (els) => els.map((e) => e.value));
      assert.deepStrictEqual(options, ['up', 'flat'], 'a correction is to another outcome, never the one given');
      await page.fill('#reviewQueue .rv-ev', 'venue wick');
      await page.click('#reviewQueue .rv-go[data-decision="corrected"]');
      await page.waitForSelector('#reviewQueue p.note');
      assert.deepStrictEqual(posted.at(-1), { path: '/api/duel/review',
        body: { pick_id: 7, decision: 'corrected', evidence: 'venue wick', outcome: 'up' } });
      assert.strictEqual(await page.textContent('#reviewQueue'), 'Nothing is waiting for review.', 'reloaded from the server');
      await ctx.close();
    });
    await t.test('the queue: a decision other than a correction names no outcome', async () => {
      const { ctx, posted } = await context(browser, base, { queue: { items: [ITEM], counts_only: true } });
      const { page } = await open(ctx, base);
      await page.waitForSelector('#reviewQueue .rv-row');
      await page.fill('#reviewQueue .rv-ev', 'sources split');
      await page.click('#reviewQueue .rv-go[data-decision="push"]');
      await page.waitForSelector('#reviewQueue p.note');
      assert.deepStrictEqual(posted.at(-1).body, { pick_id: 7, decision: 'push', evidence: 'sources split' });
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});
