'use strict';
/**
 * The account-change emails' "Reset your password" button opens the form it
 * names. It points at /#forgot (lib/email_templates, forgot_url), and the
 * landing page's #forgot door switches the sign-in panel to the "Forgot your
 * password?" step. Before the door existed the button would have landed on
 * the home page, one click away from the form it promised.
 *
 * Driven in Chromium, because the claim is what the page shows after load
 * and no source scan can see it. Needs a Chromium binary; where there is none
 * it SKIPS and says so. A skip is not a pass.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const PUBLIC = path.join(__dirname, '..', 'public');

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
const SKIP = !pw ? 'playwright-core is not installed'
  : !CHROMIUM ? 'no Chromium binary found (set PLAYWRIGHT_BROWSERS_PATH or RC_SMOKE_CHROMIUM)' : null;

async function stepsAt(hash) {
  const app = express();
  app.get('/', (req, res) => res.sendFile(path.join(PUBLIC, 'index.html')));
  app.use(express.static(PUBLIC));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
  try {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    await ctx.route('**/api/**', (route) => route.fulfill({
      status: 200, contentType: 'application/json', body: '{}',
    }));
    await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.abort());
    const page = await ctx.newPage();
    await page.goto(`${base}/${hash}`, { waitUntil: 'load' });
    await page.waitForTimeout(500);
    return await page.evaluate(() => ({
      forgot: document.getElementById('step-forgot').classList.contains('active'),
      login: document.getElementById('step-login').classList.contains('active'),
      register: document.getElementById('step-register').classList.contains('active'),
      focused: document.activeElement && document.activeElement.id,
    }));
  } finally {
    await browser.close();
    server.close();
  }
}

test('/#forgot opens the "Forgot your password?" step and focuses its email field', SKIP ? { skip: SKIP } : {}, async () => {
  const s = await stepsAt('#forgot');
  assert.deepStrictEqual([s.forgot, s.login, s.register], [true, false, false], JSON.stringify(s));
  assert.strictEqual(s.focused, 'forgot-email');
});

test('without the door the page opens on its usual step, not the forgot form', SKIP ? { skip: SKIP } : {}, async () => {
  const s = await stepsAt('');
  assert.strictEqual(s.forgot, false, JSON.stringify(s));
  assert.ok(s.login || s.register, JSON.stringify(s));
});
