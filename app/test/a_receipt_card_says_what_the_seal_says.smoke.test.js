'use strict';
/**
 * The receipt card on the real /call page in Chromium: a Duel call whose seal
 * checks offers "Make a card", the card is painted with its QR code on a
 * light tile (read back out of the pixels), the image can be saved, and a
 * receipt whose seal does not check offers no card at all. In German, the
 * button speaks German.
 */
const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const H = require('./helpers/pi_app_harness');
const Card = require('../public/js/receipt-card.js');
const { qrFor } = require('../lib/receipt_qr');

const PUBLIC = path.join(__dirname, '..', 'public');
const ORIGIN = 'https://www.humanoid-traders.com';
const sha = (s) => crypto.createHash('sha256').update(s).digest('hex');

const PAY = JSON.stringify({
  v: 4, kind: 'duel_pick', round_id: 7, day: '2026-10-22', category: 'direction', symbol: 'BTCUSDT', symbol_b: null,
  threshold_pct: null, handle: null, pick: 'long', entry_price: 108000, entry_price_b: null,
  resolves_at: '2026-10-23T09:00:00.000Z', picked_at: '2026-10-22T09:14:00.000Z',
});
const SEAL = sha(PAY);
const KEY = 'duel:' + SEAL;
const SETTLE_PAY = JSON.stringify({ v: 9, kind: 'duel_settle', pick: 'long', category: 'direction', settle_state: 'correct', disputed: false });
const SETTLE = { kind: 'duel_settle', seal: sha(SETTLE_PAY), seal_payload: SETTLE_PAY, sealed_at: '2026-10-23T09:01:00Z', review: null, flagged: false,
  call: { key: KEY, seal: SEAL }, current: { settle_state: 'correct' } };
const RECEIPT = { kind: 'duel_pick', seal: SEAL, seal_payload: PAY, sealed_at: '2026-10-22T09:14:00Z', current: {},
  anchor: { day: '2026-10-22', root: 'f'.repeat(64), proof: [], anchors: [] },
  settlement: { key: 'settle:' + SETTLE.seal, seal: SETTLE.seal, state: 'correct' } };
const QUORUM = { status: 'multi_anchored', k: 2, groups: [['base'], ['bitcoin']], anchors: [] };

async function open(browser, base, { receipt = RECEIPT, locale = null } = {}) {
  const ctx = await browser.newContext(locale ? { locale } : {});
  if (locale) await ctx.addInitScript((l) => { try { localStorage.setItem('rc_lang', l); } catch (e) { /* none */ } }, locale);
  const asked = [];
  await ctx.route('**/api/**', (route) => {
    const u = new URL(route.request().url());
    asked.push(u.pathname);
    const json = (status, body) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const p = decodeURIComponent(u.pathname);
    if (p === '/api/call/' + KEY + '/qr') return json(200, qrFor(ORIGIN, KEY));
    if (p === '/api/call/' + KEY) return json(200, receipt);
    if (p === '/api/call/settle:' + SETTLE.seal) return json(200, SETTLE);
    if (p === '/api/roots/quorum/2026-10-22') return json(200, QUORUM);
    return json(404, { error: 'not in this test' });
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(base + '/call/' + encodeURIComponent(KEY));
  return { ctx, page, errors, asked };
}

test('the receipt card on /call', H.SKIP ? { skip: H.SKIP } : {}, async (t) => {
  const app = express();
  app.get('/call/:key', (req, res) => res.sendFile(path.join(PUBLIC, 'call.html')));
  app.use(express.static(PUBLIC));
  const server = await new Promise((r) => { const s = http.createServer(app).listen(0, '127.0.0.1', () => r(s)); });
  const base = 'http://127.0.0.1:' + server.address().port;
  const browser = await H.pw.chromium.launch({ executablePath: H.CHROMIUM, args: ['--no-sandbox'] });
  try {
    await t.test('a checked Duel call makes a card whose QR is on a light tile, and the card can be saved', async () => {
      const { ctx, page, errors, asked } = await open(browser, base);
      await page.waitForSelector('#rcMake');
      assert.strictEqual(await page.textContent('#rcMake'), 'Make a card');
      await page.click('#rcMake');
      await page.waitForSelector('img.rc-img', { timeout: 15000 });
      const alt = await page.getAttribute('img.rc-img', 'alt');
      assert.strictEqual(alt, 'BTC · LONG · SEALED ✓ · SETTLED ✓ · ANCHORED ✓');
      const save = await page.$('#rcSave');
      assert.strictEqual(await save.getAttribute('download'), 'runeclaw-duel-' + SEAL.slice(0, 8) + '.png');
      assert.match(await save.getAttribute('href'), /^data:image\/png;base64,/);
      // The QR read back from the picture: a finder pattern's dark corner, its
      // light ring and dark core, and the light quiet zone outside it.
      const op = Card.ops({ eyebrow: '', market: 'x', glyph: '', pick: 'x', lines: [], seal: 'x', scan: 'x', scanNote: 'x',
        steps: [{ label: 'a', state: 'done' }, { label: 'b', state: 'done' }, { label: 'c', state: 'done' }] }, qrFor(ORIGIN, KEY), () => 10)
        .find((o) => o.op === 'qr');
      const px = await page.evaluate(({ x, y, c }) => {
        const img = document.querySelector('img.rc-img');
        const cv = document.createElement('canvas');
        cv.width = img.naturalWidth; cv.height = img.naturalHeight;
        const g = cv.getContext('2d');
        g.drawImage(img, 0, 0);
        const at = (dx, dy) => Array.from(g.getImageData(Math.floor(x + dx * c), Math.floor(y + dy * c), 1, 1).data).slice(0, 3);
        return { size: [img.naturalWidth, img.naturalHeight], corner: at(0.5, 0.5), ring: at(1.5, 1.5), core: at(3.5, 3.5), quiet: at(-2, 0.5) };
      }, { x: op.x, y: op.y, c: op.cell });
      const dark = (rgb) => rgb.every((v) => v < 40);
      const light = (rgb) => rgb.every((v) => v > 200);
      assert.deepStrictEqual(px.size, [Card.W, Card.H]);
      assert.ok(dark(px.corner) && light(px.ring) && dark(px.core) && light(px.quiet), JSON.stringify(px));
      // The settlement was asked for and hashed here; the QR came from the server.
      assert.ok(asked.includes('/api/call/' + encodeURIComponent(KEY) + '/qr') || asked.some((a) => decodeURIComponent(a) === '/api/call/' + KEY + '/qr'));
      assert.ok(asked.some((a) => decodeURIComponent(a) === '/api/call/settle:' + SETTLE.seal));
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('a receipt whose seal does not check offers no card', async () => {
      const { ctx, page, errors } = await open(browser, base, { receipt: { ...RECEIPT, seal_payload: PAY.replace('long', 'short') } });
      await page.waitForSelector('.verdict.bad');
      assert.strictEqual(await page.$('#rcMake'), null);
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });

    await t.test('in German, the button speaks German', async () => {
      const { ctx, page, errors } = await open(browser, base, { locale: 'de' });
      await page.waitForSelector('#rcMake');
      await page.waitForFunction(() => window.RCI18N && document.querySelector('#rcMake'));
      assert.strictEqual(await page.textContent('#rcMake'), 'Karte erstellen');
      assert.deepStrictEqual(errors, []);
      await ctx.close();
    });
  } finally {
    await browser.close();
    server.close();
  }
});
