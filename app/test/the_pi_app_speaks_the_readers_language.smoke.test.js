'use strict';
/**
 * The Pi Browser app in the reader's language, driven in Chromium on the
 * shared harness (helpers/pi_app_harness.js). The browser's language is
 * Playwright's `locale`, which it sends as Accept-Language, as the Pi Browser
 * does.
 *
 *   - a Spanish browser gets the app in Spanish from the first screen, the
 *     tab bar included, with no script refused by the page's policy;
 *   - About switches to another language in place (the session survives:
 *     a link would end it) and back to English;
 *   - the coach is asked in the language on screen, and told which;
 *   - Arabic is drawn right to left.
 *
 * The expected words are read from the translation sources, so a better
 * translation does not break a test about whether translation happens.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const vm = require('node:vm');
const { pw, CHROMIUM, SKIP, LIVE_CALL, serve, context, open, signInAndSettle, showTab } = require('./helpers/pi_app_harness');

/** A language's words as the page gets them: its built chunk, run against a recorder. */
function table(code) {
  let pairs = null;
  const src = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'pi-i18n', `${code}.js`), 'utf8');
  vm.runInNewContext(src, { window: { RCPiI18n: { add: (c, p) => { pairs = p; } } } });
  const map = new Map(pairs);
  return (en) => {
    assert.ok(map.has(en), `${code} has no "${en}"`);
    return map.get(en);
  };
}

/** Pages errors and console errors (a script the CSP refused is the latter). */
function watch(page) {
  const problems = [];
  page.on('console', (m) => { if (m.type() === 'error') problems.push(m.text()); });
  return problems;
}

