'use strict';
/**
 * While the $RCLAW presale is live, every page carries a slim bar that says so, shows when it
 * closes and links to /token; it steps aside everywhere else.
 *
 * Driven in Chromium against the real pages, with /api/token answering the real record and the
 * page's clock pinned, because the bar is a reading of the record's sale window against the
 * clock (presale.js presalePhase) and a test that asked the real clock would say something else
 * on each day of the sale. Measured, not inferred: the bar is the body's first element, it fits
 * a phone without sideways scroll, closing it keeps it closed for a day (not the whole sale,
 * which is how the operator lost sight of it on 2026-10-16), and it is absent before the sale,
 * after it, on /token (which says all of it), and when the record cannot be read.
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

function findChromium() {
  const cands = [process.env.RC_SMOKE_CHROMIUM].filter(Boolean);
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try { for (const d of fs.readdirSync(root)) if (/^chromium-\d+$/.test(d)) cands.push(path.join(root, d, 'chrome-linux', 'chrome')); } catch (e) { /* none */ }
  return cands.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }) || null;
}
let pw = null; try { pw = require('playwright-core'); } catch (e) { pw = null; }
const CHROMIUM = findChromium();
const SKIP = !pw ? 'playwright-core is not installed' : !CHROMIUM ? 'no Chromium binary found' : null;

