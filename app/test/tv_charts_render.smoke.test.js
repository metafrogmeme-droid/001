'use strict';
/**
 * The TradingView charts render where the page puts them. Driven in Chromium,
 * because the two defects the first draft of this slice had were both
 * GEOMETRY and invisible to anything but a browser:
 *
 *   * The library lays its chart out as a `<table>`, and a signal's chart sits
 *     inside a collapsed Signals row whose `td { display: flex }` and
 *     `td::before { content: attr(data-label) }` reached into it. The price axis
 *     wrapped BELOW the pane, and every chart in the list carried a stray label.
 *     The claim is that every canvas the chart draws sits inside its host.
 *   * The embed board's CSP refuses inline styles, and the library injects a
 *     `<style>` for its logo. The claim is that the board draws its charts and
 *     the browser reports no violation, under the route's own header.
 *
 * Needs a Chromium binary. Where there is none it SKIPS and says so -- a skip
 * is not a pass, and preflight on a box with the browser runs it for real.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
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

const HOUR = 3600e3;
function candles(n, base, step) {
  const out = []; let p = base;
  const t0 = Date.now() - n * HOUR;
  for (let i = 0; i < n; i++) {
    const o = p; p = p * (1 + Math.sin(i / 6) * step);
    out.push([String(t0 + i * HOUR), String(o), String(Math.max(o, p) * 1.003),
      String(Math.min(o, p) * 0.997), String(p), '1200']);
  }
  return out;
}

const SIGNALS = { signals: [{
  symbol: 'LINK/USDT', direction: 'LONG', confidence: 0.62,
  entry_price: 15.0885, stop_loss: 14.271, take_profit: 16.3148, rr: 1.5,
  status: 'NEW', outcome: null, signal_key: 'sig-link-1', pattern: 'Double Bottom',
  created_at: new Date(Date.now() - 18 * 60e3).toISOString(),
}] };

async function serve(withEmbed) {
  const app = express();
  // The browser asks for one on its own, and the bare static root has none.
  app.get('/favicon.ico', (q, r) => r.status(204).end());
  if (withEmbed) {
    app.get('/api/signals', (q, r) => r.json(SIGNALS));
    app.get('/api/market/candles/:s', (q, r) => r.json({ data: candles(60, 14.5, 0.01) }));
    app.use('/embed', require('../routes/embed'));
  }
  app.get('/dashboard', (req, res) => res.sendFile(path.join(PUBLIC, 'dashboard.html')));
  app.use(express.static(PUBLIC));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function dashboardPage(browser, base, viewport) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
  await ctx.route('**/api/**', (route) => {
    const u = new URL(route.request().url());
    const body = u.pathname.startsWith('/api/signals') ? SIGNALS
      : u.pathname.startsWith('/api/market/candles') ? { data: candles(120, 13.6, 0.006) }
      : { ok: true, data: {}, rows: [], items: [] };
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await ctx.route('**/api/stream*', (r) => r.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.abort());
  const page = await ctx.newPage();
  return { ctx, page };
}

/** Every canvas under `host` against the host's own box, in page pixels. */
function canvasesInside(page, hostSel) {
  return page.$eval(hostSel, (host) => {
    const h = host.getBoundingClientRect();
    const cs = [...host.querySelectorAll('canvas')].map((c) => c.getBoundingClientRect());
    const stray = [...host.querySelectorAll('td')].map((td) => getComputedStyle(td, '::before').content)
      .filter((c) => c && c !== 'none' && c !== 'normal' && c !== '""');
    return {
      host: { w: h.width, h: h.height },
      n: cs.length,
      outside: cs.filter((c) => c.width > 0 && (c.left < h.left - 1 || c.right > h.right + 1
        || c.top < h.top - 1 || c.bottom > h.bottom + 1)).length,
      // The price axis is a canvas of its own, to the RIGHT of the pane on the
      // same row. Wrapped, it sat BELOW the pane, the width of the cell.
      beside: (() => {
        const pane = cs.reduce((a, c) => (c.width * c.height > a.width * a.height ? c : a), cs[0] || { width: 0, height: 0 });
        return cs.some((c) => c !== pane && Math.abs(c.top - pane.top) < 1
          && c.left >= pane.right - 1 && c.height >= pane.height - 1);
      })(),
      stray,
    };
  });
}

