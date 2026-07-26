'use strict';
/**
 * Frame cards — the contract under test:
 * - The stdlib PNG encoder emits structurally valid PNGs (signature, IHDR
 *   dimensions, chunk CRCs verified against the same table PNG uses).
 * - The card is honest about WHERE verification happened: SERVER
 *   RECOMPUTED on the image, re-derive-in-your-browser as the pointer, and
 *   an unknown key renders an honest not-found card, never a broken image.
 * - No prices or outcomes ride on the card — symbol + direction only.
 * - /call/:key HTML carries fc:frame + og:image for valid keys ONLY, and
 *   only when a public origin is configured.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { pool } = require('../db');
const { Card, encodePng, crc32, COLORS } = require('../lib/pixel_card');
const { injectCallMeta } = require('../lib/frame_meta');

test('crc32 matches the PNG reference value for "IEND"', () => {
  assert.equal(crc32(Buffer.from('IEND', 'ascii')).toString(16), 'ae426082');
});

test('the encoder emits a structurally valid PNG with correct chunk CRCs', () => {
  const png = encodePng(3, 2, Buffer.alloc(3 * 2 * 4, 0x80));
  assert.equal(png.slice(0, 8).toString('hex'), '89504e470d0a1a0a');
  assert.equal(png.readUInt32BE(16), 3, 'IHDR width');
  assert.equal(png.readUInt32BE(20), 2, 'IHDR height');
  // Walk every chunk and verify its CRC — the same check a decoder makes.
  let off = 8;
  const types = [];
  while (off < png.length) {
    const len = png.readUInt32BE(off);
    const type = png.slice(off + 4, off + 8).toString('ascii');
    const body = png.slice(off + 4, off + 8 + len);
    assert.equal(png.readUInt32BE(off + 8 + len), crc32(body), `CRC of ${type}`);
    types.push(type);
    off += 12 + len;
  }
  assert.deepEqual(types, ['IHDR', 'IDAT', 'IEND']);
});

test('the font draws real pixels; unknown characters draw none', () => {
  const c = new Card(20, 10, [0, 0, 0, 255]);
  c.text('A', 1, 1, 1, COLORS.gold);
  // 'A' row 0 is .XXX. — pixel (2,1) < (x+col=1+1, y=1) must be gold.
  const o = (1 * 20 + 2) * 4;
  assert.equal(c.px[o], COLORS.gold[0]);
  const before = Buffer.from(c.px);
  c.text('あ', 1, 1, 1, COLORS.gold); // outside the font → space, no pixels
  assert.ok(before.equals(c.px), 'unknown glyphs must not invent pixels');
});

test('meta injection: valid keys only, configured origin only, attributes escaped', () => {
  const html = '<html><head><title>x</title></head><body></body></html>';
  const out = injectCallMeta(html, 'sig:abc-123', 'https://pmvc58g2.mule.page');
  assert.match(out, /fc:frame" content="vNext"/);
  assert.match(out, /fc:frame:image" content="https:\/\/pmvc58g2\.mule\.page\/api\/frame\/call\/sig%3Aabc-123\/image"/);
  assert.match(out, /og:image/);
  assert.equal(injectCallMeta(html, '<script>', 'https://x.io'), html,
    'a malformed key serves the untouched page');
  assert.equal(injectCallMeta(html, 'sig:abc', ''), html,
    'no public origin, no absolute URLs — untouched beats broken');
});

// ── the image route ──────────────────────────────────────────────────────────
let server, base;

function get(p) {
  return new Promise((resolve, reject) => {
    http.get(`${base}${p}`, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        type: res.headers['content-type'] || '',
        body: Buffer.concat(chunks),
      }));
    }).on('error', reject);
  });
}

test.before(async () => {
  const { sealCall } = require('../lib/callseal.js');
  const fixed = { signal_key: 'framekey1', symbol: 'BTCUSDT', direction: 'LONG',
    confidence: 0.7, entry_price: 64000, stop_loss: 62000, take_profit: 70000,
    pattern: null, regime: null, created_at: new Date() };
  const receipt = sealCall(fixed);
  await pool.execute(
    `INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern, regime,
       entry_price, stop_loss, take_profit, rr, thesis, status, pnl, created_at, resolved_at,
       seal, seal_payload, sealed_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [fixed.signal_key, fixed.symbol, fixed.direction, fixed.confidence, 0, null, null,
      fixed.entry_price, fixed.stop_loss, fixed.take_profit, 0, null, 'NEW', null,
      fixed.created_at, null, receipt.seal, receipt.seal_payload, fixed.created_at]);
  const app = express();
  app.use('/api/frame', require('../routes/frame'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('a sealed call renders a real PNG frame card', async () => {
  const r = await get('/api/frame/call/framekey1/image');
  assert.equal(r.status, 200);
  assert.match(r.type, /image\/png/);
  assert.equal(r.body.slice(0, 8).toString('hex'), '89504e470d0a1a0a');
  assert.equal(r.body.readUInt32BE(16), 800);
  assert.equal(r.body.readUInt32BE(20), 418, '1.91:1 — the frame aspect');
});

test('an unknown key answers an honest not-found card, still a PNG', async () => {
  const r = await get('/api/frame/call/sig:doesnotexist/image');
  assert.equal(r.status, 200, 'a broken image says nothing; the card says NO SEALED CALL');
  assert.match(r.type, /image\/png/);
  const bad = await get('/api/frame/call/%3Cscript%3E/image');
  assert.equal(bad.status, 400);
});

test('the card never carries prices or outcomes — source pin', () => {
  const src = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'routes', 'frame.js'), 'utf8');
  assert.match(src, /symbol \+ direction only/i);
  assert.doesNotMatch(src, /entry_price|stop_loss|take_profit|pnl/,
    'sealed prices exist in the record; the FEED card still shows none');
  assert.match(src, /SERVER RECOMPUTED/, 'the card says where verification ran');
  assert.match(src, /RE-DERIVE THE HASH IN YOUR OWN BROWSER/);
  const srv = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(srv, /injectCallMeta/, 'the call page carries the frame meta');
});
