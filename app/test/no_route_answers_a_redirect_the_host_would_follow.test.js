'use strict';
/**
 * The live host follows redirects itself, so no route answers with one.
 *
 * THE FINDING (live, 2026-10-18; lib/navigate.js has the measurements). The
 * web host answered our 302 to Pi's consent screen with Pi's own page under
 * our address, its assets 404ing on our domain, and answered our 302 to
 * /#oauth_error=… with the home page, the fragment gone. A redirect is a
 * claim that the BROWSER will make the next request; on this host it does
 * not. Every OAuth hop is now a 200 page that navigates (meta refresh and a
 * link), which the host passes through untouched.
 *
 *   The page is a navigation and nothing else: no script, no Location,
 *   no-store, no-referrer, and a target escaped so it cannot leave its
 *   attribute.
 *   A real browser follows it, fragment and all.
 *   No route in the server answers with res.redirect: a new one would be
 *   followed by the host the same way, and no test behind the host would
 *   see it. Scanned, because the host is the one thing no test here runs.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const { codeOnly } = require('./helpers/code_only');
const { navigationTarget } = require('./helpers/navigation');
const { navigate, navigationPage } = require('../lib/navigate');

const APP = path.join(__dirname, '..');

async function serve(handler) {
  const app = express();
  app.get('/go', handler);
  app.get('/landed', (req, res) => res.type('html').send('<!doctype html><title>landed</title><p>landed</p>'));
  const server = await new Promise((r) => { const s = app.listen(0, '127.0.0.1', () => r(s)); });
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

test('the page navigates, carries no script and no Location, and is not cached', async () => {
  const target = 'https://accounts.pinet.com/oauth/authorize?response_type=token&client_id=c&redirect_uri=https%3A%2F%2Fwww.humanoid-traders.com%2Fauth%2Fpi%2Fcallback&scope=username&state=s1';
  const { server, base } = await serve((req, res) => navigate(res, target));
  try {
    const r = await fetch(base + '/go', { redirect: 'manual' });
    assert.strictEqual(await navigationTarget(r), target, 'the target did not survive the page');
  } finally {
    server.close();
  }
});

test('a target cannot leave its attribute', () => {
  const evil = '/#oauth_error=x"><script>alert(1)</script><a href="';
  const html = navigationPage(evil);
  assert.doesNotMatch(html, /<script/i);
  assert.strictEqual((html.match(/<a /g) || []).length, 1);
  assert.strictEqual((html.match(/<meta http-equiv="refresh"/g) || []).length, 1);
  assert.match(html, /content="0; url=\/#oauth_error=x&quot;&gt;&lt;script&gt;/);
});

test('no route in the server answers with res.redirect', () => {
  const files = [
    path.join(APP, 'auth.js'), path.join(APP, 'server.js'),
    ...['routes', 'lib'].flatMap((d) => fs.readdirSync(path.join(APP, d))
      .filter((f) => f.endsWith('.js')).map((f) => path.join(APP, d, f))),
  ];
  const offenders = files.filter((f) => /\bres\.redirect\s*\(/.test(codeOnly(fs.readFileSync(f, 'utf8'))))
    .map((f) => path.relative(APP, f));
  assert.deepStrictEqual(offenders, [],
    'the live host follows a 3xx itself; send a navigation page (lib/navigate.js) instead');
  // Both arms: the scan sees the shape it forbids.
  assert.ok(/\bres\.redirect\s*\(/.test(codeOnly("router.get('/x', (req, res) => res.redirect('/y'));")));
});

// ── in a real browser ─────────────────────────────────────────────────────

function findChromium() {
  const cands = [process.env.RC_SMOKE_CHROMIUM].filter(Boolean);
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try { for (const d of fs.readdirSync(root)) if (/^chromium-\d+$/.test(d)) cands.push(path.join(root, d, 'chrome-linux', 'chrome')); } catch (e) { /* none */ }
  return cands.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }) || null;
}
let pw = null; try { pw = require('playwright-core'); } catch (e) { pw = null; }
const CHROMIUM = findChromium();
const SKIP = !pw ? 'playwright-core is not installed' : !CHROMIUM ? 'no Chromium binary found' : null;

test('a browser follows the page to its target, fragment and all', SKIP ? { skip: SKIP } : {}, async () => {
  const { server, base } = await serve((req, res) => navigate(res, '/landed#oauth=eyJ0IjoxfQ%3D%3D'));
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM });
  try {
    const page = await browser.newPage();
    await page.goto(base + '/go');
    await page.waitForURL((u) => new URL(u).pathname === '/landed', { timeout: 5000 });
    assert.strictEqual(page.url(), base + '/landed#oauth=eyJ0IjoxfQ%3D%3D');
    assert.strictEqual(await page.textContent('p'), 'landed');
  } finally {
    await browser.close();
    server.close();
  }
});
