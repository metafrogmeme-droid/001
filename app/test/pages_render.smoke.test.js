'use strict';
/**
 * Every page in app/public loads in a real browser without a runtime error,
 * signed out and signed in. The dashboard smoke walks that page's views;
 * this one covers the other thirty-odd pages the site serves, each with the
 * same shaped API stubs. It fails on an uncaught page error or a
 * console.error. (Panels in a generic error state are reported as
 * diagnostics only here: a page-level throw is the class of defect nothing
 * else catches, and the per-panel judgement lives with each page's tests.)
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const PUBLIC = process.env.RC_SMOKE_PUBLIC || path.join(__dirname, '..', 'public');
const FIXTURES = require(process.env.RC_SMOKE_FIXTURES || './fixtures/dashboard_smoke_fixtures.js');
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

const ONLY = (process.env.RC_SMOKE_PAGES || '').split(',').filter(Boolean);
const PAGES = fs.readdirSync(PUBLIC).filter((f) => f.endsWith('.html') && !/^(embed|partial|_)/.test(f) && (!ONLY.length || ONLY.includes(f))).sort();

async function serve() {
  const app = express(); app.use(express.static(PUBLIC));
  const server = http.createServer(app); await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

test('every page loads in Chromium without a runtime error, signed out and signed in', SKIP ? { skip: SKIP } : {}, async (t) => {
  if (!ONLY.length) assert.ok(PAGES.length >= 20, `expected the site's pages, got ${PAGES.length}`);
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
  const problems = [];
  try {
    for (const signedIn of [false, true]) {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
      if (signedIn) await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
      await ctx.route('**/api/**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixtureFor(new URL(route.request().url()).pathname)) }));
      await ctx.route('**/api/stream*', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
      // Dynamic non-/api endpoints the real server mounts (the MCP surface,
      // .well-known manifests): under this static server they would 404 and
      // read as page errors. Answer them with an empty document.
      await ctx.route(/\/(mcp|\.well-known)(\/|$|\?)/, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
      await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
      for (const pg of PAGES) {
        const page = await ctx.newPage(); const errs = [];
        page.on('pageerror', (e) => errs.push(`pageerror: ${e.message} ${String(e.stack || '').split('\n').slice(1, 3).join(' | ')}`));
        page.on('console', (m) => { if (m.type() === 'error') errs.push(`console.error: ${m.text().slice(0, 200)}`); });
        try { await page.goto(`${base}/${pg}`, { waitUntil: 'load', timeout: 20000 }); await page.waitForTimeout(600); }
        catch (e) { errs.push(`navigation: ${e.message.slice(0, 160)}`); }
        const generic = await page.$$eval('.state-block p[data-i18n="dd.err_panel"]', (ps) => ps.length).catch(() => 0);
        t.diagnostic(`${signedIn ? 'in ' : 'out'} ${pg}: ${errs.length} error(s), ${generic} generic panel error(s)`);
        for (const e of errs) problems.push(`[${signedIn ? 'signed-in' : 'signed-out'}] ${pg}: ${e}`);
        await page.close();
      }
      await ctx.close();
    }
  } finally { await browser.close(); server.close(); }
  assert.deepStrictEqual(problems, [], 'runtime errors while loading pages:\n  ' + problems.join('\n  '));
});
