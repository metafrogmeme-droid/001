'use strict';
/**
 * One API family dead at a time: the composite panels must hold.
 *
 * The total-failure smoke (dashboard_unreadable_is_not_zero) checks the rule
 * when NOTHING answers. The shape the Hub strip actually shipped with was one
 * dead source beside live ones -- the strip caught `/api/alerts` to null so a
 * dead alerts read could not blank the portfolio tile, then printed the null
 * as "0 alerts armed". That is the composite case from CLAUDE.md's table, and
 * "omit, never neither" is exactly what this pass exercises: every view, with
 * one family of `/api/**` answering 503 while every other answers its fixture.
 *
 * A finding is a zero-like figure that a panel shows ONLY when a family is
 * dead -- it did not show it with everything live, it is not inside an honest
 * state (.state-block / .skel), and it appeared the moment one read failed.
 * A zero that was already there with every source live came from data.
 *
 * DEEP, by design: sixteen browser contexts over every view is minutes of
 * Chromium. It runs with RC_SMOKE_DEEP=1 and otherwise SKIPS with the reason
 * printed -- a visible skip, not a pass -- so every-commit runs keep the two
 * cheap smokes and a deliberate deep pass is one variable away.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');

const PUBLIC = process.env.RC_SMOKE_PUBLIC || path.join(__dirname, '..', 'public');
const ZERO = /(\$0\.00|\$0\b|(^|[^\d.])0\.0%|(^|[^\d.])0%|\b0 open\b|\b0 positions?\b|\b0 trades?\b|\+0\.00%|\b0 signals?\b|\b0 alerts?\b)/i;

function findChromium() {
  const cands = [process.env.RC_SMOKE_CHROMIUM].filter(Boolean);
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try { for (const d of fs.readdirSync(root)) if (/^chromium-\d+$/.test(d)) cands.push(path.join(root, d, 'chrome-linux', 'chrome')); } catch (e) { /* none */ }
  return cands.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }) || null;
}
let pw = null; try { pw = require('playwright-core'); } catch (e) { pw = null; }
const CHROMIUM = findChromium();
const SKIP = process.env.RC_SMOKE_DEEP !== '1' ? 'deep pass is opt-in: set RC_SMOKE_DEEP=1'
  : !pw ? 'playwright-core is not installed' : !CHROMIUM ? 'no Chromium binary found' : null;

const FIXTURES = require('./fixtures/dashboard_smoke_fixtures.js');
function fixtureFor(pathname) {
  for (const [prefix, body] of FIXTURES) if (pathname === prefix || pathname.startsWith(prefix + '/') || pathname.startsWith(prefix + '?')) return body;
  return { ok: true, data: {}, rows: [], items: [] };
}

const dashSrc = () => fs.readFileSync(path.join(PUBLIC, 'js', 'dashboard.js'), 'utf8');
function viewIds() {
  const src = dashSrc();
  const i = src.indexOf('const VIEWS = [');
  return [...src.slice(i, src.indexOf('];', i)).matchAll(/id:\s*'([a-z-]+)'/g)].map((m) => m[1]);
}
/** The API families the page actually READ while every view rendered with
 *  everything live -- the first two path segments of each request the
 *  baseline walk made. Derived from the walk, not from a list here and not
 *  from every literal in the bundle: a family the page only calls on a
 *  button press (a trade, a share, a credential save) cannot blank a panel
 *  by being dead, and the bundle names fifty of those. A family joins this
 *  pass by being fetched during a render, which is exactly the property. */
function apiFamilies(requestedPaths) {
  const fams = new Set();
  for (const pth of requestedPaths) {
    const seg = pth.split('/');
    fams.add('/' + seg.slice(1, 3).join('/'));
  }
  fams.delete('/api/stream');          // routed separately as an event stream
  fams.delete('/api/auth');            // a dead session is a different page, not a dead panel
  return [...fams].sort();
}

