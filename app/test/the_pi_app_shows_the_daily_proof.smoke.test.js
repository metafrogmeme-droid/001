'use strict';
/**
 * The daily proof in the real Pi app (/pi), in Chromium: the header's Proof
 * button opens it beside About, the bar keeps its five tabs, each day shows
 * its quorum badge, a badge the server would not give is "not checked", the
 * view links nowhere, and it speaks the reader's language.
 */
const test = require('node:test');
const assert = require('node:assert');
const H = require('./helpers/pi_app_harness');

const ROOT = 'b'.repeat(64);
const ROOTS = { construction: 'x', chains: [], roots: [
  { day: '2026-10-21', root: ROOT, seal_count: 42, anchors: [] },
  { day: '2026-10-20', root: ROOT, seal_count: 17, anchors: [] },
] };
const HELD = { day: '2026-10-21', root: ROOT, status: 'multi_anchored', k: 2, groups: ['bitcoin', 'ethereum'], unreadable: [], pending: [],
  mismatched: [], meaning: 'time-proven, not performance',
  anchors: [{ chain: 'base', chain_name: 'Base', status: 'verified', counts_toward_quorum: true }], bitcoin: { status: 'verified' } };
const json = (route, status, body) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
const routes = {
  '/api/roots': (route) => json(route, 200, ROOTS),
  '/api/roots/quorum/2026-10-21': (route) => json(route, 200, HELD),
  '/api/roots/quorum/2026-10-20': (route) => json(route, 429, { error: 'Too many requests' }),
};

test('the daily proof in the Pi app', H.SKIP ? { skip: H.SKIP } : {}, async (t) => {
  const { server, base } = await H.serve();
  const browser = await H.pw.chromium.launch({ executablePath: H.CHROMIUM, args: ['--no-sandbox'] });
  try {
    await t.test('the header opens it; each day shows its badge; nothing links out', async () => {
      const { ctx, sent } = await H.context(browser, { routes });
      const { page, errors } = await H.open(ctx, `${base}/pi`);
      await H.signInAndSettle(page);
      assert.strictEqual(await page.$('.p-tab-b[data-tab="proof"]'), null, 'the bar keeps its five tabs');
      await H.showTab(page, 'proof');
      const panel = '.p-panel[data-tab="proof"]';
      await page.waitForFunction((sel) => /Held by 2 independent chains/.test(document.querySelector(sel).textContent), panel);
      const rows = await page.$$eval(`${panel} .p-proof-row`, (els) => els.map((e) => e.textContent));
      assert.strictEqual(rows.length, 2);
      assert.match(rows[0], /2026-10-21.*42 seals.*Held by 2 independent chains.*On: Base, Bitcoin/);
      assert.match(rows[1], /2026-10-20.*17 seals.*Not checked yet/, 'a refused ask is not read, never "not anchored"');
      assert.strictEqual(await page.$eval(`${panel} .p-proof-row .p-proof-q`, (e) => e.className), 'p-proof-q tone-up');
      assert.strictEqual(await page.$(`${panel} a`), null, 'the view links nowhere');
      const quorumAsks = sent.filter((s) => s.path.startsWith('/api/roots/quorum/')).map((s) => s.path);
      assert.deepStrictEqual(quorumAsks, ['/api/roots/quorum/2026-10-21', '/api/roots/quorum/2026-10-20']);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });
    await t.test('in the reader\'s language', async () => {
      const { ctx } = await H.context(browser, { routes, locale: 'de-DE' });
      const { page } = await H.open(ctx, `${base}/pi`);
      await H.signInAndSettle(page);
      await H.showTab(page, 'proof');
      const panel = '.p-panel[data-tab="proof"]';
      await page.waitForFunction((sel) => /Gehalten von 2 unabhängigen Chains/.test(document.querySelector(sel).textContent), panel);
      assert.match(await page.textContent(panel), /Täglicher Nachweis/);
      assert.match(await page.textContent('.p-off-b[data-tab="proof"]'), /Nachweis/);
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});
