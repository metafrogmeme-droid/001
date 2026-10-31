'use strict';
/**
 * Claw Watch heuristics route. Admin preview while the flag is off.
 * The runner is a stand-in: this file does not open a chain socket.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.CLAW_WATCH_HEURISTICS_ENABLED;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const route = require('../routes/claw_watch_heuristics');
const flag = require('../lib/claw_watch_heuristics_flag');

const ADMIN_ID = 95021;
const USER_ID = 95022;
pool.users.push({ id: ADMIN_ID, email: 'cwh-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: USER_ID, email: 'cwh-user@example.com', plan: 'free', token_epoch: 0 });

function tokenFor(id, email) {
  return jwt.sign({ user_id: id, email, epoch: 0 }, process.env.JWT_SECRET);
}
const adminToken = tokenFor(ADMIN_ID, 'cwh-admin@example.com');
const userToken = tokenFor(USER_ID, 'cwh-user@example.com');

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

async function hit(server, { bearer, body } = {}) {
  const headers = { 'content-type': 'application/json' };
  if (bearer) headers.authorization = 'Bearer ' + bearer;
  const res = await fetch(`http://127.0.0.1:${server.address().port}/api/claw-watch-heuristics/score`, {
    method: 'POST', headers, body: JSON.stringify(body || {}),
  });
  const raw = await res.text();
  let parsed = raw;
  try { parsed = JSON.parse(raw); } catch (err) { parsed = raw; }
  return { status: res.status, body: parsed };
}

function app() {
  const server = express();
  server.use(express.json());
  server.use('/api/claw-watch-heuristics', route);
  return server;
}

test('flag is off unless the value is 1 or true', () => {
  assert.equal(flag.flagOn({}), false);
  assert.equal(flag.flagOn({ CLAW_WATCH_HEURISTICS_ENABLED: 'on' }), false);
  assert.equal(flag.flagOn({ CLAW_WATCH_HEURISTICS_ENABLED: 'yes' }), false);
  assert.equal(flag.flagOn({ CLAW_WATCH_HEURISTICS_ENABLED: ' 1 ' }), true);
  assert.equal(flag.flagOn({ CLAW_WATCH_HEURISTICS_ENABLED: 'TRUE' }), true);
});

test('the route is authenticated and does not sign', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'claw_watch_heuristics.js'), 'utf8');
  assert.match(src, /function authMiddleware/);
  assert.match(src, /flag_on_for/);
  assert.doesNotMatch(src, /signTransaction|sendRawTransaction|new Wallet\(|eth_sendRawTransaction/);
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /\/api\/claw-watch-heuristics/);
  const preview = fs.readFileSync(path.join(__dirname, '..', 'lib', 'admin_preview.js'), 'utf8');
  assert.match(preview, /CLAW_WATCH_HEURISTICS_ENABLED/);
});

test('anonymous is 401, a non-operator is 404, an admin preview scores', async () => {
  const calls = [];
  route.setRunnerForTests(function (body) {
    calls.push(body.chain_id);
    return {
      chain_allowed: true,
      chain_id: body.chain_id,
      findings: [],
      disclaimer: 'Public on-chain signal only. This is not a verdict.',
      internal: { status: 'off', reason: 'disabled' },
      code_status: 'read',
    };
  });
  delete process.env.CLAW_WATCH_HEURISTICS_ENABLED;
  const server = await listen(app());
  try {
    const anon = await hit(server, { body: { chain_id: 11155111 } });
    assert.equal(anon.status, 401);
    const user = await hit(server, { bearer: userToken, body: { chain_id: 11155111 } });
    assert.equal(user.status, 404);
    const preview = await hit(server, { bearer: adminToken, body: { chain_id: 11155111 } });
    assert.equal(preview.status, 200);
    assert.equal(preview.body.banner, 'Admin preview. Not public.');
    assert.deepEqual(preview.body.findings, []);
    assert.equal(calls.length, 1);
    const text = JSON.stringify(preview.body).toLowerCase();
    assert.equal(text.includes('scam'), false);
    assert.equal(text.includes('safe'), false);
  } finally {
    route.setRunnerForTests(null);
    delete process.env.CLAW_WATCH_HEURISTICS_ENABLED;
    await close(server);
  }
});

test('a refused chain is not rewritten into an empty clear', async () => {
  route.setRunnerForTests(function () {
    return {
      chain_allowed: false,
      chain_id: 1,
      findings: null,
      sentence: 'This chain is not a testnet or local fork this watch reads.',
      disclaimer: 'Public on-chain signal only. This is not a verdict.',
    };
  });
  const server = await listen(app());
  try {
    const preview = await hit(server, { bearer: adminToken, body: { chain_id: 1 } });
    assert.equal(preview.status, 200);
    assert.equal(preview.body.chain_allowed, false);
    assert.equal(preview.body.findings, null);
  } finally {
    route.setRunnerForTests(null);
    await close(server);
  }
});

async function hitSession(server, { bearer, body } = {}) {
  const headers = { 'content-type': 'application/json' };
  if (bearer) headers.authorization = 'Bearer ' + bearer;
  const res = await fetch(`http://127.0.0.1:${server.address().port}/api/claw-watch-heuristics/session`, {
    method: 'POST', headers, body: JSON.stringify(body === undefined ? {} : body),
  });
  const raw = await res.text();
  let parsed = raw;
  try { parsed = JSON.parse(raw); } catch (err) { parsed = raw; }
  return { status: res.status, body: parsed };
}

test('session route is authed, admin-only while the flag is off, and keeps an unreadable score', async () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'claw_watch_heuristics.js'), 'utf8');
  assert.match(src, /router\.post\('\/session', authMiddleware/);
  assert.match(src, /bot\.claw_watch_heuristics\.session/);
  assert.equal(src.includes('api.hyperliquid.xyz'), false);
  delete process.env.CLAW_WATCH_HEURISTICS_ENABLED;
  const calls = [];
  route.setSessionRunnerForTests(function (body) {
    calls.push(body);
    if (body && body.unread) {
      return { alerts: null, scored: false, disclaimer: 'Public account signal only. This is not a verdict.' };
    }
    return {
      enabled: true,
      scored: true,
      alerts: [{ code: 'NEW_API_KEY', level: 'info', ts: 1, detail: { key_id: 'k9' }, text: 'A new API key was created on this account.' }],
      disclaimer: 'Public account signal only. This is not a verdict.',
    };
  });
  const server = await listen(app());
  try {
    const anon = await hitSession(server, { body: { events: [] } });
    assert.equal(anon.status, 401);
    const user = await hitSession(server, { bearer: userToken, body: { events: [] } });
    assert.equal(user.status, 404);
    assert.equal(calls.length, 0);
    const preview = await hitSession(server, { bearer: adminToken, body: { events: [] } });
    assert.equal(preview.status, 200);
    assert.equal(preview.body.banner, 'Admin preview. Not public.');
    assert.equal(preview.body.alerts[0].code, 'NEW_API_KEY');
    const text = JSON.stringify(preview.body).toLowerCase();
    assert.equal(text.includes('scam'), false);
    assert.equal(text.includes('safe'), false);
    const unread = await hitSession(server, { bearer: adminToken, body: { events: [], unread: true } });
    assert.equal(unread.status, 200);
    assert.equal(unread.body.alerts, null);
    assert.equal(unread.body.scored, false);
  } finally {
    route.setSessionRunnerForTests(null);
    delete process.env.CLAW_WATCH_HEURISTICS_ENABLED;
    await close(server);
  }
});

test('with the flag on a signed-in caller can score a session', async () => {
  process.env.CLAW_WATCH_HEURISTICS_ENABLED = '1';
  route.setSessionRunnerForTests(function () {
    return {
      enabled: true,
      scored: true,
      alerts: [],
      disclaimer: 'Public account signal only. This is not a verdict.',
    };
  });
  const server = await listen(app());
  try {
    const user = await hitSession(server, { bearer: userToken, body: { events: [] } });
    assert.equal(user.status, 200);
    assert.equal(user.body.banner, undefined);
    assert.deepEqual(user.body.alerts, []);
  } finally {
    delete process.env.CLAW_WATCH_HEURISTICS_ENABLED;
    route.setSessionRunnerForTests(null);
    await close(server);
  }
});

test('with the flag on a signed-in caller can score', async () => {
  process.env.CLAW_WATCH_HEURISTICS_ENABLED = '1';
  route.setRunnerForTests(function () {
    return {
      chain_allowed: true,
      findings: [{
        chain_id: 11155111, score: 40, signal_strength: 'medium', tx_hash: '0xab',
        signals: [], revokes: [{ signature: 'nope', authorization: { r: '1', s: '2', v: '3', signature: 'x' } }],
        disclaimer: 'Public on-chain signal only. This is not a verdict.',
      }],
      disclaimer: 'Public on-chain signal only. This is not a verdict.',
    };
  });
  const server = await listen(app());
  try {
    const user = await hit(server, { bearer: userToken, body: { chain_id: 11155111 } });
    assert.equal(user.status, 200);
    assert.equal(user.body.banner, undefined);
    assert.equal(user.body.findings[0].revokes[0].signature, null);
    assert.equal(user.body.findings[0].revokes[0].authorization.signature, null);
    assert.equal(user.body.findings[0].revokes[0].authorization.r, undefined);
  } finally {
    delete process.env.CLAW_WATCH_HEURISTICS_ENABLED;
    route.setRunnerForTests(null);
    await close(server);
  }
});
