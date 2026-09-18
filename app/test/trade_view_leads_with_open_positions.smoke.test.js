'use strict';
/**
 * The Trade view leads with the caller's OPEN POSITIONS, in a real browser.
 *
 * Reported from the live site: the signed-in Trade view opened with YOUR
 * TRADING AUTHORITY — a custody SETUP form you fill in once — and buried
 * Open positions at the very bottom, under the ticket, the sizer and a
 * Decision picture that says "Enter a symbol" until you type one. So the
 * panel describing money that is already at risk was the last thing on the
 * page, and the first thing was a form most sessions never touch.
 *
 * Order is a claim about priority, and the order here is now:
 *
 *   open positions -> ticket + sizer -> decision picture -> trading authority
 *
 * live state, then the primary action, then context, then setup.
 *
 * WHY THIS IS A DRIVE AND NOT A SCAN. The panels sit in one template literal,
 * so `indexOf` would "work" — and that is exactly the guard this repo already
 * replaced once. A source scan cannot see `${LOGGED_IN ? ... : ''}` around a
 * panel, so a mutation that stops MOUNTING one leaves every id in the source,
 * in order, and the scan stays green while the browser renders nothing there
 * (`dashboard_views_render.smoke.test.js` records that exact hole). The DOM is
 * the only place the question can be asked.
 *
 * AND THE SECOND TEST IS WHY THE FIRST ONE IS NOT ENOUGH. `.state-block` is a
 * centred column with `--s6` padding — fine mid-page, a ~300px hero at the
 * TOP. Raising Open positions while leaving that would push the order ticket
 * FURTHER down than it was before the fix, for every user holding nothing,
 * which is most new accounts. `.panel--lead` collapses it to one row. The
 * assertion is the geometry a reader actually gets (how far the ticket sits
 * below the panel that now precedes it), never the CSS spelling: a previous
 * guard in this repo pinned `margin-right: auto` and `getComputedStyle`
 * resolved it to a used pixel value, so the assertion checked a spelling
 * rather than the claim.
 *
 * Needs a Chromium binary; SKIPS where there is none, and a skip is not a pass.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const PUBLIC = process.env.RC_SMOKE_PUBLIC || path.join(__dirname, '..', 'public');
const FIXTURES = require('./fixtures/dashboard_smoke_fixtures.js');

function fixtureFor(pathname) {
  for (const [prefix, body] of FIXTURES) {
    if (pathname === prefix || pathname.startsWith(prefix + '/') || pathname.startsWith(prefix + '?')) return body;
  }
  return { ok: true, data: {}, rows: [], items: [] };
}

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
  : !CHROMIUM ? 'no Chromium binary found (set PLAYWRIGHT_BROWSERS_PATH or RC_SMOKE_CHROMIUM)'
    : null;

async function serve() {
  const app = express();
  app.get('/dashboard', (req, res) => res.sendFile(path.join(PUBLIC, 'dashboard.html')));
  app.use(express.static(PUBLIC));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

/** Open the signed-in Trade view. `positions` replaces the portfolio fixture's
 *  own list, because the empty state and the populated one are different
 *  claims and the shared fixture only ever exercises one of them. */
