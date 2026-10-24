'use strict';
/**
 * Conservation-brake status. The flag defaults off. A non-admin is 404
 * whether the flag is off or on. An admin with the flag off sees the
 * preview banner. An admin with the flag on sees the status without it.
 * The payload carries no bond size and the server does not sign.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;
delete process.env.CGC_ENABLED;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const { codeOnly } = require('./helpers/code_only');
const { pool } = require('../db');
const { FLAG, BANNER, flag_on, pageGate, sendPage } = require('../lib/cgc_flag');
const reading = require('../lib/cgc_reading');

const APP = path.join(__dirname, '..');
const CHAINS = JSON.parse(fs.readFileSync(reading.CHAINS_FILE, 'utf8'));
const HTML = fs.readFileSync(path.join(APP, 'public', 'cgc.html'), 'utf8');
const PAGE_JS = fs.readFileSync(path.join(APP, 'public', 'js', 'cgc.js'), 'utf8');
const SERVER = codeOnly(fs.readFileSync(path.join(APP, 'server.js'), 'utf8'));

const ADMIN = 9101;
const STRANGER = 9102;
pool.users.push({ id: ADMIN, email: 'cgc-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: STRANGER, email: 'cgc-free@example.com', plan: 'free', token_epoch: 0 });
const ADMIN_TOKEN = jwt.sign({ user_id: ADMIN, email: 'cgc-admin@example.com', epoch: 0 }, process.env.JWT_SECRET);
const FREE_TOKEN = jwt.sign({ user_id: STRANGER, email: 'cgc-free@example.com', epoch: 0 }, process.env.JWT_SECRET);

const MAINNETS = [1, 10, 8453, 130, 57073, 42161, 56, 43114, 143, 999, 9745, 5000];
const MONEY = /\$|rclaw|stake|usd|dollar|bond/i;

function app() {
  const server = express();
  server.use(pageGate);
  server.use(express.static(path.join(APP, 'public')));
  server.get('/cgc', (req, res, next) => { sendPage(req, res).catch(next); });
  server.use('/api/cgc', require('../routes/cgc'));
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

test('the flag is off unless the value is 1, true, on, or yes', () => {
  assert.equal(FLAG, 'CGC_ENABLED');
  assert.equal(flag_on({}), false);
  assert.equal(flag_on({ CGC_ENABLED: '' }), false);
  assert.equal(flag_on({ CGC_ENABLED: '0' }), false);
  assert.equal(flag_on({ CGC_ENABLED: 'false' }), false);
  assert.equal(flag_on({ CGC_ENABLED: 'no' }), false);
  assert.equal(flag_on({ CGC_ENABLED: '  TRUE  ' }), true);
  for (const word of ['1', 'true', 'on', 'yes']) {
    assert.equal(flag_on({ CGC_ENABLED: word }), true, word);
  }
  const src = fs.readFileSync(path.join(APP, 'lib', 'cgc_flag.js'), 'utf8');
  assert.doesNotMatch(codeOnly(src), /function enabled\b/);
});

test('a non-admin is 404 with the flag off and with the flag on', async () => {
  const prev = process.env.CGC_ENABLED;
  try {
    delete process.env.CGC_ENABLED;
    for (const token of [undefined, FREE_TOKEN]) {
      const page = await req(base, '/cgc', token);
      assert.equal(page.status, 404);
      assert.equal(page.body, 'Not found');
      const file = await req(base, '/cgc.html', token);
      assert.equal(file.status, 404);
      const script = await req(base, '/js/cgc.js', token);
      assert.equal(script.status, 404);
      const api = await req(base, '/api/cgc/status', token);
      assert.equal(api.status, 404);
      assert.deepEqual(JSON.parse(api.body), { error: 'Not found' });
    }
    process.env.CGC_ENABLED = 'on';
    const page = await req(base, '/cgc', FREE_TOKEN);
    assert.equal(page.status, 404);
    const api = await req(base, '/api/cgc/status', FREE_TOKEN);
    assert.equal(api.status, 404);
    const anon = await req(base, '/api/cgc/status');
    assert.equal(anon.status, 404);
  } finally {
    if (prev == null) delete process.env.CGC_ENABLED;
    else process.env.CGC_ENABLED = prev;
  }
});

test('an admin sees the preview banner only while the flag is off', async () => {
  const prev = process.env.CGC_ENABLED;
  try {
    delete process.env.CGC_ENABLED;
    const off = await req(base, '/api/cgc/status', ADMIN_TOKEN);
    assert.equal(off.status, 200);
    assert.equal(off.cache, 'no-store');
    const offBody = JSON.parse(off.body);
    assert.equal(offBody.preview, true);
    assert.equal(offBody.banner, BANNER);
    assert.equal(BANNER, 'Admin preview. Not public.');
    const page = await req(base, '/cgc', ADMIN_TOKEN);
    assert.equal(page.status, 200);
    assert.match(page.body, /Conservation brake/);
    assert.doesNotMatch(page.body, /Admin preview/);

    process.env.CGC_ENABLED = 'yes';
    const on = await req(base, '/api/cgc/status', ADMIN_TOKEN);
    assert.equal(on.status, 200);
    const onBody = JSON.parse(on.body);
    assert.equal(onBody.preview, false);
    assert.equal(onBody.banner, null);
    assert.equal(JSON.stringify(onBody).includes(BANNER), false);
  } finally {
    if (prev == null) delete process.env.CGC_ENABLED;
    else process.env.CGC_ENABLED = prev;
  }
});

test('the page names its controls and loads api-retry first', () => {
  assert.match(HTML, /src="\/js\/api-retry\.js\?v=3"/);
  assert.ok(HTML.indexOf('/js/api-retry.js') < HTML.indexOf('/js/cgc.js'));
  assert.equal((HTML.match(/<script\b/g) || []).length, 2);
  assert.doesNotMatch(HTML, /\b(async|defer)\b/);
  assert.doesNotMatch(HTML, /aria-label=/);
  assert.doesNotMatch(HTML, /data-i18n/);
  assert.match(HTML, /role="main"/);
  assert.match(HTML, /styles\.css\?v=52/);
  assert.match(HTML, /rel="canonical" href="https:\/\/www\.humanoid-traders\.com\/cgc"/);
  assert.equal(HTML.match(/property="og:site_name" content="([^"]*)"/)[1], 'Humanoid Traders');
  assert.match(HTML, /name="robots" content="noindex"/);
  assert.match(PAGE_JS, /AbortSignal\.timeout\(/);
  assert.doesNotMatch(PAGE_JS, /console\.error/);
  assert.doesNotMatch(codeOnly(PAGE_JS), /parseFloat\([^)]*\)\s*\|\|\s*0/);
});

test('the page gate sits ahead of static, and /cgc is a page route', () => {
  const gateAt = SERVER.indexOf("require('./lib/cgc_flag').pageGate");
  const staticAt = SERVER.indexOf('express.static');
  assert.ok(gateAt > 0 && gateAt < staticAt, 'the gate is mounted before static');
  assert.match(SERVER, /app\.get\('\/cgc'/);
  assert.match(SERVER, /app\.use\('\/api\/cgc'/);
  const unlinked = JSON.parse(fs.readFileSync(path.join(__dirname, 'unlinked_routes.json'), 'utf8'));
  assert.ok(unlinked.routes['/cgc'].length >= 40);
  const landing = fs.readFileSync(path.join(APP, 'public', 'index.html'), 'utf8');
  assert.equal(landing.includes('href="/cgc"'), false);
  const sitemap = fs.readFileSync(path.join(APP, 'lib', 'sitemap.js'), 'utf8');
  assert.equal(sitemap.includes("'/cgc'"), false);
});

test('windows match chains.json, and a mainnet chain id is refused', () => {
  const body = reading.statusPayload({ preview: false });
  assert.equal(body.unreadable, false);
  assert.equal(body.chains.length, CHAINS.chains.length);
  for (let i = 0; i < body.chains.length; i++) {
    const src = CHAINS.chains[i];
    const row = body.chains[i];
    assert.equal(row.name, src.name);
    assert.equal(row.windows.w_brake, src.w_brake);
    assert.equal(row.windows.w_prov, src.w_prov);
    assert.equal(row.windows.w_final, src.w_final);
    assert.equal(row.windows.w_final_max, src.w_final_max);
    assert.ok(row.windows.w_final_max > row.windows.w_final);
    assert.equal(Object.prototype.hasOwnProperty.call(row, 'mainnet_chain_id'), false);
    if (src.chain_id == null) assert.equal(row.chain_id, null);
    else {
      assert.equal(row.chain_id, src.chain_id);
      assert.equal(reading.isTestnetChainId(src.chain_id, CHAINS.chains), true);
    }
    assert.equal(reading.isTestnetChainId(src.mainnet_chain_id, CHAINS.chains), false);
  }
  for (const id of MAINNETS) {
    assert.equal(reading.isTestnetChainId(id, CHAINS.chains), false, String(id));
  }
  assert.equal(reading.isTestnetChainId(5003, CHAINS.chains), false);
  assert.equal(reading.isTestnetChainId('11155111', CHAINS.chains), false);
  const factors = Object.fromEntries(body.inflow.map((row) => [row.floor, row]));
  assert.deepEqual(factors[0], { floor: 0, num: 0, den: 1 });
  assert.deepEqual(factors[1], { floor: 1, num: 1, den: 10 });
  assert.deepEqual(factors[2], { floor: 2, num: 1, den: 2 });
  assert.deepEqual(factors[3], { floor: 3, num: 1, den: 1 });
});

test('the kelp figures are a scenario, and the payload has no money', () => {
  const body = reading.statusPayload({ preview: true });
  assert.equal(body.kelp.kind, 'scenario');
  assert.equal(body.kelp.forged_release_units, 116500);
  assert.equal(body.kelp.dest_nonce, 308);
  assert.equal(body.kelp.source_outbound_nonce, 307);
  assert.equal(body.kelp.supplied_units, 89567);
  assert.equal(body.kelp.first_window_units, 11650);
  assert.equal(body.kelp.first_window_units * 10, body.kelp.forged_release_units);
  assert.match(body.kelp.note, /not a measurement/);
  assert.equal(body.server_signs, false);
  assert.deepEqual(body.wrapper, { symbol: 'gwrsETH', testnet_only: true, public: false });
  assert.deepEqual(body.proof_kinds.rejected, [
    'groth16', 'plonk', 'bridge verifier', 'unimplemented hash proof',
  ]);
  const flat = JSON.stringify(body);
  assert.doesNotMatch(flat, MONEY);
  const forbidden = [
    'pnl', 'pnl_usd', 'net_pnl_usd', 'realized_pnl', 'unrealized_pnl',
    'equity', 'equity_usd', 'balance', 'balance_usd', 'margin', 'margin_usd',
    'notional', 'notional_usd', 'amount_usd', 'usd_value', 'vusdt', 'collateral_usd',
  ];
  const keys = [];
  JSON.stringify(body, (k, v) => { keys.push(k); return v; });
  for (const name of forbidden) assert.equal(keys.includes(name), false, name);
});

test('an unreadable chain file is not an empty list', () => {
  const missing = reading.statusPayload({ preview: false, file: path.join(__dirname, 'no-such-chains.json') });
  assert.equal(missing.unreadable, true);
  assert.equal(missing.chains, null);
  assert.match(missing.note, /could not be read/);
  const broken = path.join(__dirname, 'cgc-broken.json');
  fs.writeFileSync(broken, '{');
  try {
    const bad = reading.statusPayload({ preview: false, file: broken });
    assert.equal(bad.unreadable, true);
    assert.equal(bad.chains, null);
  } finally {
    fs.unlinkSync(broken);
  }
});

test('the server files do not sign', () => {
  const files = [
    'lib/cgc_flag.js', 'lib/cgc_reading.js', 'routes/cgc.js', 'public/js/cgc.js',
  ];
  for (const rel of files) {
    const src = codeOnly(fs.readFileSync(path.join(APP, rel), 'utf8'));
    assert.doesNotMatch(src, /signTransaction|private_key|privateKey|sendRawTransaction/, rel);
  }
});
