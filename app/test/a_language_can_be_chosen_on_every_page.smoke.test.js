'use strict';
/**
 * Every page that speaks fourteen languages lets a visitor choose one, on a
 * phone as well as a desktop.
 *
 * Measured in Chromium before this change, at 390px (a common phone): 25 of
 * the 40 pages that load the translations had no language control a visitor
 * could see. Two causes:
 *  - styles.css hid the control under 420px so the sign-up button kept its
 *    room. That is every phone, so the landing page and the dashboard had
 *    no way to change language on one.
 *  - The control mounted only into a page's own slot or the top bar's
 *    `.nav-links`. Twenty pages use the shared `nav.topnav` row or no header
 *    at all, so they never had one; and `.nav-links` is the scroller phones
 *    hide or scroll, so a control placed in it was out of sight there too.
 *
 * Now the control shrinks to a globe on a phone instead of disappearing, and
 * mounts on every page (a slot, the top bar's actions, the shared row, the
 * top bar, or a small fixed corner control). This drives each page in a real
 * browser at two phone widths and a desktop width:
 *  - there is exactly one control;
 *  - a tap at its centre reaches the select (nothing covers it), and what the
 *    visitor sees there is on screen;
 *  - nothing else in the header was pushed off the screen to make room;
 *  - choosing Spanish switches the page to Spanish and the face says so.
 * The page list is every page that loads the translations, read from the
 * files, so a new page is covered without anyone adding it here.
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

const PAGES = fs.readdirSync(PUBLIC)
  .filter((f) => f.endsWith('.html') && /\bjs\/i18n(-core)?\.js/.test(fs.readFileSync(path.join(PUBLIC, f), 'utf8')))
  .sort();

async function serve() {
  const app = express(); app.use(express.static(PUBLIC));
  const server = http.createServer(app); await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}
async function context(browser, width) {
  const ctx = await browser.newContext({ viewport: { width, height: 800 } });
  await ctx.route('**/api/**', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixtureFor(new URL(route.request().url()).pathname)) }));
  await ctx.route('**/api/stream*', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
  await ctx.route(/\/(mcp|\.well-known)(\/|$|\?)/, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
  return ctx;
}

// Runs in the page. What a visitor can reach, measured, not inferred.
function measure() {
  const W = innerWidth;
  const sels = Array.from(document.querySelectorAll('.rc-lang-select'));
  if (sels.length !== 1) return { count: sels.length };
  const sel = sels[0];
  const box = sel.getBoundingClientRect();
  const cx = box.left + box.width / 2; const cy = box.top + box.height / 2;
  const hit = document.elementFromPoint(cx, cy);
  // What is SEEN at that spot: the select itself, or (on a phone) the face
  // the transparent select is laid over.
  const face = sel.parentElement && sel.parentElement.querySelector('.rc-lang-face');
  const shown = getComputedStyle(sel).opacity !== '0' ? sel
    : (face && getComputedStyle(face).display !== 'none' ? face : null);
  const sb = shown ? shown.getBoundingClientRect() : null;
  // Everything else a visitor taps in the same header must still be on
  // screen. `.nav-links` is a scroller by design, so its links may sit past
  // the edge; nothing else may.
  const header = sel.closest('nav, header, .topbar, .rc-lang-float');
  const pushedOff = header ? Array.from(header.querySelectorAll('a, button, summary, select'))
    .filter((e) => !e.closest('.nav-links') && e.checkVisibility())
    .filter((e) => { const b = e.getBoundingClientRect(); return b.width > 0 && (b.right > W + 0.5 || b.left < -0.5); })
    .map((e) => (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30)) : [];
  return {
    count: 1,
    reachable: hit === sel,
    seen: !!(sb && sb.width >= 24 && sb.height >= 24 && sb.left >= -0.5 && sb.right <= W + 0.5 && sb.top >= 0 && sb.bottom <= innerHeight),
    pushedOff,
  };
}

test('every page that loads the translations offers a language a visitor can see and tap', SKIP ? { skip: SKIP } : {}, async (t) => {
  assert.ok(PAGES.length >= 30, `expected the site's translated pages, got ${PAGES.length}`);
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
  const problems = [];
  try {
    for (const width of [360, 390, 1280]) {
      const ctx = await context(browser, width);
      for (const pg of PAGES) {
        const page = await ctx.newPage();
        await page.goto(`${base}/${pg}`, { waitUntil: 'load', timeout: 20000 });
        await page.waitForTimeout(150);
        const m = await page.evaluate(measure);
        if (m.count !== 1) problems.push(`${width}px ${pg}: ${m.count} language controls`);
        else {
          if (!m.reachable) problems.push(`${width}px ${pg}: a tap on the language control lands on something else`);
          if (!m.seen) problems.push(`${width}px ${pg}: the language control is not on screen`);
          if (m.pushedOff.length) problems.push(`${width}px ${pg}: pushed off the screen: ${m.pushedOff.join(', ')}`);
        }
        await page.close();
      }
      await ctx.close();
    }
  } finally { await browser.close(); server.close(); }
  t.diagnostic(`${PAGES.length} pages x 3 widths`);
  assert.deepStrictEqual(problems, [], problems.join('\n'));
});

test('choosing a language on a phone switches the page, on every page', SKIP ? { skip: SKIP } : {}, async () => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
  const problems = [];
  try {
    const ctx = await context(browser, 390);
    for (const pg of PAGES) {
      const page = await ctx.newPage();
      await page.goto(`${base}/${pg}`, { waitUntil: 'load', timeout: 20000 });
      // The fixture can produce the state it names: the page starts in English.
      const before = await page.evaluate(() => document.documentElement.lang);
      if (before !== 'en') problems.push(`${pg}: started in ${before}, not English`);
      await page.selectOption('.rc-lang-select', 'es');
      const after = await page.evaluate(() => ({
        lang: document.documentElement.lang,
        face: (document.querySelector('.rc-lang-face') || {}).textContent,
      }));
      if (after.lang !== 'es') problems.push(`${pg}: choosing Spanish left the page in ${after.lang}`);
      if (after.face !== 'ES') problems.push(`${pg}: the phone face still reads ${after.face}`);
      await page.evaluate(() => { try { localStorage.clear(); } catch (e) { /* ignore */ } });
      await page.close();
    }
    await ctx.close();
  } finally { await browser.close(); server.close(); }
  assert.deepStrictEqual(problems, [], problems.join('\n'));
});
