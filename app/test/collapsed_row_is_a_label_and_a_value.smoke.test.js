'use strict';
/**
 * On a phone, a collapsed table row is a LABEL and a VALUE. Driven in Chromium,
 * because the claim is rendered geometry and no source scan can see it.
 *
 * 2026-09-18, reported from the live site with a screenshot: the Signals card
 * rendered the LONG chip as a capsule the full height of the row (~390px), the
 * pattern name wrapped one or two words per line down seven lines, and the
 * signal sparkline — `width="100%"` over a 260-unit viewBox — was squeezed into
 * a ~60px sliver at the right edge.
 *
 * The cause is one declaration. `.tbl--collapse td { display: flex }` makes
 * every child of the cell a flex ITEM, so a cell carrying block content became
 * a row of narrow columns, and the default `align-items: stretch` sized each of
 * them to the tallest thing in the row. Nothing in the markup was wrong; the
 * cell was being laid out as a label|value pair when its value is a block.
 *
 * Four claims, each stated against the cell rather than against a pixel count
 * somebody has to maintain:
 *   1. a chip does not FILL its cell (stretch is what made the capsule);
 *   2. the chart is given the row's width;
 *   3. the description reads as a paragraph, not a column;
 *   4. the label sits left of the value.
 *
 * Needs a Chromium binary. Where there is none it SKIPS and says so — a skip is
 * not a pass, and preflight on a box with the browser runs it for real.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const PUBLIC = path.join(__dirname, '..', 'public');
const PHONE = { width: 412, height: 900 };            // under the 639px collapse breakpoint

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

// The signal that broke: a real multi-word pattern name, so the wrap is
// exercised. The existing views smoke has no /api/signals fixture, so its
// generic stub renders this panel's EMPTY state and the table never appears.
// Bitget-style [t, o, h, l, c, v]. Without these the sparkline never mounts and
// the width assertion below measures an empty slot — which is how the mutation
// round caught it: capping `.sc` at 60px changed nothing any test could see.
const CANDLES = { data: Array.from({ length: 40 }, (_, i) => {
  const base = 0.36 + i * 0.0012;
  return [String(1_700_000_000_000 + i * 3600e3), base, base + 0.004, base - 0.003, base + 0.002, '1200'];
}) };

const SIGNAL = {
  signals: [{
    symbol: 'TIA/USDT', direction: 'LONG', confidence: 0.57,
    entry_price: 0.3828, stop_loss: 0.3608, take_profit: 0.4158, rr: 1.5,
    status: 'NEW', outcome: null, signal_key: 'sig-tia-1',
    pattern: 'Falling Wedge, Elliott ABC Expanded Flat (partial)',
    created_at: new Date(Date.now() - 18 * 60e3).toISOString(),
  }],
};

async function serve() {
  const app = express();
  app.get('/dashboard', (req, res) => res.sendFile(path.join(PUBLIC, 'dashboard.html')));
  app.use(express.static(PUBLIC));
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

test('a collapsed Signals row is a label and a value, not five columns',
  SKIP ? { skip: SKIP } : {}, async (t) => {
    const { server, base } = await serve();
    const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
    try {
      const ctx = await browser.newContext({ viewport: PHONE, deviceScaleFactor: 2 });
      await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
      await ctx.route('**/api/**', (route) => {
        const u = new URL(route.request().url());
        const body = u.pathname.startsWith('/api/signals') ? SIGNAL
          : u.pathname.startsWith('/api/market/candles') ? CANDLES
          : { ok: true, data: {}, rows: [], items: [] };
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      });
      await ctx.route('**/api/stream*', (r) => r.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
      await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.abort());
      const page = await ctx.newPage();
      await page.goto(`${base}/dashboard#signals`, { waitUntil: 'load' });
      await page.waitForSelector('#c-stream td[data-label="Signal"]', { timeout: 10000 });
      await page.waitForSelector('#c-stream td[data-label="Signal"] svg.sc', { timeout: 10000 });

      const m = await page.evaluate(() => {
        const box = (el) => { const r = el.getBoundingClientRect(); return { w: r.width, h: r.height, top: r.top, left: r.left, right: r.right }; };
        const cell = document.querySelector('#c-stream td[data-label="Signal"]');
        const chip = cell.querySelector('.chip');
        const sym = cell.querySelector('b');
        const desc = cell.querySelector('div.muted');
        const chart = cell.querySelector('svg.sc');
        const age = document.querySelector('#c-stream td[data-label="Age"]');
        const label = age && getComputedStyle(age, '::before');
        return {
          cell: box(cell), chip: box(chip), sym: box(sym), desc: box(desc),
          chart: box(chart),
          // The label is a ::before, so its own box is not queryable: read the
          // VALUE's left edge against the cell's, which is the same claim —
          // a right-aligned value leaves room for the label at the left.
          ageValueLeft: age ? age.getBoundingClientRect().left : 0,
          ageText: age ? age.textContent.trim() : '',
          labelAutoMargin: label ? label.marginRight : '',
        };
      });
      t.diagnostic(`cell ${Math.round(m.cell.w)}x${Math.round(m.cell.h)} · chip ${Math.round(m.chip.w)}x${Math.round(m.chip.h)} `
        + `· chart ${Math.round(m.chart.w)}x${Math.round(m.chart.h)} · desc ${Math.round(m.desc.w)}x${Math.round(m.desc.h)}`
);

      // 1. A chip is a badge, not a column. `align-items: stretch` sized it to
      //    the tallest item in the row, which is how a 999px radius became a
      //    capsule the height of the card.
      assert.ok(m.chip.h < m.cell.h * 0.5,
        `the direction chip fills its cell (${Math.round(m.chip.h)}px of ${Math.round(m.cell.h)}px) — it is being stretched, not badged`);

      // 2. The chart is `width="100%"` over a 260-unit viewBox: starved of
      //    width it renders as a sliver of squashed candles.
      assert.ok(m.chart.w > m.cell.w * 0.7,
        `the signal chart got ${Math.round(m.chart.w)}px of the cell's ${Math.round(m.cell.w)}px — it is being squeezed into a column`);

      // 3. The pattern name reads as a sentence. Laid out as a column it wrapped
      //    one or two words per line.
      assert.ok(m.desc.w > m.cell.w * 0.7,
        `the pattern name got ${Math.round(m.desc.w)}px of ${Math.round(m.cell.w)}px — it will wrap a word per line`);

      // 4. The chip and the symbol are ONE value on ONE line, not two columns
      //    pushed to opposite ends.
      assert.ok(Math.abs(m.chip.top - m.sym.top) < m.chip.h,
        'the direction chip and the symbol are on different lines');
      assert.ok(m.sym.left - m.chip.right < 40,
        `the chip and the symbol are ${Math.round(m.sym.left - m.chip.right)}px apart — a multi-part value is being spread across the row`);

      assert.ok(m.ageValueLeft > 0 && m.ageText.length > 0, 'the Age row rendered no value');
      CHART_HEIGHT.drawn = m.chart.h;    // read by the skeleton test below
    } finally {
      await browser.close();
      server.close();
    }
  });

