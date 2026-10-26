'use strict';
/**
 * Claw Guard signature decoder. While SIG_DECODER_ENABLED is off, a
 * non-operator gets 404 and an operator gets the decode with the preview
 * banner. With the flag on, a caller who passed optionalAuth gets the
 * decode. The answer is public signals, labelled AI-assisted.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.SIG_DECODER_ENABLED;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const route = require('../routes/sig_decoder');
const flag = require('../lib/sig_decoder_flag');

const ADMIN_ID = 95031;
const USER_ID = 95032;
pool.users.push({ id: ADMIN_ID, email: 'sigdec-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: USER_ID, email: 'sigdec-user@example.com', plan: 'free', token_epoch: 0 });

function tokenFor(id, email) {
  return jwt.sign({ user_id: id, email, epoch: 0 }, process.env.JWT_SECRET);
}
const adminToken = tokenFor(ADMIN_ID, 'sigdec-admin@example.com');
const userToken = tokenFor(USER_ID, 'sigdec-user@example.com');

const calls = [];

function scripted(body, status) {
  return function (stdin) {
    calls.push(stdin);
    return { status: status == null ? 0 : status, stdout: JSON.stringify(body) + '\n' };
  };
}

const READING = {
  ok: true,
  kind: 'raw_message',
  summary: ['Message text: hello'],
  fields: { method: 'personal_sign', length: 5 },
  signals: [{ code: 'NOTE', level: 'info', text: 'A public signal.' }],
  attention_count: 0,
  disclaimer: flag.DISCLAIMER,
  assisted: 'ai',
  reads_network: false,
};

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
  const res = await fetch(`http://127.0.0.1:${server.address().port}/api/guard/sigdecode`, {
    method: 'POST', headers, body: JSON.stringify(body == null ? { input: 'hello' } : body),
  });
  const raw = await res.text();
  let parsed = raw;
  try { parsed = JSON.parse(raw); } catch (err) { parsed = raw; }
  return { status: res.status, body: parsed, cache: res.headers.get('cache-control') };
}

function app() {
  const server = express();
  server.use(express.json({ limit: '1mb' }));
  server.use('/api/guard/sigdecode', route);
  return server;
}

test('flag is off unless the value is 1 or true', () => {
  assert.equal(flag.flagOn({}), false);
  assert.equal(flag.flagOn({ SIG_DECODER_ENABLED: 'on' }), false);
  assert.equal(flag.flagOn({ SIG_DECODER_ENABLED: 'yes' }), false);
  assert.equal(flag.flagOn({ SIG_DECODER_ENABLED: ' 1 ' }), true);
  assert.equal(flag.flagOn({ SIG_DECODER_ENABLED: 'TRUE' }), true);
  assert.equal(flag.DISCLAIMER, 'AI-assisted. Public signals, not a verdict.');
});

test('the route is authenticated, mounted, and does not sign', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'sig_decoder.js'), 'utf8');
  assert.match(src, /optionalAuth/);
  assert.doesNotMatch(src, /signTransaction|sendRawTransaction|eth_sendRawTransaction|privateKey/);
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /\/api\/guard\/sigdecode/);
  const py = fs.readFileSync(path.join(__dirname, '..', '..', 'bot', 'sigdecoder', 'core.py'), 'utf8');
  assert.match(py, /AI-assisted\. Public signals, not a verdict\./);
});

test('flag off is 404 for everyone except an operator preview', async () => {
  calls.length = 0;
  route.setRunnerForTests(scripted(READING));
  delete process.env.SIG_DECODER_ENABLED;
  const server = await listen(app());
  try {
    const anon = await hit(server);
    assert.equal(anon.status, 404);
    const user = await hit(server, { bearer: userToken });
    assert.equal(user.status, 404);
    const admin = await hit(server, { bearer: adminToken });
    assert.equal(admin.status, 200);
    assert.equal(admin.cache, 'private, no-store');
    assert.equal(admin.body.admin_preview, true);
    assert.equal(admin.body.banner, 'Admin preview. Not public.');
    assert.equal(admin.body.disclaimer, flag.DISCLAIMER);
    assert.equal(admin.body.assisted, 'ai');
    assert.equal(admin.body.reads_network, false);
    assert.ok(Array.isArray(admin.body.signals));
    assert.equal(calls.length, 1);
  } finally {
    route.setRunnerForTests(null);
    await close(server);
  }
});

test('flag on admits an anonymous caller and drops the preview', async () => {
  calls.length = 0;
  process.env.SIG_DECODER_ENABLED = '1';
  route.setRunnerForTests(scripted(READING));
  const server = await listen(app());
  try {
    const anon = await hit(server);
    assert.equal(anon.status, 200);
    assert.equal(anon.body.admin_preview, false);
    assert.equal(anon.body.banner, undefined);
    assert.equal(anon.body.disclaimer, flag.DISCLAIMER);
    assert.equal(anon.cache, 'private, no-store');
    const user = await hit(server, { bearer: userToken });
    assert.equal(user.status, 200);
    assert.equal(user.body.admin_preview, false);
  } finally {
    delete process.env.SIG_DECODER_ENABLED;
    route.setRunnerForTests(null);
    await close(server);
  }
});

test('a bad payload, a bad kind, and an oversized body are not a decode', async () => {
  process.env.SIG_DECODER_ENABLED = '1';
  route.setRunnerForTests(scripted(READING));
  const server = await listen(app());
  try {
    const missing = await hit(server, { body: {} });
    assert.equal(missing.status, 400);
    assert.equal(missing.body.error, 'bad_payload');
    assert.equal(missing.body.signals, null);
    assert.equal(missing.body.disclaimer, flag.DISCLAIMER);
    const kind = await hit(server, { body: { kind: 'nope', input: 'hello' } });
    assert.equal(kind.status, 400);
    assert.equal(kind.body.error, 'bad_kind');
    assert.equal(kind.body.signals, null);
    const huge = await hit(server, { body: { input: 'a'.repeat(70000) } });
    assert.equal(huge.status, 400);
    assert.equal(huge.body.error, 'too_large');
    assert.equal(huge.body.signals, null);
  } finally {
    delete process.env.SIG_DECODER_ENABLED;
    route.setRunnerForTests(null);
    await close(server);
  }
});

test('an unreadable decoder is not an empty signal list', async () => {
  process.env.SIG_DECODER_ENABLED = '1';
  route.setRunnerForTests(scripted({ ok: false, error: 'RuntimeError', signals: null, disclaimer: flag.DISCLAIMER, assisted: 'ai' }, 1));
  const server = await listen(app());
  try {
    const got = await hit(server);
    assert.equal(got.status, 503);
    assert.equal(got.body.signals, null);
    assert.equal(got.body.error, 'SigDecoderUnreadable');
    assert.equal(got.body.disclaimer, flag.DISCLAIMER);
    const mismatched = scripted(Object.assign({}, READING, { disclaimer: 'trust this' }));
    route.setRunnerForTests(mismatched);
    const bad = await hit(server);
    assert.equal(bad.status, 503);
    assert.equal(bad.body.signals, null);
  } finally {
    delete process.env.SIG_DECODER_ENABLED;
    route.setRunnerForTests(null);
    await close(server);
  }
});

test('the live decoder answers an operator with public signals', async () => {
  delete process.env.SIG_DECODER_ENABLED;
  route.setRunnerForTests(null);
  const server = await listen(app());
  try {
    const admin = await hit(server, { bearer: adminToken, body: { input: 'hello' } });
    assert.equal(admin.status, 200);
    assert.equal(admin.body.admin_preview, true);
    assert.equal(admin.body.disclaimer, flag.DISCLAIMER);
    assert.equal(admin.body.assisted, 'ai');
    assert.equal(admin.body.reads_network, false);
    assert.ok(Array.isArray(admin.body.signals));
    const blob = JSON.stringify(admin.body).toLowerCase();
    for (const word of ['is safe', 'is a scam', 'scammer', 'safe to sign', 'legal advice']) {
      assert.equal(blob.includes(word), false, word);
    }
    const anon = await hit(server, { body: { input: 'hello' } });
    assert.equal(anon.status, 404);
  } finally {
    route.setRunnerForTests(null);
    await close(server);
  }
});
