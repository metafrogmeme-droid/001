'use strict';
/**
 * Quantum custody haircut. The flag defaults off. A non-admin is 404
 * whether the flag is off or on. An admin with the flag off sees the
 * preview banner. Scores stay estimates. The server does not sign.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;
delete process.env.QCH_ENABLED;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const { codeOnly } = require('./helpers/code_only');
const { pool } = require('../db');
const { FLAG, BANNER, flagOn, pageGate, sendPage } = require('../lib/qch_flag');
const reading = require('../lib/qch_reading');
const pageApi = require('../public/js/qch.js');

const APP = path.join(__dirname, '..');
const HTML = fs.readFileSync(path.join(APP, 'public', 'qch.html'), 'utf8');
const PAGE_JS = fs.readFileSync(path.join(APP, 'public', 'js', 'qch.js'), 'utf8');
const SERVER = codeOnly(fs.readFileSync(path.join(APP, 'server.js'), 'utf8'));
const PUBLISHED = JSON.parse(fs.readFileSync(reading.READING_FILE, 'utf8'));

const ADMIN = 9301;
const STRANGER = 9302;
pool.users.push({ id: ADMIN, email: 'qch-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: STRANGER, email: 'qch-free@example.com', plan: 'free', token_epoch: 0 });
const ADMIN_TOKEN = jwt.sign({ user_id: ADMIN, email: 'qch-admin@example.com', epoch: 0 }, process.env.JWT_SECRET);
const FREE_TOKEN = jwt.sign({ user_id: STRANGER, email: 'qch-free@example.com', epoch: 0 }, process.env.JWT_SECRET);

function app() {
  const server = express();
  server.use(pageGate);
  server.use(express.static(path.join(APP, 'public')));
  server.get('/qch', (req, res, next) => { sendPage(req, res).catch(next); });
  server.use('/api/qch', require('../routes/qch'));
  return server;
}

function listen(server) {
  return new Promise((resolve) => {
    const s = server.listen(0, '127.0.0.1', () => resolve(s));
  });
}

function req(base, urlPath, token) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${urlPath}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        type: String(res.headers['content-type'] || ''),
        cache: String(res.headers['cache-control'] || ''),
        body: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    r.on('error', reject);
    r.end();
  });
}

let server;
let base;

test.before(async () => {
  server = await listen(app());
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('the flag is off unless the value is 1 or true', () => {
  assert.equal(FLAG, 'QCH_ENABLED');
  assert.equal(flagOn({}), false);
  assert.equal(flagOn({ QCH_ENABLED: '' }), false);
  assert.equal(flagOn({ QCH_ENABLED: 'yes' }), false);
  assert.equal(flagOn({ QCH_ENABLED: 'on' }), false);
  assert.equal(flagOn({ QCH_ENABLED: '  TRUE  ' }), true);
  assert.equal(flagOn({ QCH_ENABLED: '1' }), true);
  const src = fs.readFileSync(path.join(APP, 'lib', 'qch_flag.js'), 'utf8');
  assert.doesNotMatch(codeOnly(src), /function enabled\b/);
});

test('a non-admin is 404 with the flag off and with the flag on', async () => {
  const prev = process.env.QCH_ENABLED;
  try {
    delete process.env.QCH_ENABLED;
    for (const token of [undefined, FREE_TOKEN]) {
      const page = await req(base, '/qch', token);
      assert.equal(page.status, 404);
      assert.equal(page.body, 'Not found');
      const file = await req(base, '/qch.html', token);
      assert.equal(file.status, 404);
      const script = await req(base, '/js/qch.js', token);
      assert.equal(script.status, 404);
      const api = await req(base, '/api/qch/score', token);
      assert.equal(api.status, 404);
      assert.deepEqual(JSON.parse(api.body), { error: 'Not found' });
    }
    process.env.QCH_ENABLED = 'true';
    const page = await req(base, '/qch', FREE_TOKEN);
    assert.equal(page.status, 404);
    const api = await req(base, '/api/qch/score', FREE_TOKEN);
    assert.equal(api.status, 404);
    const anon = await req(base, '/api/qch/policy?scenario=clear');
    assert.equal(anon.status, 404);
  } finally {
    if (prev == null) delete process.env.QCH_ENABLED;
    else process.env.QCH_ENABLED = prev;
  }
});

test('an admin sees the preview banner only while the flag is off', async () => {
  const prev = process.env.QCH_ENABLED;
  try {
    delete process.env.QCH_ENABLED;
    const off = await req(base, '/api/qch/score', ADMIN_TOKEN);
    assert.equal(off.status, 200);
    assert.equal(off.cache, 'no-store');
    const offBody = JSON.parse(off.body);
    assert.equal(offBody.banner, BANNER);
    assert.equal(BANNER, 'Admin preview. Not public.');
    assert.equal(offBody.signs, false);
    assert.equal(offBody.key_held, false);
    assert.equal(offBody.label, 'estimate');
    const page = await req(base, '/qch', ADMIN_TOKEN);
    assert.equal(page.status, 200);
    assert.match(page.body, /Quantum custody haircut/);
    assert.doesNotMatch(page.body, /Admin preview/);

    process.env.QCH_ENABLED = '1';
    const on = await req(base, '/api/qch/score', ADMIN_TOKEN);
    assert.equal(on.status, 200);
    const onBody = JSON.parse(on.body);
    assert.equal(onBody.banner, undefined);
    assert.equal(JSON.stringify(onBody).includes(BANNER), false);
  } finally {
    if (prev == null) delete process.env.QCH_ENABLED;
    else process.env.QCH_ENABLED = prev;
  }
});

test('the score is the committed reading and an unknown policy is unreadable', async () => {
  const prev = process.env.QCH_ENABLED;
  try {
    delete process.env.QCH_ENABLED;
    const score = await req(base, '/api/qch/score', ADMIN_TOKEN);
    const body = JSON.parse(score.body);
    assert.equal(body.readable, true);
    const byAsset = {};
    for (const row of body.issuers) byAsset[row.asset] = row;
    for (const asset of ['UBTC', 'WBTC', 'cbBTC', 'LBTC', 'tBTC']) {
      const published = PUBLISHED.issuers.find((row) => row.asset === asset);
      assert.equal(byAsset[asset].qes_bps, published.qes_bps);
      assert.equal(byAsset[asset].label, 'estimate');
      assert.equal(byAsset[asset].estimate, true);
      assert.equal(typeof byAsset[asset].method, 'string');
      assert.ok(byAsset[asset].method.length > 0);
      assert.equal(typeof byAsset[asset].data_source, 'string');
      assert.ok(byAsset[asset].data_source.length > 0);
    }
    assert.equal(JSON.stringify(body).includes('$'), false);
    const clear = await req(base, '/api/qch/policy?scenario=clear', ADMIN_TOKEN);
    const clearBody = JSON.parse(clear.body);
    assert.equal(clear.cache, 'no-store');
    assert.equal(clearBody.readable, true);
    assert.equal(clearBody.decision.action, 'hold');
    assert.equal(clearBody.decision.liquidate, false);
    assert.equal(clearBody.label, 'estimate');
    const missing = await req(base, '/api/qch/policy?scenario=nope', ADMIN_TOKEN);
    const missingBody = JSON.parse(missing.body);
    assert.equal(missingBody.readable, false);
    assert.equal(missingBody.reason, 'unreadable');
    assert.equal(missingBody.decision, null);
    const blank = await req(base, '/api/qch/policy', ADMIN_TOKEN);
    assert.equal(JSON.parse(blank.body).readable, false);
  } finally {
    if (prev == null) delete process.env.QCH_ENABLED;
    else process.env.QCH_ENABLED = prev;
  }
});

test('a number is withheld unless the score is labelled an estimate', () => {
  const row = PUBLISHED.issuers.find((item) => item.asset === 'WBTC');
  const shown = pageApi.scoreLine(row);
  assert.match(shown, /9816 bps, estimate/);
  assert.match(shown, /Source:/);
  assert.equal(pageApi.scoreLine({ qes_bps: 9816, estimate: true, label: 'estimate', method: 'm' }), null);
  assert.equal(pageApi.scoreLine({ ...row, label: 'final' }), null);
  assert.equal(pageApi.scoreLine({ ...row, qes_bps: null }), null);
  assert.equal(pageApi.scoreLine({ ...row, method: '' }), null);
  const policy = pageApi.policyLine({
    label: 'estimate',
    estimate: true,
    method: 'm',
    data_source: 's',
    decision: { action: 'hold', haircut_bps: 0, liquidate: false },
  });
  assert.match(policy, /Haircut 0 bps/);
  assert.match(policy, /No liquidation/);
  assert.equal(pageApi.policyLine({
    label: 'estimate', estimate: true, method: 'm', data_source: 's',
    decision: { action: 'hold', haircut_bps: null, liquidate: false },
  }).includes('9816'), false);
  assert.equal(pageApi.countLine('BNB Chain', 0), 'BNB Chain 0 sats');
  assert.equal(pageApi.countLine('BNB Chain', null), null);
});

test('the page names its controls and loads api-retry first', () => {
  assert.match(HTML, /src="\/js\/api-retry\.js\?v=3"/);
  assert.ok(HTML.indexOf('/js/api-retry.js') < HTML.indexOf('/js/qch.js'));
  assert.equal((HTML.match(/<script\b/g) || []).length, 2);
  assert.doesNotMatch(HTML, /\b(async|defer)\b/);
  assert.doesNotMatch(HTML, /aria-label=/);
  assert.doesNotMatch(HTML, /data-i18n/);
  assert.match(HTML, /role="main"/);
  assert.match(HTML, /styles\.css\?v=53/);
  assert.match(HTML, /rel="canonical" href="https:\/\/www\.humanoid-traders\.com\/qch"/);
  assert.equal(HTML.match(/property="og:site_name" content="([^"]*)"/)[1], 'Humanoid Traders');
  assert.match(HTML, /name="robots" content="noindex"/);
  assert.match(PAGE_JS, /AbortSignal\.timeout\(/);
  assert.doesNotMatch(PAGE_JS, /console\.error/);
  assert.doesNotMatch(codeOnly(PAGE_JS), /parseFloat\([^)]*\)\s*\|\|\s*0/);
  assert.doesNotMatch(PAGE_JS, /\$/);
});

test('the page gate sits ahead of static, and /qch is a page route', () => {
  const gateAt = SERVER.indexOf("require('./lib/qch_flag').pageGate");
  const staticAt = SERVER.indexOf('express.static');
  assert.ok(gateAt > 0 && gateAt < staticAt, 'the gate is mounted before static');
  assert.match(SERVER, /app\.get\('\/qch'/);
  assert.match(SERVER, /app\.use\('\/api\/qch'/);
  const route = fs.readFileSync(path.join(APP, 'routes', 'qch.js'), 'utf8');
  assert.match(route, /optionalAuth/);
  const unlinked = JSON.parse(fs.readFileSync(path.join(__dirname, 'unlinked_routes.json'), 'utf8'));
  assert.ok(unlinked.routes['/qch'].length >= 40);
  const landing = fs.readFileSync(path.join(APP, 'public', 'index.html'), 'utf8');
  assert.equal(landing.includes('href="/qch"'), false);
  const sitemap = fs.readFileSync(path.join(APP, 'lib', 'sitemap.js'), 'utf8');
  assert.equal(sitemap.includes("'/qch'"), false);
});
