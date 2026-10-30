'use strict';
/**
 * The bot's own dashboard pages take their token in a form when prompt() cannot.
 *
 * bot/web/dashboard.html and bot/web/performance_chart.html took DASHBOARD_TOKEN from the URL
 * fragment, then localStorage, then window.prompt(). An app window that embeds the page (Pi
 * Desktop's SoloHost tab, where scripts/solohost/ runs the bot) can block or stub prompt(), and
 * the page then polled on with no token and nothing on screen saying why. A refused token was
 * worse: the dashboard showed "Disconnected" forever, the stale token still saved.
 *
 * Driven in Chromium against the real files, both arms: prompt answered (no form, the token is
 * sent), prompt dismissed and prompt throwing (the form appears; what is typed is sent after the
 * reload), and a refused token (dropped, and the form says it was refused).
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const WEB = path.join(__dirname, '..', '..', 'bot', 'web');
const PAGES = [
  { file: 'dashboard.html', api: '/api/state' },
  { file: 'performance_chart.html', api: '/api/performance' },
];

function findChromium() {
  const cands = [process.env.RC_SMOKE_CHROMIUM].filter(Boolean);
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try { for (const d of fs.readdirSync(root)) if (/^chromium-\d+$/.test(d)) cands.push(path.join(root, d, 'chrome-linux', 'chrome')); } catch (e) { /* none */ }
  return cands.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }) || null;
}
let pw = null; try { pw = require('playwright-core'); } catch (e) { pw = null; }
const CHROMIUM = findChromium();
const SKIP = !pw ? 'playwright-core is not installed' : !CHROMIUM ? 'no Chromium binary found' : null;

const GOOD = 'the-right-token';

/** A stand-in for the bot's aiohttp API. The page posts the token to /session
 *  once; the cookie, not a header, is what later /api calls carry. */
async function serve() {
  const app = express();
  const seen = [];
  app.use(express.json());
  app.post('/session', (req, res) => {
    const token = String((req.body && req.body.token) || '');
    seen.push({ path: '/session', token });
    if (token !== GOOD) return res.status(401).json({ error: 'unauthorized' });
    res.setHeader('Set-Cookie', `dashboard_auth=${encodeURIComponent(token)}; Path=/; HttpOnly; SameSite=Lax`);
    return res.json({ ok: true });
  });
  app.use('/api', (req, res) => {
    const cookie = req.get('cookie') || '';
    seen.push({ path: req.path, cookie, auth: req.get('authorization') || '' });
    const ok = cookie.includes(`dashboard_auth=${encodeURIComponent(GOOD)}`);
    if (!ok) return res.status(401).json({ error: 'unauthorized' });
    return res.json({ portfolio: {}, risk: {}, engine: {}, positions: [], trades: [], symbols: [], points: [] });
  });
  app.use(express.static(WEB));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, seen, base: `http://127.0.0.1:${server.address().port}` };
}

async function open(browser, base, file, { dialog, init, stored } = {}) {
  const ctx = await browser.newContext();
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
  const page = await ctx.newPage();
  if (stored) await page.addInitScript((t) => { if (!sessionStorage.getItem('seeded')) { localStorage.setItem('dashboard_token', t); sessionStorage.setItem('seeded', '1'); } }, stored);
  if (init) await page.addInitScript(init);
  page.on('dialog', (d) => (dialog === undefined ? d.dismiss() : d.accept(dialog)));
  await page.goto(`${base}/${file}`, { waitUntil: 'load' });
  await page.waitForTimeout(400);
  return { ctx, page };
}

const formShown = (page) => page.evaluate(() => !!document.getElementById('tokenForm'));
const lastApi = (seen, api) => [...seen].reverse().find((s) => s.path === api.replace('/api', ''));
const sessionPosts = (seen) => seen.filter((s) => s.path === '/session');

test('the dashboard pages take their token where prompt() cannot', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { server, seen, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, args: ['--no-sandbox'] });
  try {
    for (const { file, api } of PAGES) {
      await t.test(`${file}: a token typed at the prompt is sent, and no form appears`, async () => {
        const { ctx, page } = await open(browser, base, file, { dialog: GOOD });
        assert.strictEqual(await formShown(page), false);
        assert.ok((lastApi(seen, api) || {}).cookie.includes(`dashboard_auth=${GOOD}`),
          'the API call did not carry the session cookie');
        assert.strictEqual((lastApi(seen, api) || {}).auth, '',
          'the token must not travel in a header a script assembled');
        assert.strictEqual(await page.evaluate(() => localStorage.getItem('dashboard_token')), null);
        await ctx.close();
      });

      for (const [how, opts] of [
        ['dismissed', {}],
        ['blocked', { init: () => { window.prompt = () => { throw new Error('blocked by the host'); }; } }],
      ]) {
        await t.test(`${file}: prompt ${how}, the form appears and what is typed is sent`, async () => {
          const { ctx, page } = await open(browser, base, file, opts);
          assert.strictEqual(await formShown(page), true, 'no way in was offered');
          await page.fill('#tokenInput', GOOD);
          await Promise.all([page.waitForNavigation({ waitUntil: 'load' }), page.click('#tokenForm button[type=submit]')]);
          await page.waitForTimeout(400);
          assert.strictEqual(await page.evaluate(() => localStorage.getItem('dashboard_token')), null,
            'the token was written where a script can read it');
          assert.strictEqual(await formShown(page), false);
          assert.ok((lastApi(seen, api) || {}).cookie.includes(`dashboard_auth=${GOOD}`));
          await ctx.close();
        });
      }

      await t.test(`${file}: a refused token is dropped and the form says so`, async () => {
        const { ctx, page } = await open(browser, base, file, { stored: 'a-stale-token' });
        assert.ok(sessionPosts(seen).some((s) => s.token === 'a-stale-token'),
          'the stale token was not offered to /session');
        assert.ok(!(lastApi(seen, api) || {}).cookie || !(lastApi(seen, api).cookie.includes(`dashboard_auth=${GOOD}`)),
          'a refused token became a session');
        assert.strictEqual(await formShown(page), true, 'a refused token left no way back in');
        assert.match(await page.textContent('#tokenNote'), /refused/);
        assert.strictEqual(await page.evaluate(() => localStorage.getItem('dashboard_token')), null);
        await ctx.close();
      });
    }
  } finally {
    await browser.close();
    server.close();
  }
});
