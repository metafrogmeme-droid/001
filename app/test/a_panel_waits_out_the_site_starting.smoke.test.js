'use strict';
/**
 * The dashboard waits out the website's own start by itself, in a real browser.
 *
 * Reported 15 October: every panel on the home view said "The website's
 * database is not ready yet" under a Retry button, and the topbar said ENGINE
 * STATUS UNKNOWN. The page's 2.3 s of retries had run out against an instance
 * still starting, and every panel then waited for a person.
 *
 * And a person's Retry could not have fixed the home view's biggest panels:
 * the context row, the hero and the positions panel await the ONE read the
 * view starts for their endpoint, so retrying them awaited the same failed
 * read again. Now a failed shared read is read afresh on the next await
 * (app.js sharedRead).
 *
 * Driven here with every /api/ call answering the gate's `starting` until the
 * panels have said so, and its fixture after (a switch, not a clock, so a slow
 * machine cannot skip the starting phase):
 *  - the panels say the site is starting, then fill in with no click, the
 *    shared reads included, and the topbar stops saying STATUS UNKNOWN;
 * and the other arm, a database that is failing (reason db_unreachable)
 * throughout:
 *  - the panels say so at once, with Retry, and fetch nothing more by
 *    themselves.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const PUBLIC = path.join(__dirname, '..', 'public');
const FIXTURES = require('./fixtures/dashboard_smoke_fixtures.js');
function fixtureFor(pathname) {
  for (const [prefix, body] of FIXTURES) if (pathname === prefix || pathname.startsWith(prefix + '/') || pathname.startsWith(prefix + '?')) return body;
  return { ok: true, data: {}, rows: [], items: [] };
}
function findChromium() {
  const cands = [process.env.RC_SMOKE_CHROMIUM].filter(Boolean);
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try { for (const d of fs.readdirSync(root)) if (/^chromium-\d+$/.test(d)) cands.push(path.join(root, d, 'chrome-linux', 'chrome')); } catch (e) { /* none */ }
  return cands.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }) || null;
}
let pw = null; try { pw = require('playwright-core'); } catch (e) { pw = null; }
const CHROMIUM = findChromium();
const SKIP = !pw ? 'playwright-core is not installed' : !CHROMIUM ? 'no Chromium binary found' : null;

// The home view's panels that consume a read the view shares (dashboard.js
// renderHome): the context row (/scan), the hero (/portfolio), the positions
// panel (/positions).
const SHARED = ['ctx', 'hero', 'hpos'];

async function serve() {
  const app = express();
  app.get('/dashboard', (req, res) => res.sendFile(path.join(PUBLIC, 'dashboard.html')));
  app.use(express.static(PUBLIC));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

/** A context whose /api/ answers the gate's 503 while `refusing(path)` says so. */
async function context(browser, base, refusing, reason) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
  const calls = [];
  await ctx.route('**/api/**', (route) => {
    const p = new URL(route.request().url()).pathname;
    calls.push(p);
    if (refusing(p)) {
      return route.fulfill({ status: 503, contentType: 'application/json', headers: { 'Retry-After': '15' },
        body: JSON.stringify({ error: 'starting', reason }) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixtureFor(p)) });
  });
  await ctx.route('**/api/stream*', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
  return { ctx, calls };
}

function state() {
  const q = (sel) => document.querySelectorAll(sel).length;
  const loaded = {};
  for (const id of ['ctx', 'hero', 'hpos']) {
    const el = document.getElementById('c-' + id);
    loaded[id] = !!(el && el.dataset.rcLoaded === '1');
  }
  const chip = document.getElementById('connChip');
  return {
    auto: q('[data-i18n="dd.err_starting_auto"]'),
    plain: q('[data-i18n="dd.err_starting"]'),
    generic: q('[data-i18n="dd.err_panel"]'),
    loaded,
    chip: chip ? chip.textContent : null,
  };
}

test('the home view waits out a starting instance by itself, shared reads included', SKIP ? { skip: SKIP } : {}, async () => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
  try {
    let starting = true;
    const { ctx, calls } = await context(browser, base, () => starting, 'starting');
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(`${base}/dashboard#home`, { waitUntil: 'load' });
    // While it starts, the panels say so, and say they will try again.
    await page.waitForFunction((ids) => ids.every((id) => {
      const el = document.getElementById('c-' + id);
      return el && el.querySelector('[data-i18n="dd.err_starting_auto"]');
    }), SHARED, { timeout: 30000 });
    const during = await page.evaluate(state);
    assert.ok(during.auto >= SHARED.length, JSON.stringify(during));
    const sharedBefore = calls.length;
    // The instance is ready. Then, with no click, every panel fills in.
    starting = false;
    await page.waitForFunction((ids) => ids.every((id) => {
      const el = document.getElementById('c-' + id);
      return el && el.dataset.rcLoaded === '1';
    }) && !document.querySelector('[data-i18n="dd.err_starting_auto"]'), SHARED, { timeout: 45000 }).catch(() => {});
    const after = await page.evaluate(state);
    assert.deepStrictEqual(after.loaded, { ctx: true, hero: true, hpos: true },
      `the shared-read panels did not fill in: ${JSON.stringify(after)}`);
    assert.deepStrictEqual([after.auto, after.plain, after.generic], [0, 0, 0], JSON.stringify(after));
    assert.ok(after.chip && !/UNKNOWN/.test(after.chip), `the topbar still says ${after.chip}`);
    // The shared reads were read again, which is the only way they could fill.
    for (const p of ['/api/bot/sync/scan', '/api/portfolio', '/api/positions']) {
      assert.ok(calls.slice(sharedBefore).includes(p), `${p} was never read again`);
    }
    assert.deepStrictEqual(errors, []);
    await ctx.close();
  } finally { await browser.close(); server.close(); }
});

test('a failing database is said at once, with Retry, and the panels do not keep fetching', SKIP ? { skip: SKIP } : {}, async () => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
  try {
    const { ctx, calls } = await context(browser, base, () => true, 'db_unreachable');
    const page = await ctx.newPage();
    await page.goto(`${base}/dashboard#home`, { waitUntil: 'load' });
    await page.waitForFunction((ids) => ids.every((id) => {
      const el = document.getElementById('c-' + id);
      return el && el.querySelector('[data-i18n="dd.err_starting"]');
    }), SHARED, { timeout: 30000 });
    const settled = calls.length;
    // Longer than the first two automatic waits put together.
    await page.waitForTimeout(6500);
    const s = await page.evaluate(state);
    assert.strictEqual(s.auto, 0, 'a failing database is not "starting up"');
    assert.ok(s.plain >= SHARED.length, JSON.stringify(s));
    for (const id of SHARED) {
      assert.ok(await page.$(`#c-${id} button[data-i18n="dd.retry"]`), `${id} offers Retry`);
    }
    const extra = calls.slice(settled).filter((p) => ['/api/bot/sync/scan', '/api/portfolio', '/api/positions'].includes(p));
    assert.deepStrictEqual(extra, [], 'the panels fetched again by themselves');
    // And a person's Retry does read again (the door the button names works).
    const before = calls.filter((p) => p === '/api/bot/sync/scan').length;
    await page.click('#c-ctx button[data-i18n="dd.retry"]');
    for (let i = 0; i < 50 && calls.filter((p) => p === '/api/bot/sync/scan').length <= before; i += 1) {
      await page.waitForTimeout(100);
    }
    assert.ok(calls.filter((p) => p === '/api/bot/sync/scan').length > before, 'Retry on the context row read nothing');
    await ctx.close();
  } finally { await browser.close(); server.close(); }
});
