'use strict';
/**
 * Sandwich restitution, behind SRB_ENABLED.
 *
 * Off, a caller who is not the operator gets 404 — the same answer as a
 * path this server does not have. The operator still gets the page, and
 * the client is told "Admin preview. Not public." On, the page is public
 * and the status word is Bonded. The bond figure stays on the admin view.
 *
 * The report itself is produced by `python3 -m bot.srb.report`. This file
 * scripts that process so the gate and the copy rules run without a Python
 * environment; the recorded window is pinned in tests/test_srb_shadow.py.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;
delete process.env.SRB_ENABLED;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const cp = require('node:child_process');

const realSpawn = cp.spawnSync;
const spawnCalls = [];
let scripted = null;
cp.spawnSync = function (cmd, args, opts) {
  spawnCalls.push({ cmd, args });
  if (scripted && cmd === 'python3' && args && args[1] === 'bot.srb.report') return scripted;
  return realSpawn.call(this, cmd, args, opts);
};

const jwt = require('jsonwebtoken');
const express = require('express');
const { pool } = require('../db');
const srbFlag = require('../lib/srb_flag');
const srbRouter = require('../routes/srb');
const { PREVIEW, renderSrb } = require('../public/js/srb.js');

const ADMIN_ID = 91001;
const FREE_ID = 91002;
pool.users.push({ id: ADMIN_ID, email: 'srb-op@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: FREE_ID, email: 'srb-free@example.com', plan: 'free', token_epoch: 0 });
const ADMIN = jwt.sign({ user_id: ADMIN_ID, email: 'srb-op@example.com', epoch: 0 }, process.env.JWT_SECRET);
const FREE = jwt.sign({ user_id: FREE_ID, email: 'srb-free@example.com', epoch: 0 }, process.env.JWT_SECRET);

const BOND = 4242424242;
const REPORT = {
  public: {
    mode: 'shadow',
    pays: false,
    cluster: 'fixture',
    providers: [{ provider_id: 'provider-a', status: 'Bonded', policy_hash: 'ab' }],
    signals: [{
      signal_id: 'sig',
      kind: 'public_signal',
      slot_lo: 10,
      slot_hi: 10,
      pool_ref: 'pool_deadbeef',
      impact_bps: 2000,
      restitution: 'recorded',
      restitution_bps: 2000,
      wide: false,
      sentence: 'A same-pool front and back landed around a swap. This is a public signal. Nothing is paid.',
    }],
  },
  admin: {
    amounts: {
      bond_base_units: BOND,
      signals: [{
        signal_id: 'sig',
        reason: null,
        loss_base_units: 2,
        would_record_base_units: 2,
      }],
    },
  },
};

function buildApp() {
  const app = express();
  app.use(srbFlag.pageGate);
  app.get('/srb', (req, res) => srbFlag.sendPage(req, res));
  app.use('/api/srb', srbRouter);
  app.use((req, res) => res.status(200).type('text/plain').send('next'));
  return app;
}

function call(server, urlPath, token) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: '127.0.0.1',
      port: server.address().port,
      path: urlPath,
      method: 'GET',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, text: data }));
    });
    req.on('error', reject);
    req.end();
  });
}

function el() {
  return {
    hidden: false,
    textContent: '',
    className: '',
    children: [],
    appendChild(child) { this.children.push(child); },
  };
}

function fakeRoot() {
  const nodes = {
    '#srb-banner': el(),
    '#srb-provider': el(),
    '#srb-signals': el(),
    '#srb-amounts': el(),
    '#srb-err': el(),
  };
  return {
    nodes,
    querySelector(sel) { return nodes[sel]; },
  };
}

test.before(() => {
  global.document = {
    createElement() { return el(); },
  };
});

test.after(() => {
  cp.spawnSync = realSpawn;
  delete process.env.SRB_ENABLED;
});

test('server.js mounts the gate ahead of static, the page, and the api', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  const gateAt = src.indexOf("require('./lib/srb_flag').pageGate");
  const staticAt = src.indexOf('express.static(');
  assert.ok(gateAt > 0 && staticAt > gateAt, 'the page gate must sit ahead of express.static');
  assert.match(src, /app\.get\(\s*'\/srb'/);
  assert.match(src, /app\.use\('\/api\/srb', require\('\.\/routes\/srb'\)\)/);
});

test('the page source is Bonded-ready and carries no amount', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'srb.html'), 'utf8');
  const js = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'srb.js'), 'utf8');
  assert.equal(html.includes('Bonded ✓'), false);
  assert.equal(html.includes('✓'), false);
  assert.equal(html.includes(String(BOND)), false);
  assert.equal(js.includes('Bonded ✓'), false);
  assert.equal(js.includes(String(BOND)), false);
  assert.doesNotMatch(html, /\bSOL\b/);
  assert.doesNotMatch(js, /\bSOL\b/);
  assert.ok(html.indexOf('/js/api-retry.js?v=3') < html.indexOf('/js/srb.js?v=1'));
  assert.match(html, /property="og:site_name" content="Humanoid Traders"/);
  assert.match(html, /rel="canonical" href="https:\/\/www\.humanoid-traders\.com\/srb"/);
  assert.equal(PREVIEW, 'Admin preview. Not public.');
});

test('a public render says Bonded and an admin render is where the units go', () => {
  const root = fakeRoot();
  const sneaked = {
    preview: false,
    banner: null,
    public: {
      ...REPORT.public,
      bond_base_units: BOND,
      providers: [{ ...REPORT.public.providers[0], bond_base_units: BOND, status: 'Bonded' }],
    },
  };
  renderSrb(root, sneaked);
  assert.equal(root.nodes['#srb-banner'].hidden, true);
  assert.equal(root.nodes['#srb-provider'].textContent, 'provider-a: Bonded');
  assert.equal(root.nodes['#srb-amounts'].hidden, true);
  assert.equal(root.nodes['#srb-amounts'].textContent, '');
  const painted = JSON.stringify(root.nodes);
  assert.equal(painted.includes(String(BOND)), false);
  assert.equal(painted.includes('Recorded base units'), false);
  assert.equal(painted.includes('Bond base units'), false);
  assert.equal(painted.includes('Bonded ✓'), false);
  assert.equal(painted.includes('✓'), false);

  const adminRoot = fakeRoot();
  renderSrb(adminRoot, { preview: true, banner: PREVIEW, public: REPORT.public, admin: REPORT.admin });
  assert.equal(adminRoot.nodes['#srb-banner'].hidden, false);
  assert.equal(adminRoot.nodes['#srb-banner'].textContent, 'Admin preview. Not public.');
  assert.equal(adminRoot.nodes['#srb-provider'].textContent, 'provider-a: Bonded');
  assert.equal(adminRoot.nodes['#srb-amounts'].hidden, false);
  assert.equal(
    adminRoot.nodes['#srb-amounts'].textContent,
    `Recorded base units: 2 · Bond base units: ${BOND}`,
  );

  const empty = fakeRoot();
  renderSrb(empty, null);
  assert.equal(empty.nodes['#srb-err'].hidden, false);
  assert.equal(empty.nodes['#srb-err'].textContent, 'The shadow record could not be read.');
  assert.equal(empty.nodes['#srb-provider'].textContent, '');
  assert.equal(empty.nodes['#srb-amounts'].hidden, true);
});

test('off is 404, an admin preview works, and on is public Bonded', async () => {
  delete process.env.SRB_ENABLED;
  scripted = { status: 0, stdout: `${JSON.stringify(REPORT)}\n`, stderr: '' };
  const server = http.createServer(buildApp());
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    for (const urlPath of ['/srb', '/srb.html', '/js/srb.js', '/api/srb/signals']) {
      const res = await call(server, urlPath);
      assert.equal(res.status, 404, urlPath);
      assert.equal(res.text, 'Not found');
    }
    const freePage = await call(server, '/srb', FREE);
    const freeApi = await call(server, '/api/srb/signals', FREE);
    assert.equal(freePage.status, 404);
    assert.equal(freeApi.status, 404);
    assert.equal(freeApi.text.includes(String(BOND)), false);

    const previewPage = await call(server, '/srb', ADMIN);
    assert.equal(previewPage.status, 200);
    assert.match(previewPage.text, /Sandwich restitution/);
    const previewHtml = await call(server, '/srb.html', ADMIN);
    assert.equal(previewHtml.status, 200);
    assert.match(previewHtml.text, /Sandwich restitution/);
    const previewScript = await call(server, '/js/srb.js', ADMIN);
    assert.equal(previewScript.status, 200);
    assert.equal(previewScript.text, 'next');
    assert.equal(previewPage.text.includes(String(BOND)), false);
    const preview = JSON.parse((await call(server, '/api/srb/signals', ADMIN)).text);
    assert.equal(preview.preview, true);
    assert.equal(preview.banner, 'Admin preview. Not public.');
    assert.equal(preview.public.providers[0].status, 'Bonded');
    assert.equal(JSON.stringify(preview.public).includes(String(BOND)), false);
    assert.equal(JSON.stringify(preview.public).includes('Bonded ✓'), false);
    assert.equal(JSON.stringify(preview.public).includes('$'), false);
    assert.equal(JSON.stringify(preview.public).includes('✓'), false);
    assert.doesNotMatch(JSON.stringify(preview.public), /\bSOL\b/);
    assert.equal(preview.admin.amounts.bond_base_units, BOND);
    assert.equal(preview.admin.amounts.signals[0].would_record_base_units, 2);

    process.env.SRB_ENABLED = '1';
    const openRes = await call(server, '/api/srb/signals');
    assert.equal(openRes.status, 200);
    const open = JSON.parse(openRes.text);
    assert.equal(open.preview, false);
    assert.equal(open.banner, null);
    assert.equal(Object.hasOwn(open, 'admin'), false);
    assert.equal(open.public.providers[0].status, 'Bonded');
    assert.equal(openRes.text.includes(String(BOND)), false);
    assert.equal(openRes.text.includes('Bonded ✓'), false);
    assert.equal(openRes.text.includes('$'), false);
    assert.equal(openRes.text.includes('✓'), false);
    assert.doesNotMatch(openRes.text, /\bSOL\b/);
    const openPage = await call(server, '/srb');
    assert.equal(openPage.status, 200);
    const script = await call(server, '/js/srb.js');
    assert.equal(script.status, 200);
    assert.equal(script.text, 'next');

    scripted = { status: 1, stdout: '', stderr: 'secret key material at /home/key' };
    const bad = await call(server, '/api/srb/signals');
    assert.equal(bad.status, 503);
    assert.deepEqual(JSON.parse(bad.text), { error: 'SrbUnreadable' });
    assert.equal(bad.text.includes('secret'), false);

    const asked = spawnCalls.filter((c) => c.cmd === 'python3' && c.args && c.args[1] === 'bot.srb.report');
    assert.ok(asked.length >= 1, 'the route must read the shadow report from python');
  } finally {
    server.close();
    delete process.env.SRB_ENABLED;
  }
});