async function walk(ctx, base, ids) {
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
  await page.goto(`${base}/dashboard#home`, { waitUntil: 'load' });
  const seen = new Map();   // `${view}/${panel}` -> { honest, text }
  for (const id of ids) {
    await page.evaluate((v) => { location.hash = '#' + v; }, id);
    await page.waitForTimeout(900);
    const panels = await page.$$eval('section.panel, .panel', (els) => els.map((el) => ({
      id: el.id || (el.querySelector('[id]') || {}).id || '?',
      honest: !!el.querySelector('.state-block, .skel'),
      text: (el.innerText || '').replace(/\s+/g, ' ').slice(0, 400),
    })));
    panels.forEach((p, i) => seen.set(`${id}/${p.id}#${i}`, p));
  }
  await page.close();
  return { seen, errors };
}

test('with one API family dead at a time, no panel invents a zero for it', SKIP ? { skip: SKIP } : {}, async (t) => {
  const ids = viewIds();
  const app = express();
  app.get('/dashboard', (req, res) => res.sendFile(path.join(PUBLIC, 'dashboard.html')));
  app.use(express.static(PUBLIC));
  const server = http.createServer(app); await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await pw.chromium.launch({ executablePath: CHROMIUM, headless: true });
  const findings = []; const errors = [];
  async function context(deadFamily, requested) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await ctx.addCookies([{ name: 'rc_auth', value: '1', url: base }]);
    await ctx.route('**/api/**', (route) => {
      const u = new URL(route.request().url());
      if (requested) requested.add(u.pathname);
      if (deadFamily && (u.pathname === deadFamily || u.pathname.startsWith(deadFamily + '/') || u.pathname.startsWith(deadFamily + '?'))) {
        return route.fulfill({ status: 503, contentType: 'application/json', body: '{"ok":false,"error":"unavailable"}' });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixtureFor(u.pathname)) });
    });
    await ctx.route('**/api/stream*', (route) => route.fulfill({ status: 200, contentType: 'text/event-stream', body: ': ok\n\n' }));
    await ctx.route(/^https?:\/\/(?!127\.0\.0\.1)/, (route) => route.abort());
    return ctx;
  }
  try {
    const requested = new Set();
    const live = await context(null, requested);
    const baseline = await walk(live, base, ids);
    await live.close();
    errors.push(...baseline.errors.map((e) => `all live: ${e}`));
    const families = apiFamilies(requested);
    assert.ok(families.length >= 8, `expected the families the page reads on render, got ${families.length}: ${families.join(' ')}`);
    t.diagnostic(`families read on render: ${families.join(' ')}`);
    for (const fam of families) {
      const ctx = await context(fam);
      const dead = await walk(ctx, base, ids);
      await ctx.close();
      errors.push(...dead.errors.map((e) => `${fam} dead: ${e}`));
      for (const [key, p] of dead.seen) {
        if (p.honest) continue;
        const m = p.text.match(ZERO);
        if (!m) continue;
        const before = baseline.seen.get(key);
        if (before && ZERO.test(before.text)) continue;     // a zero that was there with every source live came from data
        findings.push(`${fam} dead -> ${key}: "${m[0]}" in: ${p.text.slice(0, 140)}`);
      }
      t.diagnostic(`${fam} dead: ${findings.length} finding(s) so far`);
    }
    t.diagnostic(`${families.length} families x ${ids.length} views; ${findings.length} finding(s)`);
  } finally { await browser.close(); server.close(); }
  assert.deepStrictEqual(errors, [], 'page errors with one family dead:\n  ' + errors.join('\n  '));
  assert.deepStrictEqual(findings, [], 'zero-like claims manufactured from one failed read:\n  ' + findings.join('\n  '));
});

test('the deep pass reports itself as SKIPPED, never as passed, when it does not run', () => {
  if (SKIP) console.log(`# DEEP SMOKE SKIPPED: ${SKIP}`);
  assert.ok(true);
});
