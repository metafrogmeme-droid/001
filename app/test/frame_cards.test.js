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

test('trader cards: a real handle renders; no account renders the truth, not -100%', async () => {
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
    ['framer@test.io', 'x'.repeat(60)]);
  const [u] = await pool.execute('SELECT * FROM users WHERE email = ?', ['framer@test.io']);
  await pool.execute('UPDATE users SET leaderboard_handle = ? WHERE id = ?', ['FrameFox', u[0].id]);

  // Handle exists, no arena account yet → the honest card, never a fabricated
  // -100 percent from a zero balance.
  const empty = await get('/api/frame/trader/FrameFox/image');
  assert.equal(empty.status, 200);
  assert.match(empty.type, /image\/png/);

  await pool.execute(
    `INSERT INTO arena_accounts (user_id, balance, created_at) VALUES (?, ?, ?)
     ON DUPLICATE KEY UPDATE user_id = user_id`, [u[0].id, 11500, new Date()]);
  const now = new Date();
  await pool.execute(
    `INSERT INTO arena_trades (user_id, symbol, direction, entry, exit_price,
      margin, leverage, pnl, reason, seal, opened_at, closed_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [u[0].id, 'BTCUSDT', 'LONG', 100, 110, 500, 3, 150, 'tp', 'f'.repeat(64), now, now]);

  const r = await get('/api/frame/trader/FrameFox/image');
  assert.equal(r.status, 200);
  assert.match(r.type, /image\/png/);
  assert.equal(r.body.readUInt32BE(16), 800);
  assert.equal(r.body.readUInt32BE(20), 418);

  const missing = await get('/api/frame/trader/NobodyHere99/image');
  assert.equal(missing.status, 200, 'an unknown handle answers the honest card');
  assert.match(missing.type, /image\/png/);
  const bad = await get('/api/frame/trader/a/image');
  assert.equal(bad.status, 400);
});

test('trader meta: injected for valid handles, untouched otherwise, static og:image yields', () => {
  const { injectTraderMeta } = require('../lib/frame_meta');
  const html = '<html><head><t></t></head><body></body></html>';
  const out = injectTraderMeta(html, 'FrameFox', 'https://pmvc58g2.mule.page');
  assert.match(out, /fc:frame:image" content="https:\/\/pmvc58g2\.mule\.page\/api\/frame\/trader\/FrameFox\/image"/);
  assert.match(out, /View the record/);
  assert.equal(injectTraderMeta(html, 'no spaces here', 'https://x.io'), html);
  assert.equal(injectTraderMeta(html, 'FrameFox', ''), html);
  const srv = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(srv, /injectTraderMeta/);
  assert.match(srv, /withMeta !== html/,
    'scrapers take the FIRST og:image — the static one is dropped when the card injects');
  assert.match(srv, /og_image_1200x630/);
});

test('the trader page shares the record: share row after i18n, brand labels, honest text', () => {
  const page = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'public', 'trader.html'), 'utf8');
  assert.match(page, /id="shWc"/);
  assert.ok(page.indexOf('/js/i18n-core.js') < page.indexOf("RCI18N.translate('tp.share_txt'"),
    'share links build after the dictionary loads');
  const i18n = require('../public/js/i18n.js');
  for (const l of i18n.LANGS) {
    assert.ok(String(i18n.STRINGS['tp.share_txt'][l.code] || '').trim().length,
      `tp.share_txt missing ${l.code}`);
  }
  assert.doesNotMatch(String(i18n.STRINGS['tp.share_txt'].en), /\$|usd|vUSDT/i);
});

test('the trader card draws §4 strictly — the honesty line is on the image itself', () => {
  const src = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'routes', 'frame.js'), 'utf8');
  // These three are DRAWN STRINGS — they appear on the image a reader sees.
  assert.match(src, /PERCENT AND COUNTS ONLY - NEVER AN AMOUNT/);
  assert.match(src, /SETTLED RETURN/, 'the number is named for what it is');
  assert.match(src, /60_000/, 'trader records change — a TTL cache, not a permanent one');
});

test('"SETTLED RETURN" really is settled — marks cannot move it', () => {
  // THIS ASSERTED A COMMENT. The line above used to require frame.js to
  // contain the phrase "open positions and marks deliberately excluded",
  // which is `//` prose. Passing live positions into the card would have left
  // that sentence untouched, and the card would have gone on saying SETTLED
  // over a mark-to-market number — a snapshot guess labelled as a fact, on a
  // public surface.
  //
  // The code turned out to be RIGHT: frame.js passes `positions: [], marks: {}`
  // explicitly. Checking that before writing the fix is the point — the phrase
  // being unreachable by a test does not make the claim behind it false.
  const { buildTraderCard } = require('../lib/arena_trader');
  const trades = [{ pnl: 100, margin: 100, symbol: 'BTCUSDT', direction: 'LONG',
    leverage: 2, closed_at: new Date(), seal: null }];

  const settled = buildTraderCard({ handle: 'x', balance: 11000, positions: [], marks: {}, trades });
  const marked = buildTraderCard({ handle: 'x', balance: 11000, trades,
    positions: [{ symbol: 'BTCUSDT', direction: 'LONG', entry: 100, margin: 1000, leverage: 10 }],
    marks: { BTCUSDT: { price: 200 } } });

  assert.notStrictEqual(settled.return_pct, marked.return_pct,
    'an open position at double its entry does not move return_pct at all — '
    + 'then passing empty positions proves nothing and this test is vacuous');

  // The wiring: the route feeds it the empty ones, so what it draws is the
  // settled figure and not the marked one.
  const src = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'routes', 'frame.js'), 'utf8');
  const { codeOnly } = require('./helpers/code_only');
  const call = codeOnly(src).match(/buildTraderCard\(\{[\s\S]*?\}\)/);
  assert.ok(call, 'the trader card is no longer built here');
  assert.match(call[0], /positions:\s*\[\]/,
    'the card is built with live positions — SETTLED RETURN would be a lie');
  assert.match(call[0], /marks:\s*\{\}/,
    'the card is built with live marks — SETTLED RETURN would be a lie');
});

test('the card never carries prices or outcomes — source pin', () => {
  const src = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'routes', 'frame.js'), 'utf8');
  // `symbol + direction only` was asserted here and is a COMMENT. Dropped
  // rather than replaced: the drawn-scan below already checks the property it
  // describes, and does it better — it reads what reaches the image instead of
  // what the file says about itself.
  //
  // Scan what is DRAWN, not what is queried: every center()/text() call.
  const drawn = [...src.matchAll(/c\.(?:center|text)\(([^;]*)\)/g)].map((m) => m[1]).join('\n');
  assert.doesNotMatch(drawn, /entry|stop_loss|take_profit|\bpnl\b|balance|vusdt|\$/i,
    'sealed prices and amounts exist in the record; the FEED card still draws none');
  assert.match(src, /SERVER RECOMPUTED/, 'the card says where verification ran');
  assert.match(src, /RE-DERIVE THE HASH IN YOUR OWN BROWSER/);
  const srv = require('node:fs').readFileSync(
    require('node:path').join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(srv, /injectCallMeta/, 'the call page carries the frame meta');
});
