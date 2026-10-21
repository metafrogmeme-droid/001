'use strict';
/**
 * Ask the Claw in the Pi Browser app, driven in Chromium on the shared
 * harness (helpers/pi_app_harness.js): a starter question, the "Why?" on a
 * signal card and the "Explain this round" on a Duel round each reach the
 * coach with what they are about; the reply is drawn with bare emphasis and
 * no link; a used-up day offers Coach Plus, which is paid through the same
 * server-ordered Pi payment as a tip.
 */
const test = require('node:test');
const assert = require('node:assert');

const { pw, CHROMIUM, SKIP, LIVE_CALL, serve, context, open, signInAndSettle, showTab } = require('./helpers/pi_app_harness');

const json = (route, status, body) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
const OPEN = { enabled: true, limit: 20, left: 20, resets_at: '2026-10-21T00:00:00.000Z', plus: { offered: null, daily: 100, until: null } };

function coachRoutes({ read = OPEN, reply } = {}) {
  const asked = [];
  let state = read;
  return {
    asked,
    routes: {
      '/api/pi/coach': (route, req) => {
        if (req.method() === 'GET') return json(route, 200, state);
        const body = JSON.parse(req.postData());
        asked.push(body);
        state = { ...state, left: state.left - 1 };
        return json(route, 200, { reply_html: reply || '<b>A stop-loss</b> closes the trade at a price you chose. <a href="https://x.example">more</a>',
          intent: 'pi_coach', limit: state.limit, left: state.left, resets_at: state.resets_at, plus: state.plus });
      },
    },
  };
}

test('Ask the Claw answers in the app, from every door', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });
  try {
    await t.test('a starter question is asked and answered, the reply drawn with no link', async () => {
      const c = coachRoutes();
      const { ctx } = await context(browser, { routes: c.routes });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await showTab(page, 'coach');
      assert.match(await page.textContent('.p-coach-left'), /20 of 20 questions left today/);
      await page.click('.p-coach-chip >> text=What is a stop-loss?');
      await page.waitForSelector('.p-coach-msg--claw b');
      assert.deepStrictEqual(c.asked, [{ text: 'What is a stop-loss?' }]);
      assert.strictEqual(await page.$('.p-coach-log a'), null, 'the reply drew a link');
      assert.match(await page.textContent('.p-coach-msg--claw'), /A stop-loss closes the trade/);
      assert.match(await page.textContent('.p-coach-left'), /19 of 20 questions left today/);
      // Typed questions go the same way.
      await page.fill('.p-coach-in', 'What does leverage do?');
      await page.click('.p-coach-send');
      await page.waitForFunction(() => document.querySelectorAll('.p-coach-msg--claw').length === 2);
      assert.deepStrictEqual(c.asked[1], { text: 'What does leverage do?' });
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('"Why?" on a signal card and "Explain this round" on the Duel open the coach on what they name', async () => {
      const c = coachRoutes();
      const { ctx } = await context(browser, { routes: c.routes, signals: { status: 200, body: { signals: [LIVE_CALL] } } });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await showTab(page, 'signals');
      await page.waitForSelector('.p-signals .p-sig-plain');
      assert.strictEqual(await page.textContent('.p-signals .p-sig-plain'),
        'The call: BTC rises toward 67000. It is given up if BTC falls to 64000. The target is 2.0 times as far away as the stop.');
      await page.click('.p-signals .p-why');
      await page.waitForSelector('.p-panel[data-tab="coach"]:not([hidden]) .p-coach-msg--claw');
      assert.deepStrictEqual(c.asked[0].about, { kind: 'signal', key: 'k-live-1' });
      assert.match(c.asked[0].text, /BTC LONG/);
      await showTab(page, 'duel');
      await page.click('.p-duel .p-why[data-id="11"]');
      await page.waitForFunction(() => document.querySelectorAll('.p-coach-msg--claw').length === 2);
      assert.deepStrictEqual(c.asked[1].about, { kind: 'round', id: 11 });
      assert.match(c.asked[1].text, /Direction round on BTC/);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('a used-up day says when it resets, and Coach Plus is paid as the server ordered it', async () => {
      const plusOffer = { offered: { amount: 1, days: 30 }, daily: 100, until: null };
      let read = { ...OPEN, left: 0, plus: plusOffer };
      const { ctx, sent } = await context(browser, { routes: {
        '/api/pi/coach': (route) => json(route, 200, read),
        '/api/pi/payments/coach-plus': (route, req) => {
          if (req.headers().authorization !== 'Bearer session-jwt-1') return json(route, 401, {});
          return json(route, 200, { orderId: 'order-plus', amount: 1, memo: 'RUNECLAW Coach Plus', metadata: { orderId: 'order-plus', kind: 'coach_plus' } });
        },
        '/api/pi/payments/complete': (route) => {
          read = { ...OPEN, limit: 100, left: 100, plus: { ...plusOffer, until: '2026-11-19T10:00:00.000Z' } };
          return json(route, 200, { ok: true, status: 'completed' });
        },
      } });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await showTab(page, 'coach');
      await page.waitForSelector('.p-coach-plus');
      assert.match(await page.textContent('.p-coach-out'), /You have used today’s questions\. More at 00:00 UTC\./);
      assert.strictEqual(await page.isDisabled('.p-coach-send'), true, 'the Ask button stayed live with nothing left');
      assert.match(await page.textContent('.p-coach-plus'), /100 questions a day for 30 days, 1 π/);
      await page.click('.p-coach-plus');
      await page.waitForFunction(() => /Coach Plus is on/.test(document.querySelector('.p-coach').textContent));
      const pi = await page.evaluate(() => window.__pi);
      assert.deepStrictEqual(pi.payments, [{ amount: 1, memo: 'RUNECLAW Coach Plus', metadata: { orderId: 'order-plus', kind: 'coach_plus' } }],
        'createPayment got something other than the server\'s order');
      assert.ok(sent.some((s) => s.path === '/api/pi/payments/approve'), 'the payment was never approved by the server');
      await page.waitForFunction(() => /100 of 100 questions left today · Coach Plus until 2026-11-19/.test(document.querySelector('.p-coach-left').textContent));
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('a coach that is not available says so, and no form is offered', async () => {
      const { ctx } = await context(browser, { routes: { '/api/pi/coach': (route) => json(route, 200, { enabled: false, reason: 'coach_not_configured' }) } });
      const { page } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await showTab(page, 'coach');
      await page.waitForFunction(() => /The coach is not available right now/.test(document.querySelector('.p-coach').textContent));
      assert.strictEqual(await page.$('.p-coach-send'), null);
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});
