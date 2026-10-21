'use strict';
/**
 * The owl is on screen even when the 3D cannot run.
 *
 * 2026-10-20, the operator's phone: the Agent Hub's avatar was an empty
 * 112-pixel box. The page loads the 3D viewer only when the visitor has not
 * asked for reduced motion (Android's "Remove animations", which a power
 * saving mode can switch on), and nothing else was in the box. When the
 * viewer did run, its avatar framing cropped to the upper 60% of the model
 * (a head-and-chest bust, written for the humanoid mascot before the owl),
 * which on the round owl is the badge on its forehead with its ears and eyes
 * cut off. And the /agent page, which skips the 3D below 900px, said
 * "the 3D model appears once /mascot/agent.glb is added" under "The AI Viking
 * Agent" title.
 *
 * Every avatar host now starts with a still of the owl (the same model,
 * rendered); the viewer swaps it out only once the model is loaded, and a
 * browser with no WebGL keeps it. Driven here in Chromium:
 *   - reduced motion: the still, loaded and the size of the box; no canvas;
 *   - no WebGL (getContext answers null): state "failed", the still stays;
 *   - WebGL: state "ready", the canvas shown, the still gone, and the owl
 *     framed whole (the box's edge ring is mostly background);
 *   - /agent on a phone: the still, no file name, the drag hint hidden; on a
 *     desktop with WebGL, the 3D and the hint.
 *
 * Needs a Chromium binary; without one it SKIPS and says so (a skip is not a
 * pass). The framing arm also needs WebGL in that Chromium (SwiftShader); a
 * box whose viewer could not start says so in a diagnostic instead.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const PUBLIC = path.join(__dirname, '..', 'public');
const FIXTURES = require('./fixtures/dashboard_smoke_fixtures.js');

function findChromium() {
  const cands = [process.env.RC_SMOKE_CHROMIUM, process.env.PLAYWRIGHT_CHROMIUM].filter(Boolean);
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try {
    for (const d of fs.readdirSync(root)) if (/^chromium-\d+$/.test(d)) cands.push(path.join(root, d, 'chrome-linux', 'chrome'));
  } catch (e) { /* no browsers dir */ }
  return cands.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }) || null;
}
let pw = null;
try { pw = require('playwright-core'); } catch (e) { pw = null; }
const CHROMIUM = findChromium();
const SKIP = !pw ? 'playwright-core is not installed' : !CHROMIUM ? 'no Chromium binary found' : null;
const GL_ARGS = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'];
const PHONE = { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true };

function fixtureFor(p) {
  for (const [k, b] of FIXTURES) if (p === k || p.startsWith(k + '/') || p.startsWith(k + '?')) return b;
  return { ok: true, data: {}, rows: [], items: [] };
}

let server, base, browser;
test.before(async () => {
  if (SKIP) return;
  const app = express();
  app.get('/dashboard', (q, s) => s.sendFile(path.join(PUBLIC, 'dashboard.html')));
  app.get('/agent', (q, s) => s.sendFile(path.join(PUBLIC, 'agent.html')));
  app.use(express.static(PUBLIC));
  server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true, args: GL_ARGS });
});
test.after(async () => { if (browser) await browser.close(); if (server) server.close(); });

async function open(opts, url, { noWebGL = false } = {}) {
  const ctx = await browser.newContext(opts);
  await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
  await ctx.route('**/api/**', (r) => r.fulfill({ status: 200, contentType: 'application/json',
    body: JSON.stringify(fixtureFor(new URL(r.request().url()).pathname)) }));
  await ctx.route('**/api/stream*', (r) => r.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
  await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.abort());
  if (noWebGL) {
    await ctx.addInitScript(() => {
      const orig = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
        return /webgl/i.test(String(kind)) ? null : orig.call(this, kind, ...rest);
      };
    });
  }
  const page = await ctx.newPage();
  await page.goto(`${base}${url}`, { waitUntil: 'load' });
  return { ctx, page };
}

/** The hub avatar once the viewer has settled (or was never loaded). */
async function hubAvatar(page, settle) {
  if (settle) {
    await page.waitForFunction(() => {
      const h = document.querySelector('#hubStack [data-rc-agent3d]');
      return h && ['ready', 'failed', 'error', 'absent'].includes(h.getAttribute('data-rc3d-state'));
    }, null, { timeout: 30000 });
  } else {
    await page.waitForSelector('#hubStack [data-rc-agent3d]', { timeout: 15000 });
    await page.waitForTimeout(3500);   // past the page's own idle-time loader
  }
  return page.evaluate(() => {
    const h = document.querySelector('#hubStack [data-rc-agent3d]');
    const img = h.querySelector('img.rc3d-poster');
    const cv = h.querySelector('canvas');
    const r = img && img.getBoundingClientRect();
    return {
      state: h.getAttribute('data-rc3d-state'),
      still: img ? { loaded: img.complete && img.naturalWidth > 0, w: Math.round(r.width), h: Math.round(r.height) } : null,
      canvasShown: !!cv && getComputedStyle(cv).display !== 'none',
      box: h.clientWidth,
    };
  });
}

