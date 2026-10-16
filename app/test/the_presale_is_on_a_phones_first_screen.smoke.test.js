'use strict';
/**
 * While the $RCLAW presale is live, a phone's first screen of the home page says so, with the
 * bar up and with the bar closed.
 *
 * 2026-10-16, reported from the live site with a screenshot: "Dont see the presale banner". The
 * live record said the sale was open and the live presale.js was the repository's, byte for
 * byte; a fresh visit drew the bar. What the operator's phone showed was the home page with no
 * presale on its first screen, which is what any visitor sees who has closed the bar:
 *   - closing the bar hid it for the whole two-week sale (presale.js now keeps a close a day);
 *   - the home page's own presale line sat under the signup note, below the fold of a phone.
 * The line is now the hero's first, and this pins it to the first screen.
 *
 * Driven in Chromium against the real page, with /api/token answering the real record and the
 * page's clock pinned inside the sale window, at the reporting phone's width and the narrowest
 * common one. Needs a Chromium binary; where there is none it SKIPS and says so.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const rclaw = require('../lib/rclaw_token');
const Presale = require('../public/js/presale.js');

const PUBLIC = path.join(__dirname, '..', 'public');
const RECORD = { token: rclaw.publicRecord(rclaw.readRecord()) };
const LIVE = '2026-10-20T00:00:00Z';
// A phone's first screen is what is left under the status bar and the browser's own chrome:
// the reporting phone is 412 CSS px wide and shows about 780 of page; the narrow one, 360 x 640.
const PHONES = [{ width: 412, height: 780 }, { width: 360, height: 640 }];

function findChromium() {
  const cands = [process.env.RC_SMOKE_CHROMIUM].filter(Boolean);
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try { for (const d of fs.readdirSync(root)) if (/^chromium-\d+$/.test(d)) cands.push(path.join(root, d, 'chrome-linux', 'chrome')); } catch (e) { /* none */ }
  return cands.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }) || null;
}
let pw = null; try { pw = require('playwright-core'); } catch (e) { pw = null; }
const CHROMIUM = findChromium();
const SKIP = !pw ? 'playwright-core is not installed' : !CHROMIUM ? 'no Chromium binary found' : null;

async function serve() {
  const app = express();
  app.use(express.static(PUBLIC));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function home(browser, base, viewport, { closed = false, query = '' } = {}) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 2 });
  await ctx.route('**/api/**', (route) => {
    const p = new URL(route.request().url()).pathname;
    const body = p === '/api/token' ? RECORD : { ok: true, data: {}, rows: [], items: [] };
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await ctx.route('**/api/stream*', (r) => r.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.abort());
  if (closed) {
    await ctx.addInitScript(([k, v]) => { try { localStorage.setItem(k, v); } catch (e) { /* none */ } },
      [Presale.DISMISS_KEY, `${RECORD.token.presale.closes_at}|${Date.parse(LIVE) - 60e3}`]);
  }
  const page = await ctx.newPage();
  await page.clock.setFixedTime(new Date(LIVE));
  await page.goto(`${base}/${query}`, { waitUntil: 'load' });
  // The strip's sentence turns to "live" once token-page.js has read the record: wait for that,
  // so the measurement is of the line a visitor reads, not the markup's first draft.
  await page.waitForFunction(() => {
    const t = document.getElementById('hero-token-text');
    return t && /live now/.test(t.textContent);
  }, null, { timeout: 10000 });
  await page.waitForTimeout(300);
  const m = await page.evaluate(() => {
    const strip = document.querySelector('.hero-token');
    const r = strip.getBoundingClientRect();
    const hit = document.elementFromPoint(Math.min(r.left + 12, innerWidth - 1), r.top + r.height / 2);
    return {
      bar: !!document.getElementById('presale-bar'),
      top: r.top, bottom: r.bottom, right: r.right, vh: innerHeight, vw: innerWidth,
      seen: !!hit && strip.contains(hit),
      text: strip.textContent.trim(),
      href: strip.getAttribute('href'),
      beforeH1: !!(strip.compareDocumentPosition(document.querySelector('.hero h1')) & Node.DOCUMENT_POSITION_FOLLOWING),
    };
  });
  await ctx.close();
  return m;
}

for (const vp of PHONES) {
  test(`on a ${vp.width}x${vp.height} phone the first screen names the live presale, bar up or closed`,
    SKIP ? { skip: SKIP } : {}, async (t) => {
      const { server, base } = await serve();
      const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
      try {
        for (const closed of [false, true]) {
          const m = await home(browser, base, vp, { closed });
          t.diagnostic(`closed=${closed} bar=${m.bar} strip ${Math.round(m.top)}-${Math.round(m.bottom)} of ${m.vh}`);
          // The two states are the two this test is about: the bar up, and the bar closed.
          assert.strictEqual(m.bar, !closed, `closed=${closed}: the bar state is not the one measured`);
          assert.ok(m.bottom <= m.vh, `closed=${closed}: the presale line ends at ${Math.round(m.bottom)}px, `
            + `below a ${m.vh}px first screen`);
          assert.ok(m.seen, `closed=${closed}: the presale line is covered`);
          assert.ok(m.right <= m.vw + 1, `closed=${closed}: the presale line runs off the screen`);
          assert.match(m.text, /presale is live now/, `closed=${closed}: the line does not say the sale is live`);
          assert.strictEqual(m.href, '/token');
          assert.ok(m.beforeH1, 'the presale line is not above the headline');
        }
        // A visitor who arrives on an invite link gets one more line in the hero; it goes under
        // the presale line, not above it.
        const invited = await home(browser, base, vp, { closed: true, query: '?ref=001' });
        assert.ok(invited.bottom <= invited.vh, `an invite link pushed the presale line below the first screen`);
      } finally {
        await browser.close();
        server.close();
      }
    });
}
