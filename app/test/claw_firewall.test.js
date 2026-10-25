'use strict';
/**
 * Claw Firewall. Flag off is 404 for everyone who is not an operator.
 * An operator gets the preview. Flag on is public. The server does not sign.
 * Fuji gets no 7702 clear. Revoke stays in the payload, including the
 * sentence that it stays open while frozen.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.DRAIN_FIREWALL_ENABLED;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');
const { ethers } = require('ethers');

const { pool } = require('../db');
const route = require('../routes/claw_firewall');
const flag = require('../lib/claw_firewall_flag');
const tx = require('../lib/claw_firewall_tx');

const ADMIN_ID = 94131;
const USER_ID = 94132;
pool.users.push({ id: ADMIN_ID, email: 'clawfw-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: USER_ID, email: 'clawfw-user@example.com', plan: 'free', token_epoch: 0 });

function tokenFor(id, email) {
  return jwt.sign({ user_id: id, email, epoch: 0 }, process.env.JWT_SECRET);
}
const adminToken = tokenFor(ADMIN_ID, 'clawfw-admin@example.com');
const userToken = tokenFor(USER_ID, 'clawfw-user@example.com');

const TOKEN = '0x' + 'ab'.repeat(20);
const SPENDER = '0x' + 'cd'.repeat(20);
const GUARDIAN = '0x' + '11'.repeat(20);
const VAULT = '0x' + '22'.repeat(20);
const PERMIT2 = '0x000000000022D473030F116dDEE9F6B43aC78BA3';

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

async function hit(server, url, { bearer, method, body, raw } = {}) {
  const headers = {};
  if (bearer) headers.authorization = 'Bearer ' + bearer;
  let payload;
  if (raw != null) {
    payload = raw;
    headers['content-type'] = 'application/json';
  } else if (body != null) {
    payload = JSON.stringify(body);
    headers['content-type'] = 'application/json';
  }
  const res = await fetch(`http://127.0.0.1:${server.address().port}${url}`, {
    method: method || 'GET', headers, body: payload,
  });
  const text = await res.text();
  let parsed = text;
  try { parsed = JSON.parse(text); } catch (err) { parsed = text; }
  return { status: res.status, body: parsed, text, cache: res.headers.get('cache-control') };
}

function app() {
  const server = express();
  server.use(flag.pageGate);
  server.get('/claw-firewall', flag.sendPage);
  server.use('/api/claw-firewall', route);
  return server;
}

function scanBody() {
  return {
    available: true,
    read_failed: false,
    items: [
      {
        rank: 1, chain_id: 84532, chain: 'Base Sepolia', kind: 'erc20',
        token: TOKEN, spender: SPENDER, why: 'open allowance',
      },
      {
        rank: 1, chain_id: 84532, chain: 'Base Sepolia', kind: 'delegation_7702',
        token: '', spender: '0x' + 'ee'.repeat(20), why: 'delegated',
      },
      {
        rank: 1, chain_id: 43113, chain: 'Avalanche Fuji', kind: 'delegation_7702',
        token: '', spender: '0x' + 'ff'.repeat(20), why: 'delegated',
      },
    ],
    unread_chains: ['Arbitrum Sepolia'],
    exploit_list_read: false,
    message: 'These are the approvals we could read.',
    partial_line: 'An unread chain is not a clean result.',
  };
}

test('flag is off unless the value is 1 or true', () => {
  assert.equal(flag.flagOn({}), false);
  assert.equal(flag.flagOn({ DRAIN_FIREWALL_ENABLED: 'on' }), false);
  assert.equal(flag.flagOn({ DRAIN_FIREWALL_ENABLED: 'yes' }), false);
  assert.equal(flag.flagOn({ DRAIN_FIREWALL_ENABLED: '0' }), false);
  assert.equal(flag.flagOn({ DRAIN_FIREWALL_ENABLED: 'false' }), false);
  assert.equal(flag.flagOn({ DRAIN_FIREWALL_ENABLED: ' 1 ' }), true);
  assert.equal(flag.flagOn({ DRAIN_FIREWALL_ENABLED: 'TRUE' }), true);
  assert.equal(flag.gate(false, false), 'deny');
  assert.equal(flag.gate(false, true), 'preview');
  assert.equal(flag.gate(true, false), 'open');
});

test('the page states the limits and does not sign', () => {
  const html = fs.readFileSync(flag.pageFile(), 'utf8');
  assert.match(html, /rel="canonical" href="https:\/\/www\.humanoid-traders\.com\/claw-firewall"/);
  assert.match(html, /already open is not stopped/);
  assert.match(html, /Revoke stays available, including while the account is frozen/);
  assert.match(html, /DAI, OpenZeppelin ERC20Permit and USDS/);
  assert.match(html, /does not gate those permits/);
  assert.match(html, /plain v, r, s signature fails closed/);
  assert.match(html, /7702 root key bypasses/);
  assert.match(html, /Fuji is an ERC-7579 smart account/);
  assert.match(html, /no EIP-7702/);
  assert.match(html, /Base Sepolia/);
  assert.match(html, /unread chain is not a clean result/i);
  assert.match(html, /never ask for a seed phrase or a private key/i);
  assert.doesNotMatch(html, /Drain Watch/);
  assert.doesNotMatch(html, /\$\d/);
  assert.doesNotMatch(html, /\bscam\b/i);
  assert.doesNotMatch(html, /this is safe|marked safe|looks safe/i);
  assert.doesNotMatch(html, /eth_sendTransaction|signTransaction|privateKey/);
  const scripts = [...html.replace(/<!--[\s\S]*?-->/g, '').matchAll(/<script\b([^>]*)>/gi)].map((m) => m[1]);
  assert.match(scripts[0], /src="\/js\/api-retry\.js\?v=\d+"/);
  assert.doesNotMatch(scripts[0], /\b(async|defer|type=)/);
  const unlinked = JSON.parse(fs.readFileSync(path.join(__dirname, 'unlinked_routes.json'), 'utf8'));
  assert.ok(unlinked.routes['/claw-firewall'].length >= 40);
  const landing = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.equal(landing.includes('href="/claw-firewall"'), false);
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'claw_firewall.js'), 'utf8');
  assert.match(src, /optionalAuth/);
  assert.match(src, /flag_on_for/);
  assert.doesNotMatch(src, /signTransaction|sendRawTransaction|new Wallet\(|eth_sendRawTransaction/);
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /\/api\/claw-firewall/);
  assert.match(server, /claw_firewall_flag/);
  const gateAt = server.indexOf("require('./lib/claw_firewall_flag').pageGate");
  const staticAt = server.indexOf('app.use(express.static(');
  assert.ok(gateAt > 0 && staticAt > gateAt, 'page gate sits ahead of static');
  const sitemap = fs.readFileSync(path.join(__dirname, '..', 'lib', 'sitemap.js'), 'utf8');
  assert.doesNotMatch(sitemap, /claw-firewall/);
});

test('executeBatch matches ethers, and a 7702 clear is not offered on Fuji', () => {
  const calls = [
    { target: TOKEN, value: 0, data: '0xa9059cbb' + '00'.repeat(64) },
    { target: PERMIT2, value: 0, data: '0x' },
    { target: '0x' + '33'.repeat(20), value: 7, data: '0x' + 'ab'.repeat(40) },
  ];
  const iface = new ethers.Interface([
    'function executeBatch((address target, uint256 value, bytes data)[] calls)',
  ]);
  const theirs = iface.encodeFunctionData('executeBatch', [
    calls.map((c) => [c.target, c.value, c.data]),
  ]);
  assert.equal(tx.encodeExecuteBatch(calls), theirs);
  const initIface = new ethers.Interface(['function init(address guardian, address vault, bytes sig)']);
  assert.equal(
    tx.encodeInit(GUARDIAN, VAULT),
    initIface.encodeFunctionData('init', [GUARDIAN, VAULT, '0x']),
  );
  const pairs = [];
  for (let i = 0; i < 21; i++) {
    pairs.push({
      chain_id: 43113,
      kind: 'permit2',
      token: '0x' + (i + 1).toString(16).padStart(40, '0'),
      spender: SPENDER,
    });
  }
  pairs.push({
    chain_id: 43113, kind: 'delegation_7702', token: '', spender: '0x' + 'ff'.repeat(20),
  });
  pairs.push({
    chain_id: 84532, kind: 'delegation_7702', token: '', spender: '0x' + 'ee'.repeat(20),
  });
  const prepared = tx.prepareRevokes(pairs, []);
  assert.equal(prepared.permit2_calls.length, 2);
  assert.equal(prepared.permit2_calls[0].pairs, 20);
  assert.equal(prepared.permit2_calls[1].pairs, 1);
  assert.equal(prepared.permit2_calls[0].split, true);
  const fuji = prepared.individual.find((item) => item.chain_id === 43113 && item.kind === 'delegation_7702');
  assert.equal(fuji.tx, null);
  assert.match(fuji.note, /does not prepare a 7702 clear/);
  const base = prepared.authorizations.find((item) => item.chain_id === 84532);
  assert.ok(base);
  assert.equal(base.kind, 'authorization');
  assert.equal(prepared.delegation_test_chain, 84532);
  const kept = tx.prepareRevokes([
    { chain_id: 84532, kind: 'erc20', token: TOKEN, spender: SPENDER },
  ], ['84532:' + TOKEN + ':' + SPENDER]);
  assert.deepEqual(kept.kept, ['84532:' + TOKEN.toLowerCase() + ':' + SPENDER.toLowerCase()]);
  assert.equal(kept.execute_batch, null);
  assert.match(kept.kept_line, /Left in place/);
  const hash = tx.pairsHash(['b', 'a']);
  assert.equal(hash, ethers.keccak256(ethers.toUtf8Bytes('a\nb')));
});

test('flag off is 404 for a non-operator and a preview for an operator', async () => {
  route.setRunner(async () => scanBody());
  delete process.env.DRAIN_FIREWALL_ENABLED;
  const server = await listen(app());
  try {
    const anon = await hit(server, '/api/claw-firewall/scan', {
      method: 'POST', body: { address: '0x' + '11'.repeat(20) },
    });
    assert.equal(anon.status, 404);
    const user = await hit(server, '/api/claw-firewall/scan', {
      method: 'POST', bearer: userToken, body: { address: '0x' + '11'.repeat(20) },
    });
    assert.equal(user.status, 404);
    const anonPage = await hit(server, '/claw-firewall');
    assert.equal(anonPage.status, 404);
    const htmlPage = await hit(server, '/claw-firewall.html');
    assert.equal(htmlPage.status, 404);
    const preview = await hit(server, '/api/claw-firewall/scan', {
      method: 'POST', bearer: adminToken, body: { address: '0x' + '11'.repeat(20) },
    });
    assert.equal(preview.status, 200);
    assert.equal(preview.body.banner, 'Admin preview. Not public.');
    assert.match(preview.body.limits.pre_existing, /already open/);
    assert.match(preview.body.limits.revoke_open, /frozen/);
    assert.match(preview.body.limits.ecrecover_first, /does not gate/);
    assert.match(preview.body.limits.plain_signature, /fails closed/);
    assert.match(preview.body.limits.root_key, /root key/);
    assert.match(preview.body.limits.fuji, /no EIP-7702/);
    assert.equal(preview.cache, 'no-store');
    const page = await hit(server, '/claw-firewall', { bearer: adminToken });
    assert.equal(page.status, 200);
    assert.match(page.text, /Admin preview\. Not public\./);
    assert.match(page.text, /does not gate those permits/);
    assert.doesNotMatch(page.text, /<!--CF_BANNER-->/);
  } finally {
    route.setRunner(null);
    await close(server);
  }
});

test('flag on is public, an unread list is not clean, and Fuji has no 7702 clear', async () => {
  process.env.DRAIN_FIREWALL_ENABLED = '1';
  route.setRunner(async () => scanBody());
  const server = await listen(app());
  try {
    const anon = await hit(server, '/api/claw-firewall/scan', {
      method: 'POST', body: { address: '0x' + '11'.repeat(20) },
    });
    assert.equal(anon.status, 200);
    assert.equal(anon.body.banner, undefined);
    assert.equal(anon.body.read_failed, false);
    assert.deepEqual(anon.body.unread_chains, ['Arbitrum Sepolia']);
    assert.match(anon.body.partial_line, /not a clean result/);
    const userPage = await hit(server, '/claw-firewall', { bearer: userToken });
    assert.equal(userPage.status, 200);
    assert.doesNotMatch(userPage.text, /Admin preview/);
    const batch = await hit(server, '/api/claw-firewall/batch', {
      method: 'POST',
      body: {
        address: '0x' + '11'.repeat(20),
        keep: ['84532:' + TOKEN.toLowerCase() + ':' + SPENDER.toLowerCase()],
        guardian: GUARDIAN,
        vault: VAULT,
      },
    });
    assert.equal(batch.status, 200);
    assert.equal(batch.body.empty_is_not_everything_revoked, true);
    assert.deepEqual(batch.body.kept, ['84532:' + TOKEN.toLowerCase() + ':' + SPENDER.toLowerCase()]);
    assert.match(batch.body.kept_line, /not a judgement/);
    const fuji = batch.body.items.find((item) => item.chain_id === 43113);
    assert.equal(fuji.tx, null);
    assert.equal(batch.body.authorizations.length, 1);
    assert.equal(batch.body.authorizations[0].chain_id, 84532);
    assert.equal(batch.body.delegation_test_chain, 84532);
    assert.match(batch.body.init.data, /^0x/);
    assert.match(batch.body.init.note, /does not sign/);
    assert.equal(JSON.stringify(batch.body).includes('$'), false);
    assert.doesNotMatch(JSON.stringify(batch.body), /Drain Watch/);
  } finally {
    delete process.env.DRAIN_FIREWALL_ENABLED;
    route.setRunner(null);
    await close(server);
  }
});

test('a failed read is not an empty clean list, and a huge body is refused', async () => {
  process.env.DRAIN_FIREWALL_ENABLED = '1';
  route.setRunner(async () => ({
    available: true,
    read_failed: true,
    items: [],
    unread_chains: [],
    message: 'The approval lists could not be read just now, so there is no list. This is not a clean result.',
  }));
  const server = await listen(app());
  try {
    const unread = await hit(server, '/api/claw-firewall/scan', {
      method: 'POST', body: { address: '0x' + '11'.repeat(20) },
    });
    assert.equal(unread.status, 200);
    assert.equal(unread.body.read_failed, true);
    assert.match(unread.body.message, /not a clean result/);
    assert.deepEqual(unread.body.items, []);
    const refused = await hit(server, '/api/claw-firewall/batch', {
      method: 'POST', body: { address: '0x' + '11'.repeat(20) },
    });
    assert.equal(refused.status, 200);
    assert.equal(refused.body.read_failed, true);
    route.setRunner(async () => ({
      available: true, refused: 'secret', message: 'Nothing was stored.',
    }));
    const secret = await hit(server, '/api/claw-firewall/batch', {
      method: 'POST', body: { address: 'abandon abandon abandon' },
    });
    assert.equal(secret.status, 400);
    assert.equal(secret.body.message, 'Nothing was stored.');
    assert.doesNotMatch(secret.text, /abandon/);
    const huge = 'x'.repeat(route.bodyLimitBytes + 50);
    const tooBig = await hit(server, '/api/claw-firewall/scan', { method: 'POST', raw: huge });
    assert.equal(tooBig.status, 413);
  } finally {
    delete process.env.DRAIN_FIREWALL_ENABLED;
    route.setRunner(null);
    await close(server);
  }
});