async function openTrade(browser, base, positions, viewport) {
  const ctx = await browser.newContext({ viewport: viewport || { width: 412, height: 915 } });
  // rc_auth=1 is what app.js reads for LOGGED_IN — without it the Trade view
  // renders its signed-out PREVIEW branch, which has none of these panels.
  await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
  await ctx.route('**/api/**', (route) => {
    const u = new URL(route.request().url());
    const fx = fixtureFor(u.pathname);
    const body = (u.pathname === '/api/portfolio')
      ? Object.assign({}, fx, { open_positions: positions })
      : fx;
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await ctx.route('**/api/stream*', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
  const page = await ctx.newPage();
  await page.goto(`${base}/dashboard#trade`, { waitUntil: 'load' });
  await page.waitForSelector('#p-tpos', { timeout: 15000 });
  await page.waitForTimeout(900);            // let the panel loaders settle
  return page;
}

const HELD = [{
  symbol: 'BTC/USDT', direction: 'LONG', entry_price: 60000, current_price: 60500,
  size_usd: 100, pnl: 0.83, pnl_pct: 0.83, stop_loss: 59000, take_profit: 63000,
  opened_at: '2026-09-02T00:00:00Z',
}];

test('the Trade view leads with open positions, then the ticket, with setup last',
  SKIP ? { skip: SKIP } : {}, async (t) => {
    const { server, base } = await serve();
    const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
    try {
      const page = await openTrade(browser, base, HELD);

      // Read the ORDER OFF THE DOCUMENT, not off the source: every panel must
      // be present AND mounted, and a panel that renders nowhere has no y.
      const tops = await page.evaluate((ids) => {
        const out = {};
        for (const id of ids) {
          const el = document.getElementById(id);
          out[id] = el ? el.getBoundingClientRect().top + window.scrollY : null;
        }
        return out;
      }, ['p-tpos', 'p-ticket', 'p-sizer', 'p-tinsight', 'p-authority']);

      for (const [id, y] of Object.entries(tops)) {
        assert.ok(y != null, `#${id} is not in the document — it is not enough for the id to be in the source`);
      }

      assert.ok(tops['p-tpos'] < tops['p-ticket'],
        `open positions must lead the Trade view: p-tpos at ${tops['p-tpos']}, p-ticket at ${tops['p-ticket']}`);
      assert.ok(tops['p-ticket'] < tops['p-tinsight'],
        'the order ticket comes before the decision picture');
      assert.ok(tops['p-tinsight'] < tops['p-authority'],
        `the custody SETUP form is last — it was first, above live money: p-tinsight at ${tops['p-tinsight']}, p-authority at ${tops['p-authority']}`);

      // The position really rendered; an empty panel would satisfy the order
      // assertions above while saying nothing about the caller's book.
      const posText = await page.$eval('#c-tpos', (el) => el.textContent || '');
      assert.match(posText, /BTC/, 'the leading panel must show the held position, not an empty state');
      await page.context().close();
    } finally {
      await browser.close();
      server.close();
    }
  });

test('holding nothing, the leading panel is one row — the ticket is not pushed down by a hero-sized empty state',
  SKIP ? { skip: SKIP } : {}, async (t) => {
    const { server, base } = await serve();
    const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
    try {
      const page = await openTrade(browser, base, []);

      const m = await page.evaluate(() => {
        const tpos = document.getElementById('p-tpos');
        const ticket = document.getElementById('p-ticket');
        const block = document.querySelector('#c-tpos .state-block');
        const icon = block && block.querySelector('.icon');
        const p = block && block.querySelector('p');
        const r = (el) => (el ? el.getBoundingClientRect() : null);
        return {
          gap: r(ticket).top - r(tpos).top,
          panelH: r(tpos).height,
          hasBlock: !!block,
          iconMidY: icon ? r(icon).top + r(icon).height / 2 : null,
          textMidY: p ? r(p).top + r(p).height / 2 : null,
          text: p ? (p.textContent || '') : '',
        };
      });

      assert.ok(m.hasBlock, 'the empty state must still be rendered — this is about its SHAPE, not its removal');
      assert.match(m.text, /No open positions/i, 'and it must still say what it means');

      // The claim: the ticket sits close under a panel with nothing in it.
      // Unstacked, `.state-block`'s centred column with --s6 padding puts this
      // near 300px, which is the whole reason the reorder needed the modifier.
      assert.ok(m.gap < 220,
        `the order ticket is ${Math.round(m.gap)}px below the top of an EMPTY positions panel — a hero-sized empty state pushes the primary action further down than it was before the reorder`);
      assert.ok(m.panelH < 190,
        `an empty leading panel is ${Math.round(m.panelH)}px tall; it should read as a line, not a hero`);

      // A row, not a column — asserted as geometry (icon and sentence share a
      // line) rather than by reading a CSS property back, which pins a
      // spelling instead of the claim.
      assert.ok(Math.abs(m.iconMidY - m.textMidY) < 12,
        `the icon and the sentence must sit on one line: icon mid ${m.iconMidY}, text mid ${m.textMidY}`);
      await page.context().close();
    } finally {
      await browser.close();
      server.close();
    }
  });
