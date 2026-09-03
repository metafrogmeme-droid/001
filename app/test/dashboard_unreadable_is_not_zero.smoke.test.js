'use strict';
/**
 * With every API answering 503, no dashboard panel may show a zero-like
 * figure outside an error state.
 *
 * The repo's rule is "unreadable is never zero, and absent is never a
 * measurement". Every panel has a test for its own shape; this is the rule
 * checked end to end, in the real page, under total failure: any "$0.00",
 * "0 positions", "+0.00%" that is not sitting inside a state-block is a
 * measurement the page never made. Its first run found the Hub strip saying
 * "0 positions carried" and "0 alerts armed" beside a Mode tile that said
 * "—" for the same failed read.
 *
 * Under total failure any zero is a claim from nothing, so there is no
 * allowlist: a legitimate zero can only come from data, and no data arrives.
 * Same skip rule as the other smokes: no Chromium, a printed SKIP.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const PUBLIC = process.env.RC_SMOKE_PUBLIC || path.join(__dirname, '..', 'public');
const ZERO = /(\$0\.00|\$0\b|(^|[^\d.])0\.0%|(^|[^\d.])0%|\b0 open\b|\b0 positions?\b|\b0 trades?\b|\+0\.00%|\b0 signals?\b|\b0 alerts?\b)/i;

function findChromium() {
  const cands = [process.env.RC_SMOKE_CHROMIUM].filter(Boolean);
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try { for (const d of fs.readdirSync(root)) if (/^chromium-\d+$/.test(d)) cands.push(path.join(root, d, 'chrome-linux', 'chrome')); } catch (e) { /* none */ }
  return cands.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }) || null;
}
let pw = null; try { pw = require('playwright-core'); } catch (e) { pw = null; }
const CHROMIUM = findChromium();
const SKIP = !pw ? 'playwright-core is not installed' : !CHROMIUM ? 'no Chromium binary found' : null;

function viewIds() {
  const src = fs.readFileSync(path.join(PUBLIC, 'js', 'dashboard.js'), 'utf8');
  const i = src.indexOf('const VIEWS = [');
  return [...src.slice(i, src.indexOf('];', i)).matchAll(/id:\s*'([a-z-]+)'/g)].map((m) => m[1]);
}

test('under total API failure no dashboard panel shows a zero outside an error state', SKIP ? { skip: SKIP } : {}, async (t) => {
  const ids = viewIds();
  const app = express();
  app.get('/dashboard', (req, res) => res.sendFile(path.join(PUBLIC, 'dashboard.html')));
  app.use(express.static(PUBLIC));
  const server = http.createServer(app); await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
  const findings = []; const errors = [];
  try {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
    await ctx.route('**/api/**', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: '{"ok":false,"error":"unavailable"}' }));
    await ctx.route('**/api/stream*', (route) => route.abort());
    await ctx.route(/\/(mcp|\.well-known)(\/|$|\?)/, (route) => route.fulfill({ status: 503, body: '{}' }));
    await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
    const page = await ctx.newPage();
    page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
    await page.goto(`${base}/dashboard#home`, { waitUntil: 'load' });
    for (const id of ids) {
      await page.evaluate((v) => { location.hash = '#' + v; }, id);
      await page.waitForTimeout(900);
      const panels = await page.$$eval('section.panel, .panel', (els) => els.map((el) => ({
        id: el.id || (el.querySelector('[id]') || {}).id || '?',
        honest: !!el.querySelector('.state-block, .skel'),
        text: (el.innerText || '').replace(/\s+/g, ' ').slice(0, 400),
      })));
      for (const p of panels) {
        const m = p.text.match(ZERO);
        if (!p.honest && m) findings.push(`${id} / ${p.id}: "${m[0]}" in: ${p.text.slice(0, 140)}`);
      }
      t.diagnostic(`view ${id}: ${findings.length} finding(s) so far`);
    }
  } finally { await browser.close(); server.close(); }
  assert.deepStrictEqual(errors, [], 'page errors under total failure:\n  ' + errors.join('\n  '));
  assert.deepStrictEqual(findings, [], 'zero-like claims from reads that never happened:\n  ' + findings.join('\n  '));
});