// The height the chart DRAWS at, recorded above and compared below. The phone
// height itself is a design choice and is deliberately NOT pinned to a number;
// what is pinned is that the skeleton reserves whatever the chart occupies, or
// the row jumps 24px when the SVG arrives.
const CHART_HEIGHT = { drawn: null };

test('the chart skeleton reserves what the chart draws', SKIP ? { skip: SKIP } : {}, async (t) => {
  assert.ok(CHART_HEIGHT.drawn > 0, 'the drawn height was not recorded');
  let slowCandles = true;
  const { server, base } = await serve();
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
  try {
    const ctx = await browser.newContext({ viewport: PHONE, deviceScaleFactor: 2 });
    await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
    // The candle read FAILS, so the slot keeps its skeleton. That is the ONLY
    // page state in which the reservation is observable: once the SVG mounts,
    // its own height carries the slot and reading it then compares a number
    // with itself — which is exactly how the first draft of this passed while
    // the skeleton rule went unmeasured, and the mutation round said so.
    // Two states, not one. A SLOW read leaves the skeleton up with `aria-busy`
    // still set; a FAILED read clears it and leaves only the slot's own
    // min-height. Both must reserve what the chart draws, and the first draft
    // measured only the second — the mutation round said so by shrinking the
    // skeleton rule with no verdict changing.
    await ctx.route('**/api/market/candles**', async (r) => {
      if (slowCandles) { await new Promise((ok) => setTimeout(ok, 4000)); }
      return r.fulfill({ status: 502, contentType: 'application/json', body: '{}' });
    });
    await ctx.route('**/api/**', (route) => {
      const u = new URL(route.request().url());
      return route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify(u.pathname.startsWith('/api/signals') ? SIGNAL : { ok: true, data: {}, rows: [], items: [] }) });
    });
    await ctx.route('**/api/stream*', (r) => r.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
    await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.abort());
    const page = await ctx.newPage();
    const read = () => page.$eval('#c-stream td[data-label="Signal"] .sc-slot', (el) => ({
      h: el.getBoundingClientRect().height,
      busy: el.getAttribute('aria-busy') === 'true',
      hasSvg: !!el.querySelector('svg.sc'),
      skel: (() => { const k = el.querySelector('.skel--sc'); return k ? k.getBoundingClientRect().height : null; })(),
    }));

    await page.goto(`${base}/dashboard#signals`, { waitUntil: 'load' });
    await page.waitForSelector('#c-stream td[data-label="Signal"] .sc-slot', { timeout: 10000 });
    await page.waitForTimeout(700);
    const loading = await read();
    t.diagnostic(`loading ${Math.round(loading.h)}px (busy ${loading.busy}) · chart draws ${Math.round(CHART_HEIGHT.drawn)}px`);
    assert.equal(loading.busy, true, 'the slot is no longer loading — this is measuring the wrong state');
    assert.ok(Math.abs(loading.h - CHART_HEIGHT.drawn) < 8,
      `the loading skeleton reserves ${Math.round(loading.h)}px and the chart draws `
      + `${Math.round(CHART_HEIGHT.drawn)}px — the row will jump when the chart arrives`);
    // NOT asserted, and the reason is worth more than the assertion: the
    // shimmer's own height rule governs only the first few frames. The chart
    // code sets `data-sc-done` and empties the slot before it awaits the
    // fetch, so by any observable moment `aria-busy` is still true and the
    // `.skel--sc` element is already gone — measured here as absent. What
    // reserves the space in every state a test can reach is the slot's
    // `min-height`, which the mutation round does kill.

    slowCandles = false;                       // now the read FAILS outright
    await page.reload({ waitUntil: 'load' });
    await page.waitForSelector('#c-stream td[data-label="Signal"] .sc-slot', { timeout: 10000 });
    await page.waitForTimeout(800);
    const failed = await read();
    t.diagnostic(`failed-read ${Math.round(failed.h)}px (busy ${failed.busy}, svg ${failed.hasSvg})`);
    assert.equal(failed.hasSvg, false, 'the chart mounted despite a failed candle read');
    assert.ok(Math.abs(failed.h - CHART_HEIGHT.drawn) < 8,
      `after a failed read the slot reserves ${Math.round(failed.h)}px against the chart's `
      + `${Math.round(CHART_HEIGHT.drawn)}px — the row will jump if a later read succeeds`);
  } finally {
    await browser.close();
    server.close();
  }
});

