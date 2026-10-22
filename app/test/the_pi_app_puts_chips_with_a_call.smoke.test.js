'use strict';
/**
 * Chips in the real Pi app (/pi), driven in Chromium: the Duel tab draws the
 * odds and the chips field with the website's renderer and the app's own
 * stylesheet, a value typed in the field rides the call, and the record shows
 * the season's stack and what a chip is. With no chips block on the card,
 * none of it appears. No chip touches a Pi payment: nothing here calls the
 * Pi SDK's payment API.
 */
const test = require('node:test');
const assert = require('node:assert');
const H = require('./helpers/pi_app_harness');

const STACK = { season: '2026-10', float: 1000, available: 850, on_table: 150, held: 50, net: 0, put: 150,
  calls_with_chips: 2, paid: 0, open: 2, unread: 0, return_pct: null };
const BLOCK = { enabled: true, float: 1000, min: 10, max: 200, hold_hours: 2, odds_source: 'table-2026-10', stack: STACK };
const withChips = { ...H.DUEL_CARD, chips: BLOCK, rounds: H.DUEL_CARD.rounds.map((r) => (r.kind === 'sports'
  ? { ...r, odds: null } : r.kind === 'direction' ? { ...r, odds: { long: 1.9, short: 1.9 } } : r)) };

test('chips in the Pi app', H.SKIP ? { skip: H.SKIP } : {}, async (t) => {
  const { server, base } = await H.serve();
  const browser = await H.pw.chromium.launch({ executablePath: H.CHROMIUM, args: ['--no-sandbox'] });
  try {
    await t.test('on: odds, a usable field, the call carries its chips, and the record shows the stack', async () => {
      const { ctx, sent } = await H.context(browser, { duel: { status: 200, body: withChips },
        duelMe: { status: 200, body: { ...H.DUEL_ME, chips: BLOCK } },
        duelPick: { status: 200, contentType: 'application/json',
          body: JSON.stringify({ ...H.DUEL_PICKED, round_id: 11, kind: 'direction', symbol: 'BTCUSDT', pick: 'long', chips: 50, odds: 1.9 }) } });
      const { page, errors } = await H.open(ctx, `${base}/pi`);
      await H.signInAndSettle(page);
      await H.showTab(page, 'duel');
      const panel = '.p-panel[data-tab="duel"]';
      await page.waitForSelector(`${panel} .d-chips-in`);
      assert.match(await page.textContent(`${panel} .d-actions[data-round="11"]`), /×1\.9/);
      assert.match(await page.textContent(`${panel} .d-actions[data-round="15"]`), /No chips on this round/);
      const box = await page.$eval(`${panel} .d-chips-in`, (e) => { const r = e.getBoundingClientRect(); return { w: r.width, h: r.height }; });
      assert.ok(box.w >= 80 && box.h >= 36, `the field is a tappable size: ${JSON.stringify(box)}`);
      assert.match(await page.textContent(`${panel} .p-duel-rec`), /Chips\s*850/);
      assert.match(await page.textContent(panel), /play money with no cash value/);
      await page.fill(`${panel} .d-actions[data-round="11"] .d-chips-in`, '50');
      await page.click(`${panel} .d-actions[data-round="11"] .d-btn[data-pick="long"]`);
      await H.readAgain(sent, '/api/duel/pick', 1, 'the call was not sent');
      const post = sent.find((s) => s.path === '/api/duel/pick');
      assert.deepStrictEqual(JSON.parse(post.body), { round_id: 11, pick: 'long', chips: '50' });
      assert.ok(!sent.some((s) => s.path.startsWith('/api/pi/payments/') && s.method === 'POST'), 'no chip touches a Pi payment');
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });
    await t.test('off: no odds, no field, no stack', async () => {
      const { ctx } = await H.context(browser);
      const { page } = await H.open(ctx, `${base}/pi`);
      await H.signInAndSettle(page);
      await H.showTab(page, 'duel');
      const panel = '.p-panel[data-tab="duel"]';
      await page.waitForSelector(`${panel} .d-actions`);
      assert.strictEqual(await page.$(`${panel} .d-chips-in`), null);
      assert.doesNotMatch(await page.textContent(panel), /×1\.9|No chips on this round|play money/);
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});
