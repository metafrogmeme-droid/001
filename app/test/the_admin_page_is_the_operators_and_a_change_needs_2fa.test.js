'use strict';
/**
 * The Admin page (routes/admin.js) is the operator's, and a change from it
 * needs a fresh 2FA code.
 *
 * The page changes the bot's runtime settings: leverage, the live drawdown
 * cap, auto-confirm, paper auto-accept. A stolen web session is the threat
 * the step-up exists for, so a change is refused outright to an operator
 * account with no 2FA enrolled, and asks for a code from one that has it.
 * Live trading and the venue are not settable here at all. Signed in is not
 * the operator: the plan is re-read from the database on every request.
 *
 * Driven through the real router, auth middleware, step-up and identity
 * check on the in-memory database, against a stand-in bot gateway that
 * records what it was asked, so each refusal is also shown to have asked the
 * bot nothing.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');

const seen = [];
let gatewayAnswers = 'ok';
let mockGateway;
let appServer;
let base;

function startMockGateway() {
  return new Promise((resolve) => {
    mockGateway = http.createServer((req, res) => {
      let body = '';
      req.on('data', (d) => { body += d; });
      req.on('end', () => {
        const parsed = body ? JSON.parse(body) : null;
        seen.push({ url: req.url, body: parsed });
        res.setHeader('Content-Type', 'application/json');
        if (gatewayAnswers === 'error') {
          res.statusCode = 500;
          return res.end(JSON.stringify({ error: 'read_failed' }));
        }
        if (req.url === '/gateway/admin/settings') {
          return res.end(JSON.stringify({ settings: [{ key: 'leverage_override', value: null }] }));
        }
        if (req.url === '/gateway/admin/settings/set') {
          return res.end(JSON.stringify({ ok: true, key: parsed.key, after: parsed.value }));
        }
        res.statusCode = 404;
        return res.end('{}');
      });
    });
    mockGateway.listen(0, '127.0.0.1', () => resolve(mockGateway.address().port));
  });
}

function request(method, path, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let data = '';
      res.on('data', (d) => { data += d; });
      res.on('end', () => resolve({ status: res.statusCode, data: data ? JSON.parse(data) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

let totp;
let lockout;
let secret;
const tokens = {};

async function seedUser(pool, jwt, email, { plan, tg, twoFactor }) {
  await pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)', [email, 'x', email]);
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', [email]);
  const u = rows[0];
  if (tg) await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', [tg, u.id]);
  const [again] = await pool.execute('SELECT * FROM users WHERE id = ?', [u.id]);
  again[0].plan = plan;
  again[0].telegram_linked = Boolean(tg);
  again[0].totp_enabled = twoFactor ? 1 : 0;
  again[0].totp_secret = twoFactor ? secret : null;
  return jwt.sign({ user_id: u.id, email }, process.env.JWT_SECRET);
}

function code() {
  return totp.hotp(secret, Math.floor(Date.now() / 1000 / 30));
}

test.before(async () => {
  const port = await startMockGateway();
  process.env.BOT_GATEWAY_URL = `http://127.0.0.1:${port}`;
  const jwt = require('jsonwebtoken');
  const { pool } = require('../db');
  totp = require('../lib/totp');
  lockout = require('../lib/second_factor_lockout');
  secret = totp.generateSecret();
  tokens.operator = await seedUser(pool, jwt, 'op@test.io', { plan: 'admin', tg: '777', twoFactor: true });
  tokens.bare = await seedUser(pool, jwt, 'bare@test.io', { plan: 'admin', tg: '778', twoFactor: false });
  tokens.member = await seedUser(pool, jwt, 'member@test.io', { plan: 'pro', tg: '779', twoFactor: true });
  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/admin', require('../routes/admin'));
  await new Promise((resolve) => { appServer = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${appServer.address().port}`;
});

test.beforeEach(async () => {
  seen.length = 0;
  gatewayAnswers = 'ok';
  await lockout._reset();
});

test.after(() => {
  if (appServer) appServer.close();
  if (mockGateway) mockGateway.close();
});

test('signed out is refused', async () => {
  assert.equal((await request('GET', '/api/admin/settings')).status, 401);
  assert.equal(seen.length, 0);
});

test('signed in is not the operator: a member reads and changes nothing', async () => {
  const r = await request('GET', '/api/admin/settings', { token: tokens.member });
  assert.equal(r.status, 403);
  assert.equal(r.data.error, 'admin_required');
  const w = await request('POST', '/api/admin/settings', {
    token: tokens.member, body: { key: 'leverage_override', value: 20, totp_code: code() } });
  assert.equal(w.status, 403);
  assert.equal(seen.length, 0, 'a member reached the bot');
});

test('the operator reads the bot and the site, and the bot is asked as their Telegram id', async () => {
  const r = await request('GET', '/api/admin/settings', { token: tokens.operator });
  assert.equal(r.status, 200);
  assert.deepEqual(r.data.bot, { settings: [{ key: 'leverage_override', value: null }] });
  assert.equal(r.data.bot_error, null);
  assert.ok(Array.isArray(r.data.web.findings));
  assert.equal(r.data.two_factor, true);
  assert.deepEqual(seen.map((s) => [s.url, s.body.telegram_id]), [['/gateway/admin/settings', '777']]);
});

test('a dead bot is named, and the site half still stands', async () => {
  gatewayAnswers = 'error';
  const r = await request('GET', '/api/admin/settings', { token: tokens.operator });
  assert.equal(r.status, 200);
  assert.equal(r.data.bot, null);
  assert.equal(r.data.bot_error, 'read_failed');
  assert.ok(r.data.web && Array.isArray(r.data.web.findings), 'the site half was blanked');
});

test('an operator with no 2FA may read but not change', async () => {
  const r = await request('GET', '/api/admin/settings', { token: tokens.bare });
  assert.equal(r.status, 200);
  assert.equal(r.data.two_factor, false);
  seen.length = 0;
  const w = await request('POST', '/api/admin/settings', {
    token: tokens.bare, body: { key: 'leverage_override', value: 20 } });
  assert.equal(w.status, 403);
  assert.equal(w.data.error, 'two_factor_setup_required');
  assert.equal(seen.length, 0, 'a change without 2FA reached the bot');
});

test('a change asks for the code, refuses a wrong one, and forwards on the right one', async () => {
  const ask = await request('POST', '/api/admin/settings', {
    token: tokens.operator, body: { key: 'leverage_override', value: 15 } });
  assert.equal(ask.status, 401);
  assert.equal(ask.data.error, 'two_factor_required');
  const wrong = await request('POST', '/api/admin/settings', {
    token: tokens.operator, body: { key: 'leverage_override', value: 15, totp_code: '000000' === code() ? '111111' : '000000' } });
  assert.equal(wrong.status, 401);
  assert.equal(seen.length, 0, 'the bot was asked before the code was right');
  const ok = await request('POST', '/api/admin/settings', {
    token: tokens.operator, body: { key: 'leverage_override', value: 15, totp_code: code() } });
  assert.equal(ok.status, 200);
  assert.deepEqual(seen.map((s) => [s.url, s.body]), [[
    '/gateway/admin/settings/set', { telegram_id: '777', key: 'leverage_override', value: 15 }]]);
  seen.length = 0;
  const reset = await request('POST', '/api/admin/settings', {
    token: tokens.operator, body: { key: 'leverage_override', reset: true, totp_code: code() } });
  assert.equal(reset.status, 200);
  assert.deepEqual(seen[0].body, { telegram_id: '777', key: 'leverage_override', reset: true });
});

test('live trading and the venue are refused here, with a right code too', async () => {
  for (const key of ['live_mode', 'venue']) {
    const r = await request('POST', '/api/admin/settings', {
      token: tokens.operator, body: { key, value: true, totp_code: code() } });
    assert.equal(r.status, 403, key);
    assert.equal(r.data.error, 'not_web_editable');
  }
  assert.equal(seen.length, 0);
});

test('a value that is not a scalar, or no value, is refused before anything', async () => {
  for (const body of [{ key: 'leverage_override' }, { key: 'leverage_override', value: { a: 1 } },
    { key: 'Bad Key', value: 1 }]) {
    const r = await request('POST', '/api/admin/settings', { token: tokens.operator, body });
    assert.equal(r.status, 400, JSON.stringify(body));
  }
  assert.equal(seen.length, 0);
});

test('a bot failure on a change is not reported as nothing changed', async () => {
  gatewayAnswers = 'error';
  const r = await request('POST', '/api/admin/settings', {
    token: tokens.operator, body: { key: 'leverage_override', value: 9, totp_code: code() } });
  assert.equal(r.status, 502);
  assert.match(r.data.detail, /Reload the page to read the value in force/);
});

test('the site findings never carry the gateway host', () => {
  const findings = require('../routes/admin')._webFindings({
    BOT_GATEWAY_URL: 'http://203.0.113.9:8080', WEB_GATEWAY_SECRET: 'x'.repeat(40) });
  const gw = findings.filter((f) => f.key === 'BOT_GATEWAY_URL');
  assert.equal(gw.length, 1, 'the plain-HTTP finding is the case this guards');
  assert.ok(findings.every((f) => !f.msg.includes('203.0.113.9')), JSON.stringify(findings));
  assert.match(gw[0].msg, /the gateway host/);
});