/**
 * The other half, and it needs a different table: every value in the Signals
 * row above is a single item, so nothing there can see `space-between` spread a
 * MULTI-PART value. The Portfolio view's trade history writes
 * `dirChip(direction) <b>SYM</b>` into one right-aligned cell — two flex items,
 * which `space-between` pushed to opposite ends of the row with the label
 * stranded between them. The fix is `margin-right: auto` on the label instead,
 * and this is the cell that can tell.
 *
 * Asserted as GEOMETRY, not as the declaration: `getComputedStyle` resolves an
 * auto margin to its used pixel value, so reading the property back would pin a
 * spelling and pass over any rewrite that still spread the value.
 */
const TRADES = {
  trades: [{
    id: 7, symbol: 'ETH/USDT', direction: 'SHORT',
    entry_price: 3120, exit_price: 3044.1, pnl: 75.9,
    closed_at: new Date(Date.now() - 2 * 3600e3).toISOString(), notes: '',
  }],
};

test('a multi-part value stays together instead of being spread across the row',
  SKIP ? { skip: SKIP } : {}, async (t) => {
    const { server, base } = await serve();
    const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
    try {
      const ctx = await browser.newContext({ viewport: PHONE, deviceScaleFactor: 2 });
      await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
      await ctx.route('**/api/**', (route) => {
        const u = new URL(route.request().url());
        const body = u.pathname.startsWith('/api/trades/history')
          ? TRADES : { ok: true, data: {}, rows: [], items: [] };
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      });
      await ctx.route('**/api/stream*', (r) => r.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
      await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.abort());
      const page = await ctx.newPage();
      await page.goto(`${base}/dashboard#portfolio`, { waitUntil: 'load' });
      await page.waitForSelector('#c-hist td[data-label="Trade"]', { timeout: 10000 });

      const m = await page.evaluate(() => {
        const cell = document.querySelector('#c-hist td[data-label="Trade"]');
        const cb = cell.getBoundingClientRect();
        const chip = cell.querySelector('.chip').getBoundingClientRect();
        const sym = cell.querySelector('b').getBoundingClientRect();
        const pad = parseFloat(getComputedStyle(cell).paddingRight) || 0;
        const noteCell = document.querySelector('#c-hist td[data-label="Note"]');
        const input = noteCell.querySelector('input').getBoundingClientRect();
        const btn = noteCell.querySelector('button').getBoundingClientRect();
        return { note: { cellW: noteCell.getBoundingClientRect().width, inputW: input.width,
                         inputBottom: input.bottom, buttonsTop: btn.top },
                 cellLeft: cb.left, cellRight: cb.right, pad,
                 chipLeft: chip.left, chipRight: chip.right, chipHeight: chip.height,
                 symLeft: sym.left, symRight: sym.right, symHeight: sym.height };
      });
      t.diagnostic(`cell [${Math.round(m.cellLeft)}, ${Math.round(m.cellRight)}] · chip right ${Math.round(m.chipRight)} · symbol left ${Math.round(m.symLeft)}`
        + ` · note cell ${Math.round(m.note.cellW)} input ${Math.round(m.note.inputW)} inputBottom ${Math.round(m.note.inputBottom)} btnTop ${Math.round(m.note.buttonsTop)}`);

      assert.ok(m.symLeft - m.chipRight < 40,
        `the chip and the symbol are ${Math.round(m.symLeft - m.chipRight)}px apart — the value is being spread across the row`);
      assert.ok(m.cellRight - m.symRight < m.pad + 4,
        'the value is not against the right edge — it is no longer right-aligned');
      assert.ok(m.chipLeft - m.cellLeft > 40,
        'the value starts at the left edge — the label has nowhere to sit');
      // `align-items: center`. The default `stretch` sizes every flex item to
      // the row, so the symbol's own box grows to the chip's height and its
      // text sits at the top of it. Two boxes on this page change when that
      // declaration goes, and the first draft of this test tolerated the shift.
      // The note input spans the cell. Unstacked this cell degrades GRACEFULLY
      // rather than breaking — the control row wraps and the input still gets
      // ~81% — so the claim has to be the full inner width, not a share of it.
      // The mutation round said so: a 70% threshold passed either way.
      assert.ok(m.note.inputW > m.note.cellW - 2 * m.pad - 4,
        `the journal note input got ${Math.round(m.note.inputW)}px of the cell's `
        + `${Math.round(m.note.cellW - 2 * m.pad)}px of inner width — the controls are being `
        + 'laid out beside their label rather than stacked under it');
      assert.ok(m.note.buttonsTop > m.note.inputBottom - 4,
        'the note buttons sit beside the input rather than below it — the cell is not stacking');
      assert.ok(m.symHeight < m.chipHeight,
        `the symbol is ${Math.round(m.symHeight)}px against the chip's ${Math.round(m.chipHeight)}px `
        + '— it is being stretched to the row rather than centred in it');
    } finally {
      await browser.close();
      server.close();
    }
  });