for (const [name, viewport] of [['phone', { width: 412, height: 900 }], ['desk', { width: 1280, height: 900 }]]) {
  test(`a signal row's chart is a TradingView chart that fits its slot (${name})`,
    SKIP ? { skip: SKIP } : {}, async (t) => {
      const { server, base } = await serve(false);
      const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
      try {
        const { page } = await dashboardPage(browser, base, viewport);
        const errs = [];
        page.on('pageerror', (e) => errs.push(String(e)));
        await page.goto(`${base}/dashboard#signals`, { waitUntil: 'load' });
        await page.waitForSelector('#c-stream td[data-label="Signal"] .sc-tv canvas', { timeout: 15000 });
        await page.waitForTimeout(400);
        const m = await canvasesInside(page, '#c-stream td[data-label="Signal"] .sc-tv');
        t.diagnostic(`host ${Math.round(m.host.w)}x${Math.round(m.host.h)} · ${m.n} canvases`);
        assert.ok(m.n >= 2, 'the chart drew fewer canvases than a pane and a price axis');
        assert.equal(m.outside, 0, `${m.outside} canvas(es) drawn outside the chart's own box -- a host table's layout reached into the chart`);
        assert.equal(m.beside, true, 'the price axis is not beside the pane -- it wrapped onto a row of its own');
        assert.deepEqual(m.stray, [], `the collapsed row's labels are printed inside the chart: ${m.stray}`);
        assert.equal(await page.$('#c-stream td[data-label="Signal"] svg.sc'), null,
          'the SVG fallback drew although the library loaded');
        assert.deepEqual(errs, []);
      } finally {
        await browser.close();
        server.close();
      }
    });
}

test('the symbol modal draws the decision picture as a TradingView chart, with its legend',
  SKIP ? { skip: SKIP } : {}, async (t) => {
    const { server, base } = await serve(false);
    const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
    try {
      const { page } = await dashboardPage(browser, base, { width: 412, height: 900 });
      const errs = [];
      page.on('pageerror', (e) => errs.push(String(e)));
      await page.goto(`${base}/dashboard#signals`, { waitUntil: 'load' });
      await page.waitForSelector('#c-stream td[data-label="Signal"] b', { timeout: 15000 });
      await page.click('#c-stream td[data-label="Signal"] b');
      await page.waitForSelector('#symChart canvas', { timeout: 15000 });
      await page.waitForTimeout(400);
      const m = await canvasesInside(page, '#symChart');
      t.diagnostic(`modal chart ${Math.round(m.host.w)}x${Math.round(m.host.h)} · ${m.n} canvases`);
      assert.ok(m.host.h >= 200, `the modal chart is ${Math.round(m.host.h)}px tall -- its height was not set`);
      assert.equal(m.outside, 0, 'a canvas is drawn outside the modal chart');
      assert.equal(m.beside, true, 'the price axis wrapped below the pane');
      const legend = await page.$eval('#symChart .tvc-legend', (el) => el.textContent.trim());
      assert.match(legend, /LINK/, `the legend does not name the symbol: "${legend}"`);
      assert.equal(await page.$('#symChart svg.rc-chart'), null, 'the SVG fallback drew although the library loaded');
      assert.deepEqual(errs, []);
    } finally {
      await browser.close();
      server.close();
    }
  });

test('the embed board draws its charts under its own CSP, with no violation',
  SKIP ? { skip: SKIP } : {}, async () => {
    const { server, base } = await serve(true);
    const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
    try {
      const page = await (await browser.newContext({ viewport: { width: 412, height: 800 } })).newPage();
      const errs = [];
      page.on('pageerror', (e) => errs.push(String(e)));
      page.on('console', (msg) => {
        if (msg.type() === 'error' || /Content Security Policy/i.test(msg.text())) errs.push(msg.text() + ' @' + (msg.location().url || ''));
      });
      page.on('response', (r) => { if (r.status() >= 400) errs.push(`${r.status()} ${new URL(r.url()).pathname}`); });
      const res = await page.goto(`${base}/embed/signals`, { waitUntil: 'load' });
      assert.match(res.headers()['content-security-policy'] || '', /style-src/,
        'the board was served without its CSP -- this is not measuring the real page');
      await page.waitForSelector('.e-chart .sc-tv canvas', { timeout: 15000 });
      await page.waitForTimeout(400);
      const m = await canvasesInside(page, '.e-chart .sc-tv');
      assert.equal(m.outside, 0, 'a canvas is drawn outside the embed chart');
      assert.ok(await page.$('a[href="https://www.tradingview.com/"]'), 'the board carries no TradingView attribution');
      assert.deepEqual(errs, [], `the board raised: ${errs.join(' | ')}`);
    } finally {
      await browser.close();
      server.close();
    }
  });

