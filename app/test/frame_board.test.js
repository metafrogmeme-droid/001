'use strict';
/**
 * Leaderboard frame card + sitemap. The whole paper-trading board becomes a
 * feed image, and the site tells crawlers where everything lives. Pins:
 * - ONE source of truth: the card renders from the same computeLeaderboard
 *   the JSON route serves — the image can never disagree with the API;
 * - §4 drawn strictly: the card block draws handles, percent and counts
 *   only (the file-wide drawn-args scan in frame_cards.test.js covers this
 *   file; here we pin the honest empty state and the footer);
 * - /arena and /leaderboard both carry the frame meta, and the static
 *   og:image is stripped only when the live card really took over;
 * - every sitemap URL is a real page route in server.js — a sitemap that
 *   drifts from reality is worse than none (same law as llms.txt).
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

const read = (...p) => fs.readFileSync(path.join(__dirname, '..', ...p), 'utf8');
let server, base;

function req(method, p) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${p}`, { method }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode,
        type: res.headers['content-type'] || '', body: Buffer.concat(chunks) }));
    });
    r.on('error', reject);
    r.end();
  });
}

test.before(async () => {
  const app = express();
  app.use('/api/frame', require('../routes/frame'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

test('the JSON route and the card share one leaderboard computation', () => {
  const arenaSrc = read('routes', 'arena.js');
  assert.match(arenaSrc, /async function computeLeaderboard\(\)/);
  assert.match(arenaSrc, /res\.json\(await computeLeaderboard\(\)\)/,
    'the route serves the shared computation');
  assert.match(arenaSrc, /module\.exports\.computeLeaderboard = computeLeaderboard;/);
  const frameSrc = read('routes', 'frame.js');
  assert.match(frameSrc, /computeLeaderboard/, 'the card renders from the same function');
});

test('an empty board answers an honest card, not a fabricated ranking', async () => {
  const r = await req('GET', '/api/frame/leaderboard/image');
  assert.strictEqual(r.status, 200);
  assert.match(r.type, /image\/png/);
  assert.deepStrictEqual([...r.body.slice(0, 8)],
    [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const src = read('routes', 'frame.js');
  assert.match(src, /NO RANKED TRADERS YET/);
  assert.match(src, /HANDLES ARE OPT-IN/);
  assert.match(src, /PERCENT AND COUNTS ONLY - NEVER AN AMOUNT/);
});

test('the board refresh POST answers a frame document', async () => {
  const r = await req('POST', '/api/frame/leaderboard/refresh');
  assert.strictEqual(r.status, 200);
  const html = r.body.toString('utf8');
  assert.match(html, /fc:frame" content="vNext"/);
  assert.match(html, /\/api\/frame\/leaderboard\/image\?t=/);
  assert.match(html, /button:2" content="Refresh the board"/);
});

test('both arena pages carry the frame meta with the og swap', () => {
  const { injectBoardMeta } = require('../lib/frame_meta');
  const out = injectBoardMeta('<html><head></head></html>', 'https://x.example');
  assert.match(out, /fc:frame:post_url" content="https:\/\/x\.example\/api\/frame\/leaderboard\/refresh"/);
  assert.match(out, /og:image" content="https:\/\/x\.example\/api\/frame\/leaderboard\/image"/);
  assert.strictEqual(injectBoardMeta('<html><head></head></html>', ''),
    '<html><head></head></html>', 'no origin -> untouched page');
  const srv = read('server.js');
  const arenaAt = srv.indexOf("app.get('/arena'");
  const boardAt = srv.indexOf("app.get('/leaderboard'");
  for (const [name, at] of [['arena', arenaAt], ['leaderboard', boardAt]]) {
    const block = srv.slice(at, srv.indexOf('});', at));
    assert.match(block, /injectBoardMeta/, `${name} page injects the board meta`);
    assert.match(block, /withBoard !== html|withMeta !== html/,
      `${name} strips the static og:image only when injection took over`);
  }
});

test('every sitemap URL is a real page route', () => {
  const sitemap = read('public', 'sitemap.xml');
  const srv = read('server.js');
  const locs = [...sitemap.matchAll(/<loc>https:\/\/pmvc58g2\.mule\.page(\/[a-z-]*)<\/loc>/g)]
    .map((m) => m[1]);
  assert.ok(locs.length >= 15, 'the sitemap is substantive');
  for (const loc of locs) {
    assert.ok(srv.includes(`app.get('${loc}'`), `${loc} is claimed but not routed`);
  }
  const robots = read('public', 'robots.txt');
  assert.match(robots, /Sitemap: https:\/\/pmvc58g2\.mule\.page\/sitemap\.xml/);
});
