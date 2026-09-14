'use strict';
/**
 * Render every dashboard view in a real browser and fail on a runtime error.
 *
 * 2026-09-03: a helper declared in one view and called from another shipped
 * to production. `node --check` passed (valid syntax), the seam test passed
 * (it ran the function's text in a VM), preflight was green, and the Account
 * view's yield panel threw ReferenceError on every render. Nothing in the
 * suite RAN the page. This does: headless Chromium, the real bundle, the
 * APIs stubbed to minimal JSON, every view in VIEWS switched to in turn.
 *
 * What fails it: an uncaught page error, a console.error, or a panel that
 * fell into renderPanel's GENERIC error state ("Couldn't load this panel"),
 * which is what a loader that threw looks like. Empty states are fine --
 * stubbed data is empty by design. Named failure states (sign in, offline)
 * are fine too; they are honest answers to the stubs.
 *
 * Needs a Chromium binary. Where there is none (a CI runner without
 * browsers) it SKIPS and says so in the TAP output -- a skip is not a pass,
 * and preflight on a box with the browser runs it for real.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const PUBLIC = process.env.RC_SMOKE_PUBLIC || path.join(__dirname, '..', 'public');
const BUNDLE_OVERRIDE = process.env.RC_SMOKE_DASHBOARD_JS || null;   // serve another dashboard.js (to prove the test bites)

function findChromium() {
  const cands = [process.env.RC_SMOKE_CHROMIUM, process.env.PLAYWRIGHT_CHROMIUM].filter(Boolean);
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try {
    for (const d of fs.readdirSync(root)) {
      if (/^chromium-\d+$/.test(d)) cands.push(path.join(root, d, 'chrome-linux', 'chrome'));
    }
  } catch (e) { /* no browsers dir */ }
  return cands.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }) || null;
}

let pw = null;
try { pw = require('playwright-core'); } catch (e) { pw = null; }
const CHROMIUM = findChromium();
const SKIP = !pw ? 'playwright-core is not installed' : !CHROMIUM ? 'no Chromium binary found (set PLAYWRIGHT_BROWSERS_PATH or RC_SMOKE_CHROMIUM)' : null;

// Minimal but SHAPED responses. Empty stubs only exercise every panel's
// empty branch -- the shipped defect this smoke exists for lived in the
// yield panel's populated branch, which `{data:{}}` never reaches.
const FIXTURES = require('./fixtures/dashboard_smoke_fixtures.js');
function fixtureFor(pathname) {
  for (const [prefix, body] of FIXTURES) if (pathname === prefix || pathname.startsWith(prefix + '/') || pathname.startsWith(prefix + '?')) return body;
  return { ok: true, data: {}, rows: [], items: [] };
}

function viewIds() {
  const src = fs.readFileSync(path.join(PUBLIC, 'js', 'dashboard.js'), 'utf8');
  const i = src.indexOf('const VIEWS = [');
  const block = src.slice(i, src.indexOf('];', i));
  return [...block.matchAll(/id:\s*'([a-z-]+)'/g)].map((m) => m[1]);
}

async function serve() {
  const app = express();
  if (BUNDLE_OVERRIDE) app.get('/js/dashboard.js', (req, res) => res.type('application/javascript').send(fs.readFileSync(BUNDLE_OVERRIDE, 'utf8')));
  app.get('/dashboard', (req, res) => res.sendFile(path.join(PUBLIC, 'dashboard.html')));
  app.use(express.static(PUBLIC));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

test('every dashboard view renders in Chromium without a runtime error', SKIP ? { skip: SKIP } : {}, async (t) => {
  const ids = viewIds();
  assert.ok(ids.length >= 15, `expected the VIEWS list, got ${ids.length}`);
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
  const errors = [];
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
    // Playwright consults routes newest-first: the generic stub is registered
    // FIRST so the specific ones below win.
    await ctx.route('**/api/**', (route) => {
      const u = new URL(route.request().url());
      const fx = fixtureFor(u.pathname);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fx) });
    });
    // The live stream: an open, empty event-stream, so EventSource neither
    // errors on the MIME type nor reconnects in a loop.
    await ctx.route('**/api/stream*', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
    await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());   // no third-party fetches
    const page = await ctx.newPage();
    page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}\n      ${String(e.stack || '').split('\n').slice(1, 4).join('\n      ')}`));
    page.on('console', (m) => { if (m.type() === 'error') errors.push(`console.error: ${m.text()}`); });
    await page.goto(`${base}/dashboard#home`, { waitUntil: 'load' });

    for (const id of ids) {
      await page.evaluate((v) => { location.hash = '#' + v; }, id);
      await page.waitForTimeout(700);
      const generic = await page.$$eval('.state-block p[data-i18n="dd.err_panel"]',
        (ps) => ps.map((p) => (p.closest('[id]') || {}).id || '?'));
      if (generic.length) errors.push(`view ${id}: panel(s) in the generic error state (a loader threw): ${generic.join(', ')}`);
      t.diagnostic(`view ${id}: ${generic.length} generic panel error(s), ${errors.length} problem(s) so far`);
    }
  } finally {
    await browser.close();
    server.close();
  }
  assert.deepStrictEqual(errors, [], 'runtime errors while rendering the dashboard:\n  ' + errors.join('\n  '));
});