test('the Pi app speaks the reader\'s language', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });
  try {
    await t.test('a Spanish browser gets the app in Spanish from the first screen', async () => {
      const es = table('es');
      const { ctx } = await context(browser, { locale: 'es-ES', signals: { status: 200, body: { signals: [LIVE_CALL] } } });
      const { page, errors } = await open(ctx, `${base}/pi`);
      const consoleErrors = watch(page);
      assert.strictEqual(await page.getAttribute('html', 'lang'), 'es');
      // The first screen, before signing in.
      const first = await page.textContent('#root');
      assert.ok(first.includes(es('Beat the Claw')), first);
      assert.ok(!first.includes('Beat the Claw'), 'the English title is still on the first screen');
      await signInAndSettle(page);
      // The tab bar, drawn by the shared session from the blocks' labels.
      const tabs = await page.$$eval('.p-tab-b', (bs) => bs.map((b) => b.textContent.trim()));
      for (const en of ['Arena', 'Duel', 'Signals', 'Leaders']) {
        assert.ok(tabs.some((x) => x.includes(es(en))), `${en} is not ${es(en)} in ${JSON.stringify(tabs)}`);
      }
      await showTab(page, 'duel');
      assert.ok((await page.textContent('.p-duel h2')).includes(es('Daily Duel')));
      // The Duel renderer's own words (duel.js), asked through the same table.
      const duel = await page.textContent('.p-duel');
      assert.ok(duel.includes(es('Hidden until you call')), 'the renderer\'s words are still English');
      // The signal card's level labels (embed-row.js) and the plain line.
      await showTab(page, 'signals');
      await page.waitForSelector('.p-signals .e-lv');
      const levels = await page.$$eval('.p-signals .e-lv dt', (ds) => ds.map((d) => d.textContent));
      assert.deepStrictEqual(levels.slice(0, 3), [es('entry'), es('stop'), es('target')]);
      assert.ok((await page.textContent('.p-signals .p-sig-plain')).startsWith(es('The call: {sym} rises toward {price}.').split('{sym}')[0]));
      assert.deepStrictEqual(errors, []);
      assert.deepStrictEqual(consoleErrors.filter((e) => /Content Security Policy|Refused/.test(e)), []);
      await ctx.close();
    });

    await t.test('About switches the language in place, and back, and the session survives', async () => {
      const vi = table('vi');
      const { ctx, sent } = await context(browser, {});
      const { page, errors } = await open(ctx, `${base}/pi`);
      assert.strictEqual(await page.getAttribute('html', 'lang'), 'en');
      await signInAndSettle(page);
      await showTab(page, 'about');
      assert.strictEqual(await page.getAttribute('.p-lang-b[data-lang="en"]', 'aria-pressed'), 'true');
      const chunkAsked = page.waitForRequest((r) => /\/js\/pi-i18n\/vi\.js\?v=[0-9a-f]{8}$/.test(r.url()));
      await page.click('.p-lang-b[data-lang="vi"]');
      await chunkAsked;
      await page.waitForFunction(() => document.documentElement.lang === 'vi');
      await page.waitForFunction((want) => document.querySelector('.p-about h2').textContent === want,
        vi('About RUNECLAW Arena'));
      assert.strictEqual(await page.getAttribute('.p-lang-b[data-lang="vi"]', 'aria-pressed'), 'true');
      // Still signed in: no second sign-in was asked for, and the bar is there.
      assert.strictEqual(sent.filter((s) => s.path === '/api/pi/signin').length, 1);
      const tabs = await page.$$eval('.p-tab-b', (bs) => bs.map((b) => b.textContent.trim()));
      assert.ok(tabs.some((x) => x.includes(vi('Leaders'))), JSON.stringify(tabs));
      // And back to English, with no chunk to load.
      await page.click('.p-lang-b[data-lang="en"]');
      await page.waitForFunction(() => document.documentElement.lang === 'en');
      assert.strictEqual(await page.textContent('.p-about h2'), 'About RUNECLAW Arena');
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('the coach is asked in the language on screen, and told which', async () => {
      const es = table('es');
      const asked = [];
      const { ctx } = await context(browser, { locale: 'es', routes: {
        '/api/pi/coach': (route, req) => {
          if (req.method() === 'GET') {
            return route.fulfill({ status: 200, contentType: 'application/json',
              body: JSON.stringify({ enabled: true, limit: 20, left: 20, resets_at: '2026-10-21T00:00:00.000Z', plus: { offered: null, daily: 100, until: null } }) });
          }
          asked.push(JSON.parse(req.postData()));
          return route.fulfill({ status: 200, contentType: 'application/json',
            body: JSON.stringify({ reply_html: 'Un <b>stop-loss</b> cierra la operación.', limit: 20, left: 19, resets_at: '2026-10-21T00:00:00.000Z', plus: { offered: null, daily: 100, until: null } }) });
        },
      } });
      const { page, errors } = await open(ctx, `${base}/pi`);
      await signInAndSettle(page);
      await showTab(page, 'coach');
      await page.click(`.p-coach-chip >> text=${es('What is a stop-loss?')}`);
      await page.waitForSelector('.p-coach-msg--claw b');
      assert.deepStrictEqual(asked, [{ text: es('What is a stop-loss?'), lang: 'es' }]);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('season prizes show on Leaders only when the operator turned them on', async () => {
      const off = await context(browser, {});
      const a = await open(off.ctx, `${base}/pi`);
      await signInAndSettle(a.page);
      await showTab(a.page, 'leaders');
      // The harness answers a route it does not know with a 404: a server
      // without prizes, which is neither a table nor a fault to report.
      assert.strictEqual(await a.page.$('.p-prizes, .p-prize-note'), null, 'a prize table, or a fault, with the prizes off');
      await off.ctx.close();
      const on = await context(browser, { routes: {
        '/api/pi/prizes': (route) => route.fulfill({ status: 200, contentType: 'application/json',
          body: JSON.stringify({ enabled: true, places: [50, 30, 20], min_trades: 5, total: 100, season: null }) }),
      } });
      const b = await open(on.ctx, `${base}/pi`);
      await signInAndSettle(b.page);
      await showTab(b.page, 'leaders');
      await b.page.waitForSelector('.p-prizes');
      assert.deepStrictEqual(await b.page.$$eval('.p-prize-list li', (ls) => ls.map((l) => l.textContent)), ['#150 π', '#230 π', '#320 π']);
      assert.match(await b.page.textContent('.p-prizes'), /at least 5 closed trades are paid in Pi/);
      assert.deepStrictEqual(b.errors, []);
      await on.ctx.close();
    });

    await t.test('Arabic is drawn right to left', async () => {
      const ar = table('ar');
      const { ctx } = await context(browser, { locale: 'ar' });
      const { page, errors } = await open(ctx, `${base}/pi`);
      assert.strictEqual(await page.getAttribute('html', 'dir'), 'rtl');
      assert.ok((await page.textContent('#root')).includes(ar('Beat the Claw')));
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});
