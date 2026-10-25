'use strict';
/**
 * The dashboard Tools link is the /tools gate, asked again.
 *
 * /tools stays 404 unless the caller is an admin preview (flag off,
 * flag_on_for) or the operator (flag on). The nav item is that same
 * boolean from GET /api/nav/tools. A logged-out reader and a non-admin
 * never see the link, and the page is still 404 for them. The bar
 * keeps every existing view.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.SAR_ORACLE_ENABLED;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const sarFlag = require('../lib/sar_oracle_flag');
const preview = require('../lib/admin_preview');

const ADMIN_ID = 93021;
const USER_ID = 93022;
pool.users.push({ id: ADMIN_ID, email: 'tools-nav-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: USER_ID, email: 'tools-nav-user@example.com', plan: 'free', token_epoch: 0 });

function tokenFor(id, email) {
  return jwt.sign({ user_id: id, email, epoch: 0 }, process.env.JWT_SECRET);
}
const adminToken = tokenFor(ADMIN_ID, 'tools-nav-admin@example.com');
const userToken = tokenFor(USER_ID, 'tools-nav-user@example.com');

const DASH = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const CSS = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');
const HTML = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}
function close(server) {
  return new Promise((resolve) => server.close(resolve));
}
async function hit(server, urlPath, { token } = {}) {
  const headers = {};
  if (token) headers.authorization = 'Bearer ' + token;
  const res = await fetch(`http://127.0.0.1:${server.address().port}${urlPath}`, { headers });
  const raw = await res.text();
  let body = raw;
  try { body = JSON.parse(raw); } catch (e) { body = raw; }
  return { status: res.status, body, raw };
}

function appFor() {
  const app = express();
  app.use(sarFlag.pageGate);
  app.get('/tools', (req, res) => { sarFlag.sendPage(req, res); });
  app.use('/api/nav/tools', require('../routes/nav_tools'));
  return app;
}

function viewIds() {
  const start = DASH.indexOf('const VIEWS = [');
  const block = DASH.slice(start, DASH.indexOf('];', start));
  return [...block.matchAll(/id:\s*'([a-z]+)'/g)].map((m) => m[1]);
}

function navApi() {
  const start = DASH.indexOf('const VIEWS = [');
  const viewsEnd = DASH.indexOf('];', start);
  const views = DASH.slice(start, viewsEnd + 2);
  const openStart = DASH.indexOf('function toolsNavOpen(');
  const openEnd = DASH.indexOf('\n  }\n', openStart);
  const navStart = DASH.indexOf('function navHtml(');
  const navEnd = DASH.indexOf('function renderNav(');
  assert.ok(start > 0 && openStart > 0 && openEnd > openStart && navStart > openEnd && navEnd > navStart);
  return new Function(
    `${views}\nlet toolsOpen = false;\n${DASH.slice(openStart, openEnd + 4)}\n${DASH.slice(navStart, navEnd)}\n`
    + 'return { toolsNavOpen, navHtml, syncTabbarMore, wireTabbarMore, setTools(v) { toolsOpen = v; } };'
  )();
}

function hrefs(html) {
  return [...html.matchAll(/<a href="([^"]+)"/g)].map((m) => m[1]);
}

test('a logged-out reader and a non-admin never get the Tools link, and /tools stays 404', async () => {
  delete process.env.SAR_ORACLE_ENABLED;
  const server = await listen(appFor());
  try {
    for (const token of [undefined, userToken]) {
      const page = await hit(server, '/tools', { token });
      const nav = await hit(server, '/api/nav/tools', { token });
      assert.equal(page.status, 404);
      assert.equal(nav.status, 200);
      assert.equal(nav.body.tools, false);
      assert.equal(typeof nav.body.tools, 'boolean');
    }
    process.env.SAR_ORACLE_ENABLED = '1';
    for (const token of [undefined, userToken]) {
      const page = await hit(server, '/tools', { token });
      const nav = await hit(server, '/api/nav/tools', { token });
      assert.equal(page.status, 404);
      assert.equal(nav.body.tools, false);
    }
  } finally {
    delete process.env.SAR_ORACLE_ENABLED;
    await close(server);
  }
});

test('an admin preview (flag_on_for, flag off) sees the Tools link, and so does the operator when the flag is on', async () => {
  delete process.env.SAR_ORACLE_ENABLED;
  const server = await listen(appFor());
  try {
    const req = { headers: { authorization: 'Bearer ' + adminToken } };
    assert.equal(await preview.flag_on_for(req, 'SAR_ORACLE_ENABLED'), true);
    const previewNav = await hit(server, '/api/nav/tools', { token: adminToken });
    const previewPage = await hit(server, '/tools', { token: adminToken });
    assert.equal(previewNav.status, 200);
    assert.equal(previewNav.body.tools, true);
    assert.equal(previewPage.status, 200);
    assert.equal(await sarFlag.openFor(req), true);

    process.env.SAR_ORACLE_ENABLED = '1';
    const openNav = await hit(server, '/api/nav/tools', { token: adminToken });
    const openPage = await hit(server, '/tools', { token: adminToken });
    const userNav = await hit(server, '/api/nav/tools', { token: userToken });
    const userPage = await hit(server, '/tools', { token: userToken });
    assert.equal(openNav.body.tools, true);
    assert.equal(openPage.status, 200);
    assert.equal(userNav.body.tools, false);
    assert.equal(userPage.status, 404);
    assert.equal(await preview.flag_on_for({ headers: {} }, 'SAR_ORACLE_ENABLED'), true);
    assert.equal(await sarFlag.openFor({ headers: {} }), false);
  } finally {
    delete process.env.SAR_ORACLE_ENABLED;
    await close(server);
  }
});

test('the bar markup keeps every existing view, and Tools sits after Home only when the gate is open', () => {
  const ids = viewIds();
  assert.ok(ids.length >= 23, `expected the full rail, found ${ids.length}`);
  assert.equal(ids.includes('tools'), false, 'Tools is a page, not a hash view');
  assert.equal(ids[0], 'home');
  const nav = navApi();
  nav.setTools(false);
  const closed = hrefs(nav.navHtml('signals'));
  assert.deepEqual(closed, ids.map((id) => '#' + id));
  assert.equal(closed.includes('/tools'), false);
  assert.match(nav.navHtml('signals'), /href="#signals" aria-current="page"/);

  nav.setTools(true);
  const open = hrefs(nav.navHtml('home'));
  assert.equal(open[0], '#home');
  assert.equal(open[1], '/tools');
  assert.equal(open[2], '#chat');
  for (const id of ids) assert.ok(open.includes('#' + id), `missing #${id}`);
  assert.equal(open.length, ids.length + 1);
  assert.match(nav.navHtml('home'), /data-i18n="nav\.tools"/);
  assert.match(DASH, /getElementById\('railNav'\)\.innerHTML = navHtml/);
  assert.match(DASH, /getElementById\('tabbarNav'\)\.innerHTML = navHtml/);
});

test('the client shows Tools only for the boolean the server sent', () => {
  const nav = navApi();
  assert.equal(nav.toolsNavOpen({ tools: true }), true);
  assert.equal(nav.toolsNavOpen({ tools: false }), false);
  assert.equal(nav.toolsNavOpen({ tools: 'true' }), false);
  assert.equal(nav.toolsNavOpen({ tools: ['catalog'] }), false);
  assert.equal(nav.toolsNavOpen({ plan: 'admin' }), false);
  assert.equal(nav.toolsNavOpen(null), false);
  assert.equal(nav.toolsNavOpen(undefined), false);
  assert.match(DASH, /fetchJSON\('\/api\/nav\/tools'/);
  assert.doesNotMatch(DASH, /SAR_ORACLE_ENABLED/);
  assert.match(DASH, /r\.ok && !r\.unreadable && toolsNavOpen\(r\.data\)/);
});

test('the phone bar names a way to scroll, and the desktop rail does not gain one', () => {
  assert.match(HTML, /id="tabbarMore"/);
  assert.match(HTML, /type="button"/);
  assert.match(HTML, /hidden/);
  assert.match(HTML, /aria-controls="tabbarNav"/);
  assert.match(HTML, /data-i18n-attr="aria-label:a11y\.tabbar_more"/);
  assert.match(DASH, /scrollIntoView\(\{ inline: 'center', block: 'nearest' \}\)/);
  assert.match(DASH, /scrollBy\(\{ left: step/);
  const bar = CSS.slice(CSS.indexOf('.tabbar {'), CSS.indexOf('}', CSS.indexOf('.tabbar {')));
  assert.match(bar, /overflow-x:\s*auto/);
  assert.match(bar, /mask-image: linear-gradient\(to right/);
  assert.match(CSS, /\.tabbar-more \{[^}]*display:\s*none/s);
  assert.match(CSS, /@media \(max-width: 1023px\) \{[^}]*\.tabbar-more:not\(\[hidden\]\) \{ display: flex; \}/s);
  const desk = CSS.slice(CSS.indexOf('@media (min-width: 1024px)'), CSS.indexOf('@media (min-width: 640px)'));
  assert.match(desk, /\.tabbar \{ display: none; \}/);
  assert.doesNotMatch(desk, /\.tabbar-more/);

  const nav = navApi();
  const classes = new Set();
  const el = {
    scrollWidth: 900,
    clientWidth: 320,
    scrollLeft: 0,
    classList: {
      toggle(name, on) { if (on) classes.add(name); else classes.delete(name); },
    },
    addEventListener(type, fn) {
      el.listeners = el.listeners || {};
      (el.listeners[type] = el.listeners[type] || []).push(fn);
    },
    scrollBy(opts) { el.scrolled = opts; },
  };
  const btn = { hidden: true, addEventListener(type, fn) { btn.click = fn; } };
  const prevDoc = global.document;
  const prevWin = global.window;
  global.document = { getElementById(id) { return id === 'tabbarNav' ? el : btn; } };
  global.window = {
    matchMedia() { return { matches: true }; },
    addEventListener() {},
  };
  try {
    nav.syncTabbarMore();
    assert.equal(btn.hidden, false);
    assert.equal(classes.has('tabbar--end'), false);
    el.scrollLeft = 600;
    nav.syncTabbarMore();
    assert.equal(btn.hidden, true);
    assert.equal(classes.has('tabbar--end'), true);
    el.scrollLeft = 0;
    el.scrollWidth = 320;
    nav.syncTabbarMore();
    assert.equal(btn.hidden, true);
    el.scrollWidth = 900;
    nav.wireTabbarMore();
    btn.click();
    assert.equal(el.scrolled.behavior, 'auto');
    assert.ok(el.scrolled.left >= 128);
  } finally {
    global.document = prevDoc;
    global.window = prevWin;
  }
});
