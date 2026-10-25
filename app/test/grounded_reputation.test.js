'use strict';
/**
 * Grounded Reputation route. Admin-only. Flag off is 404 for every caller,
 * including an operator. A non-operator is 404 while the flag is on.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.GROUNDED_REPUTATION_ENABLED;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const route = require('../routes/grounded_reputation');
const flag = require('../lib/grounded_reputation_flag');
const { renderEvidenceTab } = require('../lib/grounded_reputation_tab');

const ADMIN_ID = 95021;
const USER_ID = 95022;
pool.users.push({ id: ADMIN_ID, email: 'grounded-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: USER_ID, email: 'grounded-user@example.com', plan: 'free', token_epoch: 0 });

function tokenFor(id, email) {
  return jwt.sign({ user_id: id, email, epoch: 0 }, process.env.JWT_SECRET);
}
const adminToken = tokenFor(ADMIN_ID, 'grounded-admin@example.com');
const userToken = tokenFor(USER_ID, 'grounded-user@example.com');

const calls = [];

function scripted(body, status) {
  return function (args) {
    calls.push(args);
    return { status: status == null ? 0 : status, stdout: JSON.stringify(body) + '\n' };
  };
}

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

async function hit(server, url, { bearer, method, body } = {}) {
  const headers = {};
  if (bearer) headers.authorization = 'Bearer ' + bearer;
  let payload;
  if (body != null) {
    payload = JSON.stringify(body);
    headers['content-type'] = 'application/json';
  }
  const res = await fetch(`http://127.0.0.1:${server.address().port}${url}`, {
    method: method || 'GET', headers, body: payload,
  });
  const raw = await res.text();
  let parsed = raw;
  try { parsed = JSON.parse(raw); } catch (err) { parsed = raw; }
  return { status: res.status, body: parsed, raw, cache: res.headers.get('cache-control') };
}

function app() {
  const server = express();
  server.use(express.json());
  server.use('/api/grounded-reputation', route);
  return server;
}

const READING = {
  ok: true,
  readable: true,
  indexed: true,
  counts: {
    backed_direct: 1,
    escrow: 2,
    failed: 3,
    tampered: 0,
    none: 4,
    uri_unresolvable: 0,
    revoked: 0,
  },
  escrow_label: "escrow, can't attribute",
  value_buckets: [{ label: 'under $0.01', count: 1 }],
  escrow_buckets: [{ label: 'under $0.01', count: 2 }],
  buckets_unreadable: false,
  bucket_basis: 'USDC token units at the token\'s decimals. Not a market price.',
  signals: {
    self_loop_suspect: [],
    common_first_funder: [],
    owner_adjacent: [],
    burst: [],
    wash: [],
  },
  limits: 'A payment shows that value moved.',
  footer: 'Unbacked does not mean fake.',
  summary: null,
  ai_written: false,
};

test('flag is off unless the value is 1 or true', () => {
  assert.equal(flag.flagOn({}), false);
  assert.equal(flag.flagOn({ GROUNDED_REPUTATION_ENABLED: 'on' }), false);
  assert.equal(flag.flagOn({ GROUNDED_REPUTATION_ENABLED: 'yes' }), false);
  assert.equal(flag.flagOn({ GROUNDED_REPUTATION_ENABLED: ' 1 ' }), true);
  assert.equal(flag.flagOn({ GROUNDED_REPUTATION_ENABLED: 'TRUE' }), true);
});

test('the route is authenticated and does not sign', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'grounded_reputation.js'), 'utf8');
  assert.match(src, /optionalAuth/);
  assert.match(src, /accessFor/);
  assert.doesNotMatch(src, /signTransaction|sendRawTransaction|eth_sendRawTransaction|privateKey/);
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /\/api\/grounded-reputation/);
});

test('flag off is 404 for an operator and for everyone else', async () => {
  calls.length = 0;
  route.setRunnerForTests(scripted(READING));
  delete process.env.GROUNDED_REPUTATION_ENABLED;
  const server = await listen(app());
  try {
    const anon = await hit(server, '/api/grounded-reputation/evidence?chain_id=84532&agent_id=1');
    assert.equal(anon.status, 404);
    const user = await hit(server, '/api/grounded-reputation/evidence?chain_id=84532&agent_id=1', { bearer: userToken });
    assert.equal(user.status, 404);
    const admin = await hit(server, '/api/grounded-reputation/evidence?chain_id=84532&agent_id=1', { bearer: adminToken });
    assert.equal(admin.status, 404);
    const posted = await hit(server, '/api/grounded-reputation/classify', {
      bearer: adminToken, method: 'POST', body: { agent: { chain_id: 84532, agent_id: 1 } },
    });
    assert.equal(posted.status, 404);
    assert.deepEqual(calls, []);
  } finally {
    route.setRunnerForTests(null);
    await close(server);
  }
});

test('flag on admits an operator and refuses a non-operator', async () => {
  calls.length = 0;
  process.env.GROUNDED_REPUTATION_ENABLED = '1';
  route.setRunnerForTests(scripted(READING));
  const server = await listen(app());
  try {
    const user = await hit(server, '/api/grounded-reputation/evidence?chain_id=84532&agent_id=1', { bearer: userToken });
    assert.equal(user.status, 404);
    const admin = await hit(server, '/api/grounded-reputation/evidence?chain_id=84532&agent_id=1', { bearer: adminToken });
    assert.equal(admin.status, 200);
    assert.equal(admin.cache, 'private, no-store');
    assert.equal(admin.body.counts.backed_direct, 1);
    assert.equal(admin.body.counts.escrow, 2);
    assert.equal(admin.body.escrow_label, "escrow, can't attribute");
    assert.equal(admin.body.counts == null, false);
    const bad = await hit(server, '/api/grounded-reputation/evidence?chain_id=nope&agent_id=1', { bearer: adminToken });
    assert.equal(bad.status, 400);
    assert.equal(bad.body.counts, null);
    assert.deepEqual(calls, [['evidence', '84532', '1']]);
  } finally {
    delete process.env.GROUNDED_REPUTATION_ENABLED;
    route.setRunnerForTests(null);
    await close(server);
  }
});

test('an unreadable classifier is not a zero count', async () => {
  process.env.GROUNDED_REPUTATION_ENABLED = '1';
  route.setRunnerForTests(scripted({}, 1));
  const server = await listen(app());
  try {
    const got = await hit(server, '/api/grounded-reputation/classify', {
      bearer: adminToken, method: 'POST', body: { agent: { chain_id: 84532 } },
    });
    assert.equal(got.status, 503);
    assert.equal(got.body.readable, false);
    assert.equal(got.body.counts, null);
  } finally {
    delete process.env.GROUNDED_REPUTATION_ENABLED;
    route.setRunnerForTests(null);
    await close(server);
  }
});

test('the evidence tab shows counts, the escrow label, and no verdict', () => {
  const html = renderEvidenceTab(READING);
  assert.match(html, /escrow, can't attribute/);
  assert.match(html, /Unbacked does not mean fake/);
  assert.match(html, /Backed/);
  assert.doesNotMatch(html, /AI-written summary/);
  assert.doesNotMatch(html, /self-payment|trustworthy|scam/i);
  const labelled = renderEvidenceTab(Object.assign({}, READING, { summary: 'Two ratings cite a payment.', ai_written: true }));
  assert.match(labelled, /AI-written summary/);
  const missing = renderEvidenceTab({ indexed: false, counts: null, note: 'This agent is not in the local index.' });
  assert.match(missing, /not in the local index/);
  assert.doesNotMatch(missing, />0</);
});