test('reduced motion: the hub shows the owl still, the size of its box', SKIP ? { skip: SKIP } : {}, async () => {
  const { ctx, page } = await open({ ...PHONE, reducedMotion: 'reduce' }, '/dashboard#hub');
  try {
    const a = await hubAvatar(page, false);
    assert.equal(a.state, null, 'the viewer is not loaded at all under reduced motion');
    assert.ok(a.still, 'the avatar box holds no still: the empty box of 2026-10-20');
    assert.ok(a.still.loaded, 'the still did not load');
    assert.equal(a.still.w, a.box, 'the still fills its box');
    assert.equal(a.canvasShown, false);
  } finally { await ctx.close(); }
});

test('no WebGL: the viewer says it failed and the still stays', SKIP ? { skip: SKIP } : {}, async () => {
  const { ctx, page } = await open(PHONE, '/dashboard#hub', { noWebGL: true });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  try {
    const a = await hubAvatar(page, true);
    assert.equal(a.state, 'failed', 'a browser without WebGL is "failed", not a thrown error');
    assert.ok(a.still && a.still.loaded, 'the still left with the 3D that never came');
    assert.equal(a.canvasShown, false);
    assert.deepStrictEqual(errors, []);
  } finally { await ctx.close(); }
});

test('WebGL: the 3D replaces the still, and frames the whole owl', SKIP ? { skip: SKIP } : {}, async (t) => {
  const { ctx, page } = await open(PHONE, '/dashboard#hub');
  try {
    const a = await hubAvatar(page, true);
    if (a.state !== 'ready') {
      t.diagnostic(`the viewer could not start here (state ${a.state}); the still must stand in`);
      assert.ok(a.still && a.still.loaded, 'neither the 3D nor the still');
      return;
    }
    assert.equal(a.still, null, 'the still is still in the box beside the 3D');
    assert.equal(a.canvasShown, true, 'the canvas never replaced the still');
    // Framing: paint the box magenta behind the transparent canvas and read
    // its outer ring. The whole owl leaves a margin (the ring is mostly the
    // box); the old bust crop filled the box to its edges.
    await page.evaluate(() => { document.querySelector('#hubStack [data-rc-agent3d]').style.background = '#ff00ff'; });
    await page.waitForTimeout(400);
    const png = await page.locator('#hubStack [data-rc-agent3d]').screenshot();
    const { PNG } = require('pngjs');
    const img = PNG.sync.read(png);
    const ring = Math.max(2, Math.round(img.width * 0.06));
    let magenta = 0, total = 0;
    for (let y = 0; y < img.height; y++) {
      for (let x = 0; x < img.width; x++) {
        if (x >= ring && x < img.width - ring && y >= ring && y < img.height - ring) continue;
        const i = (y * img.width + x) * 4;
        total += 1;
        if (img.data[i] > 200 && img.data[i + 1] < 80 && img.data[i + 2] > 200) magenta += 1;
      }
    }
    t.diagnostic(`edge ring ${ring}px: ${(magenta / total * 100).toFixed(1)}% background`);
    assert.ok(magenta / total > 0.8, `the owl touches the box's edges (${(magenta / total * 100).toFixed(1)}% of the ring is background): cropped, not framed`);
  } finally { await ctx.close(); }
});

test('/agent on a phone: the still, no file name, no drag hint; on a desktop: the 3D and the hint', SKIP ? { skip: SKIP } : {}, async (t) => {
  let { ctx, page } = await open(PHONE, '/agent');
  try {
    await page.waitForFunction(() => document.querySelector('.agent-stage').getAttribute('data-rc3d-state') === 'skipped', null, { timeout: 20000 });
    const r = await page.evaluate(() => {
      const st = document.querySelector('.agent-stage');
      const img = st.querySelector('img.rc3d-poster');
      return { still: !!img && img.naturalWidth > 0, text: st.textContent, title: document.querySelector('.agent-title').textContent,
        hint: getComputedStyle(document.querySelector('.agent-hint')).visibility };
    });
    assert.ok(r.still, 'the agent page on a phone shows no owl');
    assert.doesNotMatch(r.text, /agent\.glb|Summoning/, 'a sentence about a file, where the owl should be');
    assert.doesNotMatch(r.title, /Viking/, 'the title names the old mascot');
    assert.equal(r.hint, 'hidden', '"drag to orbit" under a still');
  } finally { await ctx.close(); }
  ({ ctx, page } = await open({ viewport: { width: 1280, height: 900 } }, '/agent'));
  try {
    await page.waitForFunction(() => ['ready', 'failed', 'error'].includes(document.querySelector('.agent-stage').getAttribute('data-rc3d-state')), null, { timeout: 30000 });
    const r = await page.evaluate(() => ({ state: document.querySelector('.agent-stage').getAttribute('data-rc3d-state'),
      hint: getComputedStyle(document.querySelector('.agent-hint')).visibility }));
    if (r.state !== 'ready') { t.diagnostic(`desktop viewer state ${r.state}`); assert.equal(r.hint, 'hidden'); return; }
    assert.equal(r.hint, 'visible', 'the drag hint belongs with the 3D');
  } finally { await ctx.close(); }
});
