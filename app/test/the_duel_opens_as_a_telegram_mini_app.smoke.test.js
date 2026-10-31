'use strict';
/**
 * The Duel's Telegram Mini App (/tg) in Chromium, with a stand-in for
 * Telegram's script: it signs in by itself with the launch data Telegram
 * handed over, sends that data as received and nothing the page read into
 * it, opens on the Duel with no paper account, keeps the proof behind the
 * header's button, and says in the reader's language why it could not sign
 * in: opened outside Telegram, a launch too old, or a fault of ours.
 */
const test = require('node:test');
const assert = require('node:assert');
const H = require('./helpers/pi_app_harness');

const INIT = 'auth_date=1761134400&query_id=AAH&user=%7B%22id%22%3A555000111%7D&hash=ff&signature=' + 'A'.repeat(86);
const TG_STUB = `
window.__tg = { ready: 0, expand: 0 };
window.Telegram = { WebApp: {
  initData: window.__TG_INIT__ || '',
  // What the page could read for itself, and must never send.
  initDataUnsafe: { user: { id: 424242, first_name: 'Someone else' } },
  ready: function () { window.__tg.ready += 1; },
  expand: function () { window.__tg.expand += 1; }
} };`;

async function launch(browser, { init = INIT, signin = { status: 200, body: { token: 'session-jwt-1', handle: null } }, locale = null } = {}) {
  const { ctx, sent } = await H.context(browser, { locale,
    routes: { '/api/tgapp/signin': (route) => route.fulfill({ status: signin.status, contentType: 'application/json', body: JSON.stringify(signin.body) }) } });
  await ctx.addInitScript((i) => { window.__TG_INIT__ = i; }, init);
  // The page pins telegram-web-app.js with SRI. This stand-in is not those
  // bytes, so the browser will not run it. The same stand-in is installed
  // before the page, which is what tg-app.js calls.
  await ctx.addInitScript(TG_STUB);
  await ctx.route('https://telegram.org/**', (route) => route.fulfill({
    status: 200, contentType: 'application/javascript', body: TG_STUB,
    headers: { 'access-control-allow-origin': '*' },
  }));
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  return { ctx, sent, page, errors };
}

test('the Duel as a Telegram Mini App', H.SKIP ? { skip: H.SKIP } : {}, async (t) => {
  const { server, base } = await H.serve();
  const browser = await H.pw.chromium.launch({ executablePath: H.CHROMIUM, args: ['--no-sandbox'] });
  try {
    await t.test('it signs in by itself and opens on the Duel, with no paper account', async () => {
      const { ctx, sent, page, errors } = await launch(browser);
      await page.goto(`${base}/tg`, { waitUntil: 'load' });
      await page.waitForSelector('.p-panel[data-tab="duel"]:not([hidden]) .d-round, .p-panel[data-tab="duel"]:not([hidden]) [data-round]', { timeout: 8000 });
      const signin = sent.filter((s) => s.path === '/api/tgapp/signin');
      assert.strictEqual(signin.length, 1, 'no tap needed');
      assert.deepStrictEqual(JSON.parse(signin[0].body), { init_data: INIT }, 'the launch as Telegram handed it, nothing the page read');
      assert.ok(sent.filter((s) => s.path === '/api/duel/today').every((s) => s.auth === 'Bearer session-jwt-1'));
      assert.ok(!sent.some((s) => s.path.startsWith('/api/arena/account')), 'no paper account is read');
      assert.strictEqual(await page.$('.m-sym'), null, 'no open form');
      assert.strictEqual(await page.$('.p-tab-b[data-tab="arena"]'), null, 'no Arena tab');
      assert.ok(await page.$('.p-off-b[data-tab="proof"]'), 'the proof behind the header\'s button');
      assert.ok((await page.evaluate(() => window.__tg.ready)) >= 1, 'Telegram is told the app is ready');
      assert.strictEqual(await page.$('a[href*="/token"], a[href*="presale"]'), null);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });
    await t.test('opened outside Telegram, it says so and asks nothing', async () => {
      const { ctx, sent, page } = await launch(browser, { init: '' });
      await page.goto(`${base}/tg`, { waitUntil: 'load' });
      await page.waitForSelector('.m-problem');
      assert.match(await page.textContent('.m-problem'), /Open this from the RUNECLAW Duel bot in Telegram to sign in\./);
      assert.ok(!sent.some((s) => s.path === '/api/tgapp/signin'));
      await ctx.close();
    });
    await t.test('a launch too old, and a fault of ours, are different sentences', async () => {
      let l = await launch(browser, { signin: { status: 401, body: { error: 'tgapp_rejected', reason: 'expired' } } });
      await l.page.goto(`${base}/tg`, { waitUntil: 'load' });
      await l.page.waitForSelector('.m-problem');
      assert.match(await l.page.textContent('.m-problem'), /That launch is too old\. Close the app and open it again from the bot\./);
      await l.ctx.close();
      l = await launch(browser, { signin: { status: 503, body: { error: 'tgapp_unconfigured' } } });
      await l.page.goto(`${base}/tg`, { waitUntil: 'load' });
      await l.page.waitForSelector('.m-problem');
      assert.match(await l.page.textContent('.m-problem'), /this is on our side/);
      await l.ctx.close();
    });
    await t.test('in the reader\'s language', async () => {
      const { ctx, page } = await launch(browser, { init: '', locale: 'de-DE' });
      await page.goto(`${base}/tg`, { waitUntil: 'load' });
      await page.waitForSelector('.m-problem');
      assert.match(await page.textContent('.m-problem'), /Öffne das über den RUNECLAW-Duel-Bot in Telegram/);
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});