/** The pages that carry the bar: every page with the i18n runtime but the two account flows. */
const NO_BAR_PAGES = new Set(['reset.html', 'wallet-link.html']);
const PAGES = fs.readdirSync(PUBLIC)
  .filter((f) => f.endsWith('.html') && /src="\/js\/i18n-core\.js/.test(fs.readFileSync(path.join(PUBLIC, f), 'utf8')))
  .sort();

async function serve() {
  const app = express(); app.use(express.static(PUBLIC));
  const server = http.createServer(app); await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function context(browser, { width = 1280, now = LIVE, token = RECORD, status = 200, errorBody = null } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height: 800 } });
  await ctx.route('**/api/**', (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/api/token') {
      return route.fulfill({ status, contentType: 'application/json',
        body: JSON.stringify(status === 200 ? token : (errorBody || { error: 'token_record_unreadable', exception: 'Error' })) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, data: {}, rows: [], items: [] }) });
  });
  await ctx.route('**/api/stream*', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
  await ctx.route(/\/(mcp|\.well-known)(\/|$|\?)/, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
  ctx.now = now;
  return ctx;
}

async function open(ctx, base, page) {
  const p = await ctx.newPage();
  await p.clock.setFixedTime(new Date(ctx.now));
  // The bar arrives after one fetch, so a page that carries presale.js is not open until
  // /api/token has answered. `load` plus a fixed beat measured the page BEFORE the bar on a
  // loaded box (agent.html, 17 Oct, two suites running) and read "no bar" for a bar that
  // arrived a moment later. The beat after the answer is the same one a visitor gets.
  const carriesBar = /src="\/js\/presale\.js/.test(fs.readFileSync(path.join(PUBLIC, page), 'utf8'));
  const answered = carriesBar
    ? p.waitForResponse((r) => new URL(r.url()).pathname === '/api/token', { timeout: 15000 })
    : Promise.resolve(null);
  await p.goto(`${base}/${page}`, { waitUntil: 'load' });
  await answered;
  await p.waitForFunction(() => document.readyState === 'complete');
  await p.waitForTimeout(300);
  return p;
}

function barFacts() {
  const bar = document.getElementById('presale-bar');
  if (!bar) return null;
  const box = bar.getBoundingClientRect();
  const hit = document.elementFromPoint(Math.min(box.left + 24, innerWidth - 1), box.top + box.height / 2);
  return {
    // At the top of the page and drawn, not covered by a fixed header laid over it.
    top: Math.round(box.top),
    seen: !!hit && bar.contains(hit) && box.height > 0,
    text: bar.querySelector('.pb-text') && bar.querySelector('.pb-text').textContent,
    time: bar.querySelector('time') && bar.querySelector('time').textContent,
    datetime: bar.querySelector('time') && bar.querySelector('time').getAttribute('datetime'),
    href: bar.querySelector('.pb-cta') && bar.querySelector('.pb-cta').getAttribute('href'),
    close: !!bar.querySelector('button.pb-close[aria-label]'),
    overflow: document.documentElement.scrollWidth - innerWidth,
    barRight: bar.getBoundingClientRect().right,
    width: innerWidth,
  };
}

test('every page with the i18n runtime loads the bar, and before token-page.js where both are', () => {
  for (const f of PAGES) {
    const html = fs.readFileSync(path.join(PUBLIC, f), 'utf8');
    const has = /<script src="\/js\/presale\.js\?v=\d+"><\/script>/.test(html);
    assert.strictEqual(has, !NO_BAR_PAGES.has(f), f);
    if (html.includes('/js/token-page.js')) {
      assert.ok(has && html.indexOf('/js/presale.js') < html.indexOf('/js/token-page.js'), `${f}: token-page.js reads the phase from presale.js`);
    }
  }
});

test('the record answers the bar the window the sale was created with', () => {
  assert.strictEqual(Presale.presalePhase(RECORD.token.presale, Date.parse(LIVE)), 'live');
  assert.strictEqual(Presale.presalePhase(RECORD.token.presale, Date.parse('2026-10-14T00:00:00Z')), 'announced');
  assert.strictEqual(Presale.presalePhase(RECORD.token.presale, Date.parse('2026-10-29T14:00:00Z')), 'ended');
});

test('while the sale is live the bar is at the top of every page that carries it, and seen', SKIP ? { skip: SKIP } : {}, async () => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM });
  try {
    for (const width of [1280, 390]) {
      const ctx = await context(browser, { width });
      for (const f of PAGES.filter((x) => !NO_BAR_PAGES.has(x) && x !== 'token.html')) {
        const p = await open(ctx, base, f);
        const facts = await p.evaluate(barFacts);
        assert.ok(facts, `${f} @${width}: no bar while the sale is live`);
        assert.strictEqual(facts.top, 0, `${f} @${width}: the bar is not at the top of the page`);
        assert.ok(facts.seen, `${f} @${width}: the bar is covered or not drawn`);
        assert.strictEqual(facts.text, Presale.BAR_TEXT, f);
        assert.strictEqual(facts.time, '2026-10-29 14:00 UTC', f);
        assert.strictEqual(facts.datetime, RECORD.token.presale.closes_at, f);
        assert.strictEqual(facts.href, '/token', f);
        assert.ok(facts.close, `${f}: no labelled close button`);
        assert.ok(facts.barRight <= facts.width + 1, `${f} @${width}: the bar runs off the screen`);
        await p.close();
      }
      // At a phone's width the bar itself adds no sideways scroll: measured against the same
      // page with no bar (a page that already scrolls sideways is its own defect).
      if (width === 390) {
        const quiet = await context(browser, { width, now: '2026-11-01T00:00:00Z' });
        for (const f of ['index.html', 'dashboard.html', 'arena.html', 'docs.html']) {
          const withBar = await (await open(ctx, base, f)).evaluate(barFacts);
          const without = await (await open(quiet, base, f)).evaluate(() => document.documentElement.scrollWidth - innerWidth);
          assert.ok(withBar.overflow <= Math.max(0, without), `${f}: the bar adds ${withBar.overflow - without}px of sideways scroll`);
        }
        await quiet.close();
      }
      await ctx.close();
    }
  } finally {
    await browser.close();
    server.close();
  }
});