// REACHABILITY, which no source scan can see. The home shell is a template
// string, and `${LOGGED_IN ? \`<section id="p-metrics">…\` : ''}` mutated to
// `${false ? …}` keeps the id in the SOURCE, in order, so every scan that
// pins the deck's panel order stays green while the browser renders nothing
// there. This asks the DOM: the deck's account panels exist, in the order the
// layout guards pin, and each holds its RENDERED component rather than the
// skeleton or the error state.
test('the signed-in home renders the deck\'s account panels, in order, each with its component', SKIP ? { skip: SKIP } : {}, async () => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
    await ctx.route('**/api/**', (route) => {
      const u = new URL(route.request().url());
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixtureFor(u.pathname)) });
    });
    await ctx.route('**/api/stream*', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
    await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
    const page = await ctx.newPage();
    await page.goto(`${base}/dashboard#home`, { waitUntil: 'load' });
    await page.waitForSelector('#c-metrics .mcl', { timeout: 8000 });
    await page.waitForSelector('#c-ctx .ctxrow', { timeout: 8000 });
    const seen = await page.$$eval('.stack > section.panel[id]', (els) => els.map((e) => e.id));
    const deck = ['p-mode', 'p-hero', 'p-metrics', 'p-cmd', 'p-ctx'];
    assert.deepStrictEqual(seen.filter((id) => deck.includes(id)), deck, `deck panels in DOM order; saw ${seen.join(', ')}`);
    assert.ok(await page.$('#c-mode .ms-strip'), 'the status strip rendered');
    assert.ok(await page.$('#c-metrics .mcl'), 'the metric cluster rendered');
    // The context row read the fixture's scan: five subjects, each a chip,
    // and nothing named as not reported.
    const ctxKeys = await page.$$eval('#c-ctx .ctxrow .ctx-k', (els) => els.map((e) => e.textContent.trim()));
    assert.deepStrictEqual(ctxKeys, ['TICK', 'VENUE', 'REGIME', 'MACRO', 'GATE'], `the row's chips, saw ${JSON.stringify(ctxKeys)}`);
    assert.strictEqual(await page.$('#c-ctx .skel'), null, 'the row is past its skeleton');
    assert.strictEqual(await page.$('#c-ctx .state-block'), null, 'the row is not in an error state');
    assert.strictEqual(await page.$('#c-metrics .skel'), null, 'the cluster is past its skeleton');
    assert.strictEqual(await page.$('#c-metrics .state-block'), null, 'the cluster is not in an error state');
    // And the cluster read the fixture: three cells, a figure in each.
    const cells = await page.$$eval('#c-metrics .v', (els) => els.map((e) => e.textContent.trim()));
    assert.strictEqual(cells.length, 3, `three cells, saw ${JSON.stringify(cells)}`);
    assert.ok(cells.every((t) => /\d/.test(t)), `every cell shows a figure: ${JSON.stringify(cells)}`);
  } finally {
    await browser.close();
    server.close();
  }
});

// THE SIGNED-OUT HOME, driven. The context row's shell is deliberately not
// gated on LOGGED_IN, so its loader has to sit OUTSIDE the signed-in block of
// renderHome — placed inside it, every signed-out visitor gets a permanently
// animating skeleton under a heading that announced itself, which is neither
// the empty state nor the error state, and which no scan of loaders can see,
// because the loader exists. Only the browser can say whether it ran.
test('the signed-out home renders the context row, not a skeleton under its heading', SKIP ? { skip: SKIP } : {}, async () => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    // No rc_auth cookie and no token: LOGGED_IN is false.
    await ctx.route('**/api/**', (route) => {
      const u = new URL(route.request().url());
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixtureFor(u.pathname)) });
    });
    await ctx.route('**/api/stream*', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
    await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
    const page = await ctx.newPage();
    await page.goto(`${base}/dashboard#home`, { waitUntil: 'load' });
    await page.waitForSelector('#c-ctx .ctxrow', { timeout: 8000 });
    const seen = await page.$$eval('.stack > section.panel[id]', (els) => els.map((e) => e.id));
    for (const gated of ['p-mode', 'p-metrics', 'p-cmd']) assert.ok(!seen.includes(gated), `${gated} is a signed-in panel; saw ${seen.join(', ')}`);
    assert.deepStrictEqual(seen.filter((id) => ['p-hero', 'p-ctx', 'p-next'].includes(id)), ['p-hero', 'p-ctx', 'p-next'], `the row sits under the hero; saw ${seen.join(', ')}`);
    assert.strictEqual(await page.$('#c-ctx .skel'), null, 'the signed-out row is past its skeleton');
    assert.strictEqual(await page.$('#c-ctx .state-block'), null, 'the signed-out row is not in an error state');
    const ctxKeys = await page.$$eval('#c-ctx .ctxrow .ctx-k', (els) => els.map((e) => e.textContent.trim()));
    assert.deepStrictEqual(ctxKeys, ['TICK', 'VENUE', 'REGIME', 'MACRO', 'GATE'], `the row's chips, saw ${JSON.stringify(ctxKeys)}`);
  } finally {
    await browser.close();
    server.close();
  }
});

test('the smoke reports itself as SKIPPED, never as passed, when it cannot run', () => {
  // A skip is visible in TAP as "# SKIP <reason>"; this test exists so the
  // reason is printed even when the run above is skipped.
  if (SKIP) console.log(`# SMOKE SKIPPED: ${SKIP}`);
  assert.ok(true);
});
