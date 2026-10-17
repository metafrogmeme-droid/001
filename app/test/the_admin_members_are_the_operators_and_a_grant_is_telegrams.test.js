'use strict';
/**
 * The Admin page's members section (routes/admin.js `/members`) is the
 * operator's, a change needs a fresh 2FA code, and the grants that make an
 * account powerful are Telegram's.
 *
 * Approve, revoke, set a tier and restrict to paper are forwarded to the bot,
 * which writes them through `bot/core/member_admin.py`, the same path the
 * Telegram commands take. Granting live trading, the admin role or the admin
 * tier is refused here by name before the bot is asked: the website reads
 * `plan == 'admin'` as the operator, so a stolen web session that could set
 * it would mint a second operator.
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

const ROSTER = { members: [{ id: '55', name: 'Bo', role: 'paper', tier: 'basic', live: false }], changes: [] };

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
        if (req.url === '/gateway/admin/members') return res.end(JSON.stringify(ROSTER));
        if (req.url === '/gateway/admin/members/act') {
          if (gatewayAnswers === 'refuse') {
            res.statusCode = 400;
            return res.end(JSON.stringify({ ok: false, error: 'cannot_revoke_self', detail: 'Cannot revoke yourself.' }));
          }
          return res.end(JSON.stringify({ ok: true, action: parsed.action, target: parsed.target }));
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
  tokens.operator = await seedUser(pool, jwt, 'mop@test.io', { plan: 'admin', tg: '777', twoFactor: true });
  tokens.bare = await seedUser(pool, jwt, 'mbare@test.io', { plan: 'admin', tg: '778', twoFactor: false });
  tokens.member = await seedUser(pool, jwt, 'mmember@test.io', { plan: 'pro', tg: '779', twoFactor: true });
  tokens.unlinked = await seedUser(pool, jwt, 'munlinked@test.io', { plan: 'admin', tg: null, twoFactor: true });
  // The change limit is 12 a minute per account; the refusal tests each
  // spend most of one, so they run as operators of their own.
  tokens.grants = await seedUser(pool, jwt, 'mgrants@test.io', { plan: 'admin', tg: '780', twoFactor: true });
  tokens.shapes = await seedUser(pool, jwt, 'mshapes@test.io', { plan: 'admin', tg: '781', twoFactor: true });
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
  assert.equal((await request('GET', '/api/admin/members')).status, 401);
  assert.equal((await request('POST', '/api/admin/members', { body: { action: 'revoke', target: '55' } })).status, 401);
  assert.equal(seen.length, 0);
});

test('signed in is not the operator: a member reads and changes nothing', async () => {
  const r = await request('GET', '/api/admin/members', { token: tokens.member });
  assert.equal(r.status, 403);
  const w = await request('POST', '/api/admin/members', {
    token: tokens.member, body: { action: 'revoke', target: '777', totp_code: code() } });
  assert.equal(w.status, 403);
  assert.equal(seen.length, 0, 'a member reached the bot');
});

test('the operator reads the roster, and the bot is asked as their Telegram id', async () => {
  const r = await request('GET', '/api/admin/members', { token: tokens.operator });
  assert.equal(r.status, 200);
  assert.deepEqual(r.data.bot, ROSTER);
  assert.equal(r.data.bot_error, null);
  assert.deepEqual(seen.map((s) => [s.url, s.body]), [['/gateway/admin/members', { telegram_id: '777' }]]);
});

test('a dead bot is named, never an empty roster', async () => {
  gatewayAnswers = 'error';
  const r = await request('GET', '/api/admin/members', { token: tokens.operator });
  assert.equal(r.status, 200);
  assert.equal(r.data.bot, null);
  assert.equal(r.data.bot_error, 'read_failed');
});

test('an operator with no Telegram link is told so, and the bot is not asked', async () => {
  const r = await request('GET', '/api/admin/members', { token: tokens.unlinked });
  assert.equal(r.status, 200);
  assert.equal(r.data.bot_error, 'telegram_required');
  const w = await request('POST', '/api/admin/members', {
    token: tokens.unlinked, body: { action: 'revoke', target: '55', totp_code: code() } });
  assert.equal(w.status, 409);
  assert.equal(seen.length, 0);
});

test('live trading, the admin role and the admin tier are Telegram\'s, with a right code too', async () => {
  for (const body of [
    { action: 'grant_live', target: '55' },
    { action: 'grant_admin', target: '55' },
    { action: 'approve', target: '55', role: 'admin' },
    { action: 'approve', target: '55', role: ' Admin ' },
    { action: 'set_tier', target: '55', tier: 'admin' },
  ]) {
    const r = await request('POST', '/api/admin/members', {
      token: tokens.grants, body: { ...body, totp_code: code() } });
    assert.equal(r.status, 403, JSON.stringify(body));
    assert.equal(r.data.error, 'telegram_only', JSON.stringify(body));
    assert.match(r.data.detail, /Telegram only/);
  }
  assert.equal(seen.length, 0, 'a Telegram-only grant reached the bot');
});

test('an unknown action, a malformed id or a malformed word is refused before anything', async () => {
  for (const body of [
    { action: 'delete', target: '55' },
    { action: 'revoke', target: 'abc' },
    { action: 'revoke', target: '١٢' },
    { action: 'revoke', target: 'web:x' },
    { action: 'revoke', target: '55/../1' },
    { action: 'revoke', target: '1'.repeat(21) },
    { action: 'approve', target: '55', role: 'paper trader' },
    { action: 'set_tier', target: '55', tier: { pro: 1 } },
  ]) {
    const r = await request('POST', '/api/admin/members', { token: tokens.shapes, body });
    assert.equal(r.status, 400, JSON.stringify(body));
  }
  assert.equal(seen.length, 0);
});

test('an operator with no 2FA may read the members but not change one', async () => {
  const r = await request('GET', '/api/admin/members', { token: tokens.bare });
  assert.equal(r.status, 200);
  assert.equal(r.data.two_factor, false);
  seen.length = 0;
  const w = await request('POST', '/api/admin/members', {
    token: tokens.bare, body: { action: 'revoke', target: '55' } });
  assert.equal(w.status, 403);
  assert.equal(w.data.error, 'two_factor_setup_required');
  assert.equal(seen.length, 0, 'a change without 2FA reached the bot');
});

test('a change asks for the code, refuses a wrong one, and forwards exactly what was asked on the right one', async () => {
  const ask = await request('POST', '/api/admin/members', {
    token: tokens.operator, body: { action: 'approve', target: '55', role: 'trader' } });
  assert.equal(ask.status, 401);
  assert.equal(ask.data.error, 'two_factor_required');
  const wrong = await request('POST', '/api/admin/members', {
    token: tokens.operator,
    body: { action: 'approve', target: '55', role: 'trader', totp_code: code() === '000000' ? '111111' : '000000' } });
  assert.equal(wrong.status, 401);
  assert.equal(seen.length, 0, 'the bot was asked before the code was right');
  const ok = await request('POST', '/api/admin/members', {
    token: tokens.operator, body: { action: 'approve', target: '55', role: 'Trader', totp_code: code() } });
  assert.equal(ok.status, 200);
  assert.deepEqual(seen.map((s) => [s.url, s.body]), [[
    '/gateway/admin/members/act', { telegram_id: '777', action: 'approve', target: '55', role: 'trader' }]]);
  seen.length = 0;
  const tier = await request('POST', '/api/admin/members', {
    token: tokens.operator, body: { action: 'set_tier', target: 'web:7', tier: 'pro', totp_code: code() } });
  assert.equal(tier.status, 200);
  assert.deepEqual(seen[0].body, { telegram_id: '777', action: 'set_tier', target: 'web:7', tier: 'pro' });
});

test('the bot\'s refusal reaches the page with its sentence', async () => {
  gatewayAnswers = 'refuse';
  const r = await request('POST', '/api/admin/members', {
    token: tokens.operator, body: { action: 'revoke', target: '777', totp_code: code() } });
  assert.equal(r.status, 400);
  assert.equal(r.data.detail, 'Cannot revoke yourself.');
});

test('a bot failure on a change is not reported as nothing changed', async () => {
  gatewayAnswers = 'error';
  const r = await request('POST', '/api/admin/members', {
    token: tokens.operator, body: { action: 'revoke_live', target: '55', totp_code: code() } });
  assert.equal(r.status, 502);
  assert.match(r.data.detail, /Reload the page to read the member as they stand/);
});