test('closed, it stays closed for a day; it is absent on /token, outside the window and when the record cannot be read', SKIP ? { skip: SKIP } : {}, async () => {
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM });
  // A close stored the way the page stores it, `when` ms after LIVE: what a returning visitor's
  // browser holds.
  const closedAt = (closesAt, ms) => `${closesAt}|${Date.parse(LIVE) + ms}`;
  const holding = async (opts, value) => {
    const c = await context(browser, opts);
    await c.addInitScript(([k, v]) => { try { localStorage.setItem(k, v); } catch (e) { /* none */ } },
      [Presale.DISMISS_KEY, value]);
    return c;
  };
  const DAY = Presale.DISMISS_MS;
  try {
    const ctx = await context(browser);
    const p = await open(ctx, base, 'index.html');
    assert.ok(await p.evaluate(barFacts));
    await p.click('#presale-bar .pb-close');
    assert.strictEqual(await p.evaluate(barFacts), null, 'the bar did not close');
    assert.strictEqual(await p.evaluate((k) => localStorage.getItem(k), Presale.DISMISS_KEY),
      closedAt(RECORD.token.presale.closes_at, 0));
    const again = await open(ctx, base, 'arena.html');
    assert.strictEqual(await again.evaluate(barFacts), null, 'a closed bar came back on the next page');
    await ctx.close();

    // Both arms of the day. On 2026-10-16 one tap had hidden the bar for the whole sale.
    const hour = new Date(Date.parse(LIVE) + DAY - 3600e3).toISOString();
    const later = new Date(Date.parse(LIVE) + DAY + 60e3).toISOString();
    const stored = closedAt(RECORD.token.presale.closes_at, 0);
    const within = await holding({ now: hour }, stored);
    assert.strictEqual(await (await open(within, base, 'index.html')).evaluate(barFacts), null,
      'a close an hour short of a day ago did not hold');
    await within.close();
    const past = await holding({ now: later }, stored);
    assert.ok(await (await open(past, base, 'index.html')).evaluate(barFacts), 'a close a day ago still hid the bar');
    await past.close();
    // A close kept in the old shape (the closing time alone) hid the whole sale; it has expired.
    const legacy = await holding({}, RECORD.token.presale.closes_at);
    assert.ok(await (await open(legacy, base, 'index.html')).evaluate(barFacts), 'a close in the old shape still hid the bar');
    await legacy.close();
    // A close stamped in the future (a clock set back since) is not a close.
    const ahead = await holding({}, closedAt(RECORD.token.presale.closes_at, 3600e3));
    assert.ok(await (await open(ahead, base, 'index.html')).evaluate(barFacts), 'a close from the future hid the bar');
    await ahead.close();
    // A later sale is a different window: the bar shows again.
    const next = await holding({ now: '2026-12-10T00:00:00Z',
      token: { token: { ...RECORD.token, presale: { ...RECORD.token.presale,
        opens_at: '2026-12-01T14:00:00Z', closes_at: '2026-12-15T14:00:00Z' } } } },
    `${RECORD.token.presale.closes_at}|${Date.parse('2026-12-10T00:00:00Z') - 60e3}`);
    assert.ok(await (await open(next, base, 'index.html')).evaluate(barFacts), 'closing one sale hid the next');
    await next.close();

    const live = await context(browser);
    assert.strictEqual(await (await open(live, base, 'token.html')).evaluate(barFacts), null, '/token says all of it');
    await live.close();
    for (const now of ['2026-10-15T13:59:00Z', '2026-10-29T14:00:00Z', '2026-11-30T00:00:00Z']) {
      const c = await context(browser, { now });
      assert.strictEqual(await (await open(c, base, 'index.html')).evaluate(barFacts), null, now);
      await c.close();
    }
    const down = await context(browser, { status: 503 });
    assert.strictEqual(await (await open(down, base, 'index.html')).evaluate(barFacts), null, 'an unread record put up a bar');
    await down.close();
    // Only a 200 is a read: an error status whose body still carries a live record (a stale copy
    // a proxy kept) is not one.
    const stale = await context(browser, { status: 503, errorBody: RECORD });
    assert.strictEqual(await (await open(stale, base, 'index.html')).evaluate(barFacts), null, 'a non-200 was read as the record');
    await stale.close();
    const blind = await context(browser, { token: { token: { ...RECORD.token,
      presale: { ...RECORD.token.presale, closes_at: null } } } });
    assert.strictEqual(await (await open(blind, base, 'index.html')).evaluate(barFacts), null, 'a bar with no closing time');
    await blind.close();
  } finally {
    await browser.close();
    server.close();
  }
});

test('the bar speaks fourteen languages, with its time in digits', () => {
  const i18n = require('../public/js/i18n.js');
  const EN = { 'home.token_presale_live': Presale.BAR_TEXT, 'bar.presale_cta': Presale.BAR_CTA,
    'bar.presale_closes': Presale.BAR_CLOSES, 'dd.dc_close': Presale.BAR_CLOSE };
  for (const [key, en] of Object.entries(EN)) {
    assert.strictEqual(i18n.translate(key, 'en'), en, key);
    for (const l of i18n.LANGS) assert.ok((i18n.translate(key, l.code) || '').trim(), `${key} ${l.code}`);
  }
  // The markup names each key on its element, so a language switch translates the bar in place.
  const html = Presale.barHtml(RECORD.token.presale);
  for (const key of ['home.token_presale_live', 'bar.presale_cta', 'bar.presale_closes', 'aria-label:dd.dc_close']) {
    assert.ok(html.includes(key), key);
  }
  assert.ok(html.includes('>2026-10-29 14:00 UTC</time>'));
});