test('the live chart page a Telegram signal links to draws the signal on a TradingView chart, under its CSP',
  SKIP ? { skip: SKIP } : {}, async (t) => {
    const { server, base } = await serve(true);
    const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
    try {
      const page = await (await browser.newContext({ viewport: { width: 412, height: 800 } })).newPage();
      const errs = [];
      const asked = [];
      page.on('pageerror', (e) => errs.push(String(e)));
      page.on('console', (msg) => {
        if (msg.type() === 'error' || /Content Security Policy/i.test(msg.text())) errs.push(msg.text());
      });
      page.on('request', (r) => { if (r.url().includes('/api/market/candles/')) asked.push(new URL(r.url())); });
      page.on('response', (r) => { if (r.status() >= 400) errs.push(`${r.status()} ${new URL(r.url()).pathname}`); });
      const res = await page.goto(`${base}/embed/chart?s=LINKUSDT&tf=4h&e=15.0885&sl=14.271&tp=16.3148&d=LONG`,
        { waitUntil: 'load' });
      assert.match(res.headers()['content-security-policy'] || '', /style-src/,
        'the page was served without its CSP -- this is not measuring the real page');
      await page.waitForSelector('#e-ch-chart canvas', { timeout: 15000 });
      await page.waitForTimeout(400);
      const m = await canvasesInside(page, '#e-ch-chart');
      t.diagnostic(`live chart ${Math.round(m.host.w)}x${Math.round(m.host.h)} · ${m.n} canvases`);
      assert.ok(m.host.h >= 280, `the chart is ${Math.round(m.host.h)}px tall -- its height was not set`);
      assert.equal(m.outside, 0, 'a canvas is drawn outside the chart');
      assert.equal(m.beside, true, 'the price axis wrapped below the pane');
      assert.equal(await page.$('#e-ch-chart svg'), null, 'the SVG fallback drew although the library loaded');
      // The candles asked for are the link's market on the link's timeframe.
      assert.equal(asked[0].pathname, '/api/market/candles/LINKUSDT');
      assert.equal(asked[0].searchParams.get('granularity'), '4h');
      const levels = await page.$eval('.e-lv', (el) => el.textContent);
      assert.match(levels, /15\.0885/);
      assert.match(levels, /14\.271/);
      assert.match(levels, /16\.3148/);
      assert.ok(await page.$('a[href="https://www.tradingview.com/"]'), 'the page carries no TradingView attribution');
      assert.deepEqual(errs, [], `the page raised: ${errs.join(' | ')}`);
    } finally {
      await browser.close();
      server.close();
    }
  });

test('a chart link that names no market says so and asks the candle route nothing',
  SKIP ? { skip: SKIP } : {}, async () => {
    const { server, base } = await serve(true);
    const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
    try {
      const page = await (await browser.newContext({ viewport: { width: 412, height: 800 } })).newPage();
      const asked = [];
      const errs = [];
      page.on('pageerror', (e) => errs.push(String(e)));
      page.on('request', (r) => { if (r.url().includes('/api/market/candles/')) asked.push(r.url()); });
      await page.goto(`${base}/embed/chart?s=%3Cimg%3E&tf=4h`, { waitUntil: 'load' });
      await page.waitForSelector('#root .e-state', { timeout: 15000 });
      const text = await page.$eval('#root', (el) => el.textContent);
      assert.match(text, /does not name a market/);
      assert.deepEqual(asked, []);
      assert.equal(await page.$('#root img'), null, 'the symbol reached the page as markup');
      assert.deepEqual(errs, []);
    } finally {
      await browser.close();
      server.close();
    }
  });
