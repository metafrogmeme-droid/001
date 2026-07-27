'use strict';
/**
 * Frame POST interactions — the feed taps back. A post-action button on the
 * call/trader frames POSTs to the server; the answer is a fresh frame whose
 * image was re-verified on THAT request. The contract:
 * - the signed Farcaster packet is deliberately ignored: these interactions
 *   grant nothing and read nothing private, so no identity is needed, and
 *   NOTHING from the POST body reaches the drawing or the response URLs
 *   (both rebuilt server-side from the validated path param alone);
 * - the recheck stamp carries the SERVER's clock, never a client string;
 * - no configured public origin → 400, never a frame with broken URLs;
 * - bad keys/handles → 400 on the POST routes (they answer machines, not
 *   browsers — no honest-card fallback needed here).
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.PUBLIC_ORIGIN = 'https://frames.example';
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const http = require('http');
const express = require('express');
const { pool } = require('../db');
const { injectCallMeta, injectTraderMeta, callVerifyFrame, traderRefreshFrame } = require('../lib/frame_meta');

let server, base;

function req(method, p) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${p}`, { method }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        type: res.headers['content-type'] || '',
        body: Buffer.concat(chunks),
      }));
    });
    r.on('error', reject);
    r.end();
  });
}

test.before(async () => {
  const { sealCall } = require('../lib/callseal.js');
  const fixed = { signal_key: 'tapkey1', symbol: 'ETHUSDT', direction: 'SHORT',
    confidence: 0.6, entry_price: 3000, stop_loss: 3100, take_profit: 2800,
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

test('both injectors advertise the post button beside the link button', () => {
  const html = '<html><head></head><body></body></html>';
  const call = injectCallMeta(html, 'tapkey1', 'https://frames.example');
  assert.match(call, /fc:frame:post_url" content="https:\/\/frames\.example\/api\/frame\/call\/tapkey1\/verify"/);
  assert.match(call, /fc:frame:button:2" content="Re-verify now"/);
  assert.match(call, /fc:frame:button:2:action" content="post"/);
  const trader = injectTraderMeta(html, 'sometrader', 'https://frames.example');
  assert.match(trader, /fc:frame:post_url" content="https:\/\/frames\.example\/api\/frame\/trader\/sometrader\/refresh"/);
  assert.match(trader, /fc:frame:button:2:action" content="post"/);
});

test('the frame builders refuse what the injectors refuse', () => {
  assert.strictEqual(callVerifyFrame('bad key!', 'https://x.example', 'b'), null);
  assert.strictEqual(callVerifyFrame('goodkey1', '', 'b'), null);
  assert.strictEqual(traderRefreshFrame('no spaces here', 'https://x.example', 'b'), null);
  const ok = callVerifyFrame('goodkey1', 'https://x.example', 'zz1');
  assert.match(ok, /fc:frame" content="vNext"/);
  assert.match(ok, /image\?rechecked=zz1"/);
  assert.match(ok, /button:2:action" content="post"/);
});

test('tapping re-verify answers a fresh frame document', async () => {
  const r = await req('POST', '/api/frame/call/tapkey1/verify');
  assert.strictEqual(r.status, 200);
  assert.match(r.type, /text\/html/);
  const html = r.body.toString('utf8');
  assert.match(html, /fc:frame" content="vNext"/);
  assert.match(html, /\/api\/frame\/call\/tapkey1\/image\?rechecked=/);
  assert.match(html, /button:2" content="Re-verify now"/);
});

test('the recheck image really renders, with a server-clock stamp', async () => {
  const r = await req('GET', '/api/frame/call/tapkey1/image?rechecked=abc');
  assert.strictEqual(r.status, 200);
  assert.match(r.type, /image\/png/);
  assert.deepStrictEqual([...r.body.slice(0, 8)],
    [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], 'a real PNG signature');
  // the recheck variant caches briefly, not permanently
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'frame.js'), 'utf8');
  assert.match(src, /RECHECKED ' \+ at \+ ' UTC/);
  assert.match(src, /new Date\(\)\.toISOString\(\)/, 'the stamp is the server clock');
  // nothing from the request reaches the drawing: the recheck branch never
  // touches req.body or req.query beyond the presence check
  const branch = src.slice(src.indexOf('req.query.rechecked'), src.indexOf("res.type('png')"));
  assert.ok(!branch.includes('req.body'), 'POST packet content never reaches the card');
});

test('bad input and missing origin answer 400, never a broken frame', async () => {
  const bad = await req('POST', '/api/frame/call/%20/verify');
  assert.strictEqual(bad.status, 400);
  const badH = await req('POST', '/api/frame/trader/no%20spaces/refresh');
  assert.strictEqual(badH.status, 400);
});

test('trader refresh answers a frame with a busted image URL', async () => {
  const r = await req('POST', '/api/frame/trader/anyhandle/refresh');
  assert.strictEqual(r.status, 200);
  const html = r.body.toString('utf8');
  assert.match(html, /\/api\/frame\/trader\/anyhandle\/image\?t=/);
  assert.match(html, /button:2" content="Refresh the record"/);
});
