'use strict';
/**
 * The three token mails (the combined welcome, the verify resend and the
 * reset link) come out of the branded templates, say what Marketing's copy
 * says, stay security mail, and are never sent to an address the app
 * synthesised.
 *
 * Its own file because registration and forgot-password share one per-address
 * limit of ten attempts, and test/email_onboarding.test.js already spends
 * eight of them; each test file runs in its own process with its own count.
 * The behaviour that file pins (one combined mail, the honesty constraints,
 * the enumeration rules) is unchanged and stays there.
 */

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
process.env.APP_BASE_URL = 'https://app.test';

const test = require('node:test');
const assert = require('node:assert');
const express = require('express');

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

test('the combined mail is a security mail: verify link, its lifetime, no opt-out, the credit line', async () => {
  sent.length = 0;
  await withMailer(true, async () => {
    await post('/api/auth/register', { email: 'welcome3@test.io', password: 'correct-horse-battery' });
  });
  assert.strictEqual(sent.length, 1);
  const m = sent[0];
  assert.strictEqual(m.subject, 'Welcome to RUNECLAW — confirm your email');
  assert.match(m.text, /^Confirm your email\nhttps:\/\/app\.test\/verify\?token=[a-f0-9]{64}$/m);
  assert.match(m.html, /href="https:\/\/app\.test\/verify\?token=[a-f0-9]{64}"/);
  assert.ok(m.text.includes('This link expires in 24 hours.'));
  assert.ok(m.text.includes('If you didn’t create an account, ignore this message.'));
  assert.ok(m.text.includes('RUNECLAW is a trading tool, not financial advice.'));
  assert.ok(m.text.includes('HUMANOID-TRADERS · RUNECLAW'));
  assert.doesNotMatch(m.text + m.html, /unsubscribe|email-preferences/i,
    'the mail that carries the verify link must not offer a way to stop it');
  assert.strictEqual(m.listUnsubscribe, undefined);
});

test('the resend is the standalone verify mail', async () => {
  sent.length = 0;
  await withMailer(true, async () => {
    const reg = await post('/api/auth/register',
      { email: 'resend2@test.io', password: 'correct-horse-battery' });
    sent.length = 0;
    await post('/api/auth/send-verification', {}, reg.body.token);
  });
  assert.strictEqual(sent.length, 1);
  assert.strictEqual(sent[0].subject, 'Verify your RUNECLAW email');
  assert.ok(sent[0].text.includes('It does not start live trading.'));
  assert.doesNotMatch(sent[0].text + sent[0].html, /unsubscribe/i);
});

test('the reset link mail says how long it lasts and what to do if it was not asked for', async () => {
  sent.length = 0;
  await withMailer(true, async () => {
    await post('/api/auth/register', { email: 'reset-copy@test.io', password: 'correct-horse-battery' });
    sent.length = 0;
    await post('/api/auth/forgot-password', { email: 'reset-copy@test.io' });
  });
  assert.strictEqual(sent.length, 1);
  const m = sent[0];
  assert.strictEqual(m.subject, 'Reset your RUNECLAW password');
  assert.match(m.text, /^Choose a new password\nhttps:\/\/app\.test\/reset\?token=[a-f0-9]{64}$/m);
  assert.ok(m.text.includes('The link expires in 30 minutes.'));
  assert.ok(m.html.includes('If you didn’t request this, ignore this email. Your password is unchanged.'),
    'the HTML part must still say a reset nobody asked for can be ignored');
  assert.doesNotMatch(m.text + m.html, /unsubscribe/i);
});

test('an address the app synthesised is never sent a verify or reset link', async () => {
  // Registration takes any address-shaped string, including the two shapes
  // the app writes for accounts with no inbox. Neither may be mailed.
  sent.length = 0;
  let reg;
  await withMailer(true, async () => {
    reg = await post('/api/auth/register',
      { email: 'someone@telegram.runeclaw.local', password: 'correct-horse-battery' });
    assert.strictEqual(reg.status, 200, JSON.stringify(reg.body));
    const known = await post('/api/auth/forgot-password', { email: 'someone@telegram.runeclaw.local' });
    const stranger = await post('/api/auth/forgot-password', { email: 'nobody-at-all@test.io' });
    assert.deepStrictEqual(known.body, stranger.body, 'the refusal became an enumeration oracle');
  });
  assert.deepStrictEqual(sent, [], 'a placeholder address was mailed');
  assert.strictEqual(reg.body.email_pending, false, 'registration said a mail was on its way');
  // The other arm: a real address registered beside it is mailed.
  await withMailer(true, async () => {
    const ok = await post('/api/auth/register', { email: 'real-one@test.io', password: 'correct-horse-battery' });
    assert.strictEqual(ok.body.email_pending, true);
  });
  assert.strictEqual(sent.length, 1);
});

test.after(() => {
  mailer.sendMail = realSend;
  mailer.isConfigured = realIsConfigured;
  if (server) server.close();
});
