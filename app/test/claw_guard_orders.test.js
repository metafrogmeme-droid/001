'use strict';

/**
 * Per-order check. Off unless CLAW_GUARD_ENABLED is 1 or true.
 * A caller who is not signed in is 401. A signed-in non-operator is 404
 * while the flag is off. The route does not sign.
 */

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.CLAW_GUARD_ENABLED;

const flag = require('../lib/claw_guard_flag');
const routes = require('../routes/claw_guard_orders');
const { pool } = require('../db');

const ADMIN_ID = 92021;
const USER_ID = 92022;
pool.users.push({ id: ADMIN_ID, email: 'guard-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: USER_ID, email: 'guard-user@example.com', plan: 'free', token_epoch: 0 });

function tokenFor(id, email) {
  return jwt.sign({ user_id: id, email, epoch: 0 }, process.env.JWT_SECRET);
}

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

function hit(server, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body || {});
    const req = http.request({
      hostname: '127.0.0.1',
      port: server.address().port,
      path: '/api/claw-guard/check',
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'content-length': Buffer.byteLength(payload),
        ...(token ? { authorization: 'Bearer ' + token } : {}),
      },
    }, (res) => {
      let raw = '';
      res.on('data', (chunk) => { raw += chunk; });
      res.on('end', () => {
        let parsed = raw;
        try { parsed = JSON.parse(raw); } catch (e) { parsed = raw; }
        resolve({ status: res.statusCode, body: parsed });
      });
    });
    req.on('error', reject);
    req.end(payload);
  });
}

test('the flag accepts only 1 and true', () => {
  assert.equal(flag.flagOn({}), false);
  assert.equal(flag.flagOn({ CLAW_GUARD_ENABLED: 'yes' }), false);
  assert.equal(flag.flagOn({ CLAW_GUARD_ENABLED: 'on' }), false);
  assert.equal(flag.flagOn({ CLAW_GUARD_ENABLED: '1' }), true);
  assert.equal(flag.flagOn({ CLAW_GUARD_ENABLED: 'true' }), true);
});

test('check requires a session, and a non-operator is 404 while the flag is off', async () => {
  routes.setRunner(async () => ({ ok: true, decision: 'ALLOW', text: 'Allowed: within your limits', rules: [] }));
  const app = express();
  app.use(express.json());
  app.use('/api/claw-guard', routes);
  const server = await listen(app);
  try {
    const anon = await hit(server, {});
    assert.equal(anon.status, 401);
    const user = await hit(server, { token: tokenFor(USER_ID, 'guard-user@example.com') });
    assert.equal(user.status, 404);
    const admin = await hit(server, { token: tokenFor(ADMIN_ID, 'guard-admin@example.com') });
    assert.equal(admin.status, 200);
    assert.equal(admin.body.decision, 'ALLOW');
    assert.equal(admin.body.banner, 'Admin preview. Not public.');
    assert.equal(JSON.stringify(admin.body).includes('private'), false);
  } finally {
    routes.setRunner(routes.run);
    await close(server);
  }
});

test('the route file names auth and does not sign', () => {
  const fs = require('node:fs');
  const src = fs.readFileSync(require('node:path').join(__dirname, '..', 'routes', 'claw_guard_orders.js'), 'utf8');
  assert.match(src, /optionalAuth/);
  assert.doesNotMatch(src, /CLAW_GUARD_HL_AGENT_KEY/);
  assert.doesNotMatch(src, /safe|scam/i);
});
