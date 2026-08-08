'use strict';
/**
 * Transactional email: the welcome, and the reset that could never arrive.
 *
 * Two findings from the 2026-08-08 pass over the account experience:
 *
 *  1. /forgot-password always answered "If that email has an account, a reset
 *     link is on its way." That sentence is FALSE whenever SMTP is
 *     unconfigured, which is the state a fresh deploy is in — the mailer is a
 *     deliberate no-op that logs what it would have sent. The person then
 *     waits, checks spam, and concludes the account is broken.
 *
 *     Saying so leaks nothing. The generic body exists to hide whether an
 *     ACCOUNT exists; whether the SERVER has a mail relay is identical for
 *     every address, including ones with no account. So the unconfigured
 *     branch is honest and the configured branch keeps the generic wording —
 *     including when a send throws, because only accounts with a password
 *     attempt one and a distinct error there WOULD be an enumeration oracle.
 *
 *  2. Registration sent a bare "Verify your RUNECLAW email" and nothing else.
 *     A first email is the one moment the product can say what the account
 *     actually gets, so the welcome and the verify link are ONE message — two
 *     back to back read as a mailing list, and only one of them needs an
 *     action. What it says is bounded by the same rule the bot's onboarding
 *     was fixed for: paper works now, Telegram is optional, live needs the
 *     operator. A welcome implying live trading is a tap away only moves the
 *     promise-then-refuse later.
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
process.env.APP_BASE_URL = 'https://app.test';

const test = require('node:test');
const assert = require('node:assert');
const express = require('express');

// Capture what the mailer WOULD send, with no network. auth.js holds a module
// reference (`const mailer = require('./lib/mailer')`), so patching the
// exported functions on the shared module object is what both sides see.
const mailer = require('../lib/mailer');
const { router: authRouter } = require('../auth');

const sent = [];
const realSend = mailer.sendMail;
const realIsConfigured = mailer.isConfigured;
mailer.sendMail = async (msg) => { sent.push(msg); return { skipped: false }; };

let server;
let base;

test.before(async () => {
  const a = express();
  a.use(express.json());
  a.use('/api/auth', authRouter);
  await new Promise((resolve) => {
    server = a.listen(0, '127.0.0.1', () => {
      base = `http://127.0.0.1:${server.address().port}`;
      resolve();
    });
  });
});

async function post(path, body, token) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = 'Bearer ' + token;
  const r = await fetch(base + path, {
    method: 'POST', headers, body: JSON.stringify(body || {}),
  });
  return { status: r.status, body: await r.json() };
}

async function withMailer(configured, fn) {
  mailer.isConfigured = () => configured;
  try {
    return await fn();
  } finally {
    mailer.isConfigured = realIsConfigured;
  }
}

test('registration sends ONE email that both welcomes and verifies', async () => {
  sent.length = 0;
  await withMailer(true, async () => {
    const r = await post('/api/auth/register',
      { email: 'welcome1@test.io', password: 'correct-horse-battery' });
    assert.strictEqual(r.status, 200, JSON.stringify(r.body));
  });
  assert.strictEqual(sent.length, 1, 'one email, not a welcome AND a verify');
  assert.match(sent[0].subject, /welcome/i, 'the first email should welcome them');
  assert.match(sent[0].text, /\/verify\?token=/, 'and still carry the verify link');
});

test('the welcome states what the account can do NOW, and what it cannot', async () => {
  sent.length = 0;
  await withMailer(true, async () => {
    await post('/api/auth/register',
      { email: 'welcome2@test.io', password: 'correct-horse-battery' });
  });
  const body = (sent[0].text + ' ' + sent[0].html).toLowerCase();
  assert.ok(body.includes('paper'), 'paper trading is what works immediately');
  assert.ok(body.includes('operator'),
    'live trading needs operator approval — a welcome that implies otherwise '
    + 'just moves the promise-then-refuse to the first live attempt');
  assert.ok(body.includes('telegram'), 'name the optional next step');
});

test('a resend is the short version, not the whole welcome again', async () => {
  sent.length = 0;
  await withMailer(true, async () => {
    const reg = await post('/api/auth/register',
      { email: 'resend@test.io', password: 'correct-horse-battery' });
    sent.length = 0;
    const r = await post('/api/auth/send-verification', {}, reg.body.token);
    assert.strictEqual(r.status, 200, JSON.stringify(r.body));
  });
  assert.strictEqual(sent.length, 1);
  assert.doesNotMatch(sent[0].subject, /welcome/i,
    'by the resend they know what the product is; repeating the pitch is noise');
  assert.match(sent[0].text, /\/verify\?token=/);
});

test('forgot-password does not claim a link is coming when SMTP is a no-op', async () => {
  await withMailer(false, async () => {
    const r = await post('/api/auth/forgot-password', { email: 'anyone@test.io' });
    assert.strictEqual(r.status, 200);
    assert.strictEqual(r.body.ok, false);
    assert.strictEqual(r.body.error, 'email_not_configured');
    assert.doesNotMatch(r.body.message, /on its way/i,
      'the whole defect: a delivery promise no mailer could keep');
    assert.match(r.body.message, /operator/i, 'name who can fix it');
  });
});

test('and the unconfigured answer is identical for an account and a stranger', async () => {
  // The honest branch must not become an enumeration oracle: SMTP being unset
  // is a property of the server, so every address has to get the same words.
  await withMailer(true, async () => {
    await post('/api/auth/register',
      { email: 'exists@test.io', password: 'correct-horse-battery' });
  });
  await withMailer(false, async () => {
    const known = await post('/api/auth/forgot-password', { email: 'exists@test.io' });
    const unknown = await post('/api/auth/forgot-password', { email: 'nobody-here@test.io' });
    assert.deepStrictEqual(known.body, unknown.body);
  });
});

test('with SMTP configured it keeps the generic, non-enumerating wording', async () => {
  sent.length = 0;
  await withMailer(true, async () => {
    await post('/api/auth/register',
      { email: 'reset-me@test.io', password: 'correct-horse-battery' });
    sent.length = 0;
    const known = await post('/api/auth/forgot-password', { email: 'reset-me@test.io' });
    const unknown = await post('/api/auth/forgot-password', { email: 'not-a-user@test.io' });
    assert.deepStrictEqual(known.body, unknown.body,
      'the configured branch must stay indistinguishable');
    assert.match(known.body.message, /on its way/i);
  });
  assert.strictEqual(sent.length, 1, 'only the real account gets mail');
  assert.match(sent[0].text, /\/reset\?token=/);
});

test.after(() => {
  mailer.sendMail = realSend;
  mailer.isConfigured = realIsConfigured;
  if (server) server.close();
});
