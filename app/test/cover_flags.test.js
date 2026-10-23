'use strict';
/**
 * Cover drafts are off, unlinked, and not an offer.
 *
 * Not offered to customers. Licence review pending. Do not enable in production.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const { codeOnly } = require('./helpers/code_only');
const flags = require('../../contracts/cover/flags.cjs');
const page = require('../lib/cover_page');
const { STATIC_PATHS } = require('../lib/sitemap');

const HOLD = 'Not offered to customers. Licence review pending. Do not enable in production.';
const PUBLIC = path.join(__dirname, '..', 'public');
const ADL_HTML = fs.readFileSync(path.join(PUBLIC, 'cover-adl.html'), 'utf8');
const STOP_HTML = fs.readFileSync(path.join(PUBLIC, 'cover-stopgap.html'), 'utf8');
const PAGE_JS = fs.readFileSync(path.join(PUBLIC, 'js', 'cover.js'), 'utf8');
const MONEY = ['pnl', 'pnl_usd', 'balance', 'balance_usd', 'equity', 'margin', 'notional', 'amount_usd', 'collateral_usd'];

function app() {
  const a = express();
  a.use(page.pageGate);
  a.use(express.static(PUBLIC));
  a.get('/cover-adl', (req, res) => { page.sendPage(res, 'adl'); });
  a.get('/cover-stopgap', (req, res) => { page.sendPage(res, 'stopgap'); });
  a.use('/api/cover', require('../routes/cover'));
  return a;
}

async function listen() {
  const server = await new Promise((resolve) => {
    const s = app().listen(0, '127.0.0.1', () => resolve(s));
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  return {
    server,
    get: (p) => fetch(base + p),
  };
}

function clearFlags() {
  delete process.env.COVER_ADL_ENABLED;
  delete process.env.COVER_STOPGAP_ENABLED;
}

test('the pages carry the hold, load api-retry first, and do not market an offer', () => {
  for (const html of [ADL_HTML, STOP_HTML]) {
    assert.ok(html.includes(HOLD));
    assert.match(html, /src="\/js\/api-retry\.js\?v=3"/);
    assert.ok(html.indexOf('/js/api-retry.js') < html.indexOf('/js/cover.js'));
    assert.equal((html.match(/<script\b/g) || []).length, 2);
    assert.match(html, /name="robots" content="noindex, nofollow"/);
    assert.equal(html.match(/property="og:site_name" content="([^"]*)"/)[1], 'Humanoid Traders');
    assert.ok(!/guaranteed|risk-free|risk free|\$RCLAW|stake/i.test(html));
    assert.ok(!/\$\s?\d/.test(html));
  }
  assert.match(ADL_HTML, /rel="canonical" href="https:\/\/www\.humanoid-traders\.com\/cover-adl"/);
  assert.match(STOP_HTML, /rel="canonical" href="https:\/\/www\.humanoid-traders\.com\/cover-stopgap"/);
  assert.ok(PAGE_JS.includes(HOLD));
  assert.ok(!/innerHTML|guaranteed|\$RCLAW/.test(PAGE_JS));
  const paths = STATIC_PATHS.map((s) => s.path);
  assert.ok(!paths.includes('/cover-adl'));
  assert.ok(!paths.includes('/cover-stopgap'));
});

test('the server mounts the gate ahead of static and registers both pages', () => {
  const raw = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  const serverSrc = codeOnly(raw);
  const gateAt = serverSrc.indexOf("require('./lib/cover_page').pageGate");
  const staticAt = serverSrc.indexOf('express.static');
  assert.ok(gateAt > 0 && gateAt < staticAt, 'the gate is mounted before static');
  assert.match(serverSrc, /app\.get\('\/cover-adl', \(req, res\) => \{ require\('\.\/lib\/cover_page'\)\.sendPage\(res, 'adl'\); \}\);/);
  assert.match(serverSrc, /app\.get\('\/cover-stopgap', \(req, res\) => \{ require\('\.\/lib\/cover_page'\)\.sendPage\(res, 'stopgap'\); \}\);/);
  assert.match(serverSrc, /app\.use\('\/api\/cover', require\('\.\/routes\/cover'\)\)/);
  assert.ok(raw.includes(HOLD));
});

test('a missing flag, and every value other than 1 or true, is off', () => {
  assert.equal(flags.flagOn('COVER_ADL_ENABLED', {}), false);
  assert.equal(flags.adlOn({}), false);
  assert.equal(flags.stopGapOn({}), false);
  assert.equal(flags.adlOn({ COVER_STOPGAP_ENABLED: '1' }), false);
  assert.equal(flags.stopGapOn({ COVER_ADL_ENABLED: 'true' }), false);
  for (const value of ['1', 'true', ' TRUE ']) {
    assert.equal(flags.adlOn({ COVER_ADL_ENABLED: value }), true, value);
    assert.equal(flags.stopGapOn({ COVER_STOPGAP_ENABLED: value }), true, value);
  }
  for (const value of ['false', '0', 'yes', 'on', '']) {
    assert.equal(flags.adlOn({ COVER_ADL_ENABLED: value }), false, value);
    assert.equal(flags.stopGapOn({ COVER_STOPGAP_ENABLED: value }), false, value);
  }
});

test('flags off answer 404, and a flag that is on still says the cover is not offered', async () => {
  const prevAdl = process.env.COVER_ADL_ENABLED;
  const prevStop = process.env.COVER_STOPGAP_ENABLED;
  const live = await listen();
  try {
    clearFlags();
    for (const p of ['/cover-adl', '/cover-stopgap', '/cover-adl.html', '/cover-stopgap.html',
      '/js/cover.js', '/api/cover/adl', '/api/cover/stopgap']) {
      const off = await live.get(p);
      assert.equal(off.status, 404, p);
    }

    process.env.COVER_ADL_ENABLED = '1';
    const adl = await live.get('/cover-adl');
    assert.equal(adl.status, 200);
    assert.match(await adl.text(), /Licence review pending/);
    const adlHtml = await live.get('/cover-adl.html');
    assert.equal(adlHtml.status, 200);
    const stop = await live.get('/cover-stopgap');
    assert.equal(stop.status, 404);
    const script = await live.get('/js/cover.js');
    assert.equal(script.status, 200);
    const api = await live.get('/api/cover/adl');
    assert.equal(api.status, 200);
    assert.equal(api.headers.get('cache-control'), 'no-store');
    const body = await api.json();
    assert.equal(body.offered, false);
    assert.equal(body.reason, 'licence_review_pending');
    assert.deepEqual(body.chains, ['hyperevm-testnet', 'base-sepolia']);
    assert.equal(body.notice, HOLD);
    for (const key of MONEY) assert.equal(Object.hasOwn(body, key), false, key);
    assert.ok(!/\$\s?\d/.test(JSON.stringify(body)));
    const other = await live.get('/api/cover/stopgap');
    assert.equal(other.status, 404);

    delete process.env.COVER_ADL_ENABLED;
    process.env.COVER_STOPGAP_ENABLED = ' TRUE ';
    const stopOn = await live.get('/api/cover/stopgap');
    assert.equal(stopOn.status, 200);
    const stopBody = await stopOn.json();
    assert.equal(stopBody.offered, false);
    assert.equal(stopBody.notice, HOLD);
    assert.equal((await live.get('/api/cover/adl')).status, 404);
    assert.equal((await live.get('/cover-adl')).status, 404);
    assert.equal((await live.get('/cover-stopgap')).status, 200);

    process.env.COVER_ADL_ENABLED = 'yes';
    process.env.COVER_STOPGAP_ENABLED = 'on';
    assert.equal((await live.get('/cover-adl')).status, 404);
    assert.equal((await live.get('/cover-stopgap')).status, 404);
    assert.equal((await live.get('/js/cover.js')).status, 404);
  } finally {
    live.server.close();
    if (prevAdl == null) delete process.env.COVER_ADL_ENABLED;
    else process.env.COVER_ADL_ENABLED = prevAdl;
    if (prevStop == null) delete process.env.COVER_STOPGAP_ENABLED;
    else process.env.COVER_STOPGAP_ENABLED = prevStop;
  }
});
