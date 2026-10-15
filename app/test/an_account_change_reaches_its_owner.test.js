'use strict';
/**
 * An account change reaches its owner by email, and a change that did not
 * happen sends nothing.
 *
 * The account revoked sessions on a password change and asked for a code
 * before 2FA came off, but none of it reached the OWNER: someone holding a
 * stolen session could turn 2FA off and the owner heard nothing. Each
 * security change now sends a notice to the address on record. A website-only
 * member is also emailed when the operator changes their tier, on the bot's
 * explicit request; the whole-book tier mirror never mails anyone.
 *
 * Driven through the real auth and sync routes on the in-memory database,
 * with the mailer's send captured. Every test asserts both arms: the change
 * that happened is mailed, and the refused attempt beside it is not.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
process.env.APP_BASE_URL = 'https://app.test';
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;   // the bot half of a deletion is skipped

const test = require('node:test');
const assert = require('node:assert/strict');
const express = require('express');

const mailer = require('../lib/mailer');
const sent = [];
mailer.sendMail = async (msg) => { sent.push(msg); return { skipped: false }; };
mailer.isConfigured = () => true;

const accountMail = require('../lib/account_mail');
const totp = require('../lib/totp');
const { tombstoneEmail } = require('../lib/account_erasure');
const authModule = require('../auth');
const { pool } = require('../db');

let server, base;
test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => server && server.close());

async function call(method, p, { body, token, botSecret } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (botSecret) headers['X-Bot-Secret'] = botSecret;
  const r = await fetch(base + p, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const text = await r.text();
  return { status: r.status, json: text ? JSON.parse(text) : {} };
}

async function member(email) {
  const r = await call('POST', '/api/auth/register', { body: { email, password: 'longenough1' } });
  assert.equal(r.status, 200, JSON.stringify(r.json));
  return { token: r.json.token, id: Number(r.json.user_id || (r.json.user && r.json.user.id)) };
}

// The notices sent to one address since `from`, by subject. Registration's
// welcome is excluded by starting the count after it.
const mailTo = (email, from) => sent.slice(from).filter((m) => m.to === email);
const flush = () => new Promise((r) => setImmediate(r));
const nowCode = (secret) => totp.hotp(secret, Math.floor(Date.now() / 30000));

test('a password change is mailed; a refused change is not', async () => {
  const email = 'pw@example.com';
  const { token } = await member(email);
  let from = sent.length;
  const bad = await call('POST', '/api/auth/change-password',
    { token, body: { current_password: 'wrong-password', new_password: 'anotherlong1' } });
  assert.equal(bad.status, 401);
  await flush();
  assert.deepEqual(mailTo(email, from), [], 'a refused change was reported as made');

  from = sent.length;
  const ok = await call('POST', '/api/auth/change-password',
    { token, body: { current_password: 'longenough1', new_password: 'anotherlong1' } });
  assert.equal(ok.status, 200);
  await flush();
  const got = mailTo(email, from);
  assert.equal(got.length, 1, JSON.stringify(got));
  assert.equal(got[0].subject, 'Your RUNECLAW password was changed');
  assert.match(got[0].text, /Every other signed-in session was signed out/);
  assert.doesNotMatch(got[0].text, /anotherlong1/, 'the new password went into an email');
});

test('a first password on a provider account is "added", not "changed"', async () => {
  const email = 'google-user@example.com';
  const user = await authModule.findOrCreateOAuthUser(
    { provider: 'google', providerId: 'g-1001', email, avatarUrl: null });
  const token = authModule.signToken(user);
  const from = sent.length;
  const r = await call('POST', '/api/auth/change-password', { token, body: { new_password: 'firstpassword1' } });
  assert.equal(r.status, 200, JSON.stringify(r.json));
  await flush();
  const got = mailTo(email, from);
  assert.equal(got.length, 1);
  assert.equal(got[0].subject, 'A password was added to your RUNECLAW account');
});

test('a reset is mailed without the reset token; a bad token mails nothing', async () => {
  const email = 'reset-n@example.com';
  await member(email);
  await call('POST', '/api/auth/forgot-password', { body: { email } });
  const link = sent[sent.length - 1];
  const raw = /reset\?token=([a-f0-9]+)/.exec(link.text)[1];

  let from = sent.length;
  const bad = await call('POST', '/api/auth/reset-password', { body: { token: 'f'.repeat(64), new_password: 'resetpassword1' } });
  assert.equal(bad.status, 400);
  await flush();
  assert.deepEqual(mailTo(email, from), []);

  from = sent.length;
  const ok = await call('POST', '/api/auth/reset-password', { body: { token: raw, new_password: 'resetpassword1' } });
  assert.equal(ok.status, 200);
  await flush();
  const got = mailTo(email, from);
  assert.equal(got.length, 1);
  assert.equal(got[0].subject, 'Your RUNECLAW password was reset');
  assert.ok(!got[0].text.includes(raw) && !got[0].html.includes(raw), 'the reset token went into the notice');
});

test('2FA on and 2FA off are each mailed; a wrong code mails nothing', async () => {
  const email = 'twofa@example.com';
  const { token } = await member(email);
  const setup = await call('POST', '/api/auth/2fa/setup', { token });
  const secret = setup.json.secret;
  assert.ok(secret);

  let from = sent.length;
  await call('POST', '/api/auth/2fa/enable', { token, body: { code: '000000' } });
  await flush();
  assert.deepEqual(mailTo(email, from), [], 'a refused enrolment was reported as made');
  const on = await call('POST', '/api/auth/2fa/enable', { token, body: { code: nowCode(secret) } });
  assert.equal(on.status, 200, JSON.stringify(on.json));
  await flush();
  let got = mailTo(email, from);
  assert.equal(got.length, 1);
  assert.equal(got[0].subject, 'Two-factor authentication is on');
  for (const c of on.json.backup_codes) {
    assert.ok(!got[0].text.includes(c), 'a backup code went into an email');
  }

  from = sent.length;
  const wrong = await call('POST', '/api/auth/2fa/disable', { token, body: { code: '000000' } });
  assert.equal(wrong.status, 401);
  await flush();
  assert.deepEqual(mailTo(email, from), []);
  const off = await call('POST', '/api/auth/2fa/disable', { token, body: { code: nowCode(secret) } });
  assert.equal(off.status, 200, JSON.stringify(off.json));
  await flush();
  got = mailTo(email, from);
  assert.equal(got.length, 1);
  assert.equal(got[0].subject, 'Two-factor authentication was turned off');
});

test('a deletion is mailed to the address read before the erasure', async () => {
  const email = 'leaving@example.com';
  const { token, id } = await member(email);
  let from = sent.length;
  const refused = await call('DELETE', '/api/auth/account', { token, body: { password: 'wrong-password', confirm: 'DELETE' } });
  assert.equal(refused.status, 401);
  await flush();
  assert.deepEqual(mailTo(email, from), []);

  from = sent.length;
  const r = await call('DELETE', '/api/auth/account', { token, body: { password: 'longenough1', confirm: 'DELETE' } });
  assert.equal(r.status, 200, JSON.stringify(r.json));
  await flush();
  const got = sent.slice(from);
  assert.equal(got.length, 1, JSON.stringify(got));
  assert.equal(got[0].to, email, 'the notice went to the tombstone, not the person');
  assert.equal(got[0].subject, 'Your RUNECLAW account was deleted');
  // The row really was erased, so the address could only have come from
  // the read before it.
  const [rows] = await pool.execute('SELECT email FROM users WHERE id = ?', [id]);
  assert.equal(rows[0].email, tombstoneEmail(id));
});

test('no address the app synthesised is ever mailed', async () => {
  // Driven through the real constructors, not restated here.
  for (const provider of ['telegram', 'x', 'wallet']) {
    const u = await authModule.findOrCreateOAuthUser(
      { provider, providerId: `${provider}-77`, email: null, avatarUrl: null });
    assert.equal(accountMail.deliverable(u.email), false, u.email);
    const from = sent.length;
    assert.deepEqual(await accountMail.notify('password_changed', u.email), { sent: false, reason: 'undeliverable' });
    // And the resend door refuses it instead of "sending" into a bounce.
    const r = await call('POST', '/api/auth/send-verification', { token: authModule.signToken(u) });
    assert.equal(r.status, 400, `${provider}: ${JSON.stringify(r.json)}`);
    assert.deepEqual(sent.slice(from), [], `${provider}: mail went to ${u.email}`);
  }
  assert.equal(accountMail.deliverable(tombstoneEmail(9)), false);
  // The other arm: real addresses, including ones on uncommon TLDs.
  for (const real of ['a@example.com', 'first.last@sub.example.co.uk', 'me@humanoid-traders.com']) {
    assert.equal(accountMail.deliverable(real), true, real);
  }
});

test('a failed send is reported, not thrown, and logs no SMTP text', async () => {
  const real = mailer.sendMail;
  const logged = [];
  const realErr = console.error;
  mailer.sendMail = async () => { throw new Error('550 5.1.1 <victim@example.com> does not exist'); };
  console.error = (...a) => logged.push(a.join(' '));
  try {
    const r = await accountMail.notify('totp_enabled', 'victim@example.com');
    assert.deepEqual(r, { sent: false, reason: 'failed' });
  } finally {
    mailer.sendMail = real;
    console.error = realErr;
  }
  assert.equal(logged.length, 1);
  assert.doesNotMatch(logged[0], /victim|550/, logged[0]);
});

// ── the tier notice ────────────────────────────────────────────────────────

const SECRET = process.env.BOT_SYNC_SECRET;

test('the bot\'s tier notice emails a website-only member, once, by name', async () => {
  const email = 'tiered@example.com';
  const { id } = await member(email);
  let from = sent.length;
  const r = await call('POST', '/api/bot/sync/tier-notice', { botSecret: SECRET, body: { member: `web:${id}`, tier: 'pro' } });
  assert.equal(r.status, 200, JSON.stringify(r.json));
  assert.deepEqual(r.json, { sent: true });
  const got = mailTo(email, from);
  assert.equal(got.length, 1);
  assert.equal(got[0].subject, 'Your RUNECLAW plan is now Pro');
  assert.match(got[0].text, /https:\/\/app\.test\/dashboard/);

  // Without the bot's secret nothing is sent.
  from = sent.length;
  const anon = await call('POST', '/api/bot/sync/tier-notice', { body: { member: `web:${id}`, tier: 'elite' } });
  assert.equal(anon.status, 403);
  assert.deepEqual(mailTo(email, from), []);
});

test('a linked account, a Telegram key, the operator tier and a stranger are not mailed', async () => {
  const email = 'linkedtier@example.com';
  const { id } = await member(email);
  await pool.execute('UPDATE users SET telegram_linked = TRUE, telegram_id = ? WHERE id = ?', ['880001', id]);
  const from = sent.length;
  const cases = [
    [{ member: `web:${id}`, tier: 'pro' }, 200, 'linked'],
    [{ member: '880001', tier: 'pro' }, 400, 'not_a_website_member'],
    [{ member: `web:${id}`, tier: 'admin' }, 400, 'no_such_plan'],
    [{ member: 'web:999999', tier: 'pro' }, 404, 'no_such_member'],
  ];
  for (const [body, status, reason] of cases) {
    const r = await call('POST', '/api/bot/sync/tier-notice', { botSecret: SECRET, body });
    assert.equal(r.status, status, JSON.stringify([body, r.json]));
    assert.equal(r.json.sent, false);
    assert.equal(r.json.reason, reason, JSON.stringify(r.json));
  }
  assert.deepEqual(sent.slice(from), []);
});

test('the whole-book tier mirror changes a plan and mails nobody', async () => {
  const email = 'mirror@example.com';
  const { id } = await member(email);
  const from = sent.length;
  const r = await call('POST', '/api/bot/sync/tiers', { botSecret: SECRET,
    body: { tiers: [{ telegram_id: `web:${id}`, tier: 'elite' }] } });
  assert.equal(r.status, 200);
  assert.equal(r.json.updated, 1, 'the fixture did not change the plan it says nothing about');
  assert.deepEqual(sent.slice(from), [], 'a reconciliation push mailed the member');
});
