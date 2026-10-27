'use strict';
/**
 * HashKey seal route. Admin-only. A preview does not sign. A non-operator
 * gets 404 whether the flag is on or off.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.HASHKEY_SEAL_ENABLED;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const route = require('../routes/hashkey_seal');
const flag = require('../lib/hashkey_seal_flag');

const ADMIN_ID = 94021;
const USER_ID = 94022;
pool.users.push({ id: ADMIN_ID, email: 'hashkey-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: USER_ID, email: 'hashkey-user@example.com', plan: 'free', token_epoch: 0 });

function tokenFor(id, email) {
  return jwt.sign({ user_id: id, email, epoch: 0 }, process.env.JWT_SECRET);
}
const adminToken = tokenFor(ADMIN_ID, 'hashkey-admin@example.com');
const userToken = tokenFor(USER_ID, 'hashkey-user@example.com');

const HASH = '0x' + '11'.repeat(32);
const KIND = '0x' + '22'.repeat(32);
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
  return { status: res.status, body: parsed };
}

function app() {
  const server = express();
  server.use(express.json());
  server.use('/api/hashkey-seal', route);
  return server;
}

test('flag is off unless the value is 1 or true', () => {
  assert.equal(flag.flagOn({}), false);
  assert.equal(flag.flagOn({ HASHKEY_SEAL_ENABLED: 'on' }), false);
  assert.equal(flag.flagOn({ HASHKEY_SEAL_ENABLED: 'yes' }), false);
  assert.equal(flag.flagOn({ HASHKEY_SEAL_ENABLED: ' 1 ' }), true);
  assert.equal(flag.flagOn({ HASHKEY_SEAL_ENABLED: 'TRUE' }), true);
});

test('the route is authenticated and does not sign', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'hashkey_seal.js'), 'utf8');
  assert.match(src, /optionalAuth/);
  assert.match(src, /flag_on_for|accessFor/);
  assert.doesNotMatch(src, /signTransaction|sendRawTransaction|new Wallet\(|eth_sendRawTransaction/);
  assert.doesNotMatch(src, /chainId\s*[:=]\s*177\b/);
  assert.doesNotMatch(src, /spawnSync/);
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /\/api\/hashkey-seal/);
});

test('a non-operator gets 404, and a preview does not publish', async () => {
  calls.length = 0;
  route.setRunnerForTests(scripted({ flag_on: false, chain_id: 133, readable: true, anchored: false }));
  delete process.env.HASHKEY_SEAL_ENABLED;
  const server = await listen(app());
  try {
    const anon = await hit(server, '/api/hashkey-seal');
    assert.equal(anon.status, 404);
    const user = await hit(server, '/api/hashkey-seal', { bearer: userToken });
    assert.equal(user.status, 404);
    const userPost = await hit(server, '/api/hashkey-seal/anchor', {
      bearer: userToken, method: 'POST', body: { hash: HASH, kind: KIND },
    });
    assert.equal(userPost.status, 404);
    const preview = await hit(server, '/api/hashkey-seal', { bearer: adminToken });
    assert.equal(preview.status, 200);
    assert.equal(preview.body.preview, true);
    assert.match(preview.body.banner, /Admin preview/);
    const post = await hit(server, '/api/hashkey-seal/anchor', {
      bearer: adminToken, method: 'POST', body: { hash: HASH, kind: KIND },
    });
    assert.equal(post.status, 403);
    assert.equal(post.body.signed, false);
    assert.equal(post.body.broadcast, false);
    assert.deepEqual(calls, [['status']]);
  } finally {
    route.setRunnerForTests(null);
    await close(server);
  }
});

test('an operator publishes only when the flag is on, and a bad hash is refused', async () => {
  calls.length = 0;
  process.env.HASHKEY_SEAL_ENABLED = '1';
  const tx = '0x' + 'ab'.repeat(32);
  route.setRunnerForTests(scripted({
    ok: true, signed: true, broadcast: true, reason: null, tx_hash: tx, anchored: null,
  }));
  const server = await listen(app());
  try {
    const user = await hit(server, '/api/hashkey-seal/anchor', {
      bearer: userToken, method: 'POST', body: { hash: HASH, kind: KIND },
    });
    assert.equal(user.status, 404);
    const bad = await hit(server, '/api/hashkey-seal/anchor', {
      bearer: adminToken, method: 'POST', body: { hash: '0x1234', kind: KIND },
    });
    assert.equal(bad.status, 400);
    assert.equal(bad.body.signed, false);
    const posted = await hit(server, '/api/hashkey-seal/anchor', {
      bearer: adminToken, method: 'POST', body: { hash: HASH, kind: KIND },
    });
    assert.equal(posted.status, 200);
    assert.equal(posted.body.ok, true);
    assert.equal(posted.body.tx_hash, tx);
    assert.equal(posted.body.preview, false);
    assert.deepEqual(calls, [['publish', HASH, KIND]]);
    const unread = await hit(server, '/api/hashkey-seal?hash=0x' + 'zz'.repeat(32), { bearer: adminToken });
    assert.equal(unread.status, 400);
    assert.equal(unread.body.anchored, null);
  } finally {
    delete process.env.HASHKEY_SEAL_ENABLED;
    route.setRunnerForTests(null);
    await close(server);
  }
});

test('an unreadable chain read is not reported as not anchored', async () => {
  process.env.HASHKEY_SEAL_ENABLED = '1';
  route.setRunnerForTests(scripted({
    readable: false, anchored: null, reason: 'rpc_unreadable',
  }));
  const server = await listen(app());
  try {
    const got = await hit(server, '/api/hashkey-seal?hash=' + HASH, { bearer: adminToken });
    assert.equal(got.status, 503);
    assert.equal(got.body.readable, false);
    assert.equal(got.body.anchored, null);
    assert.notEqual(got.body.anchored, false);
  } finally {
    delete process.env.HASHKEY_SEAL_ENABLED;
    route.setRunnerForTests(null);
    await close(server);
  }
});

test('the parent waits longer than the child\'s own deadline', () => {
  assert.equal(route.childDeadlineSec({}), route.DEFAULT_CHILD_DEADLINE_SEC);
  assert.equal(route.childDeadlineSec({ HASHKEY_SEAL_DEADLINE_SEC: '9' }), 9);
  assert.equal(route.childDeadlineSec({ HASHKEY_SEAL_DEADLINE_SEC: 'soon' }), null);
  assert.equal(route.childDeadlineSec({ HASHKEY_SEAL_DEADLINE_SEC: '-1' }), null);
  assert.ok(route.parentTimeoutMs({}) > route.DEFAULT_CHILD_DEADLINE_SEC * 1000);
  assert.ok(route.parentTimeoutMs({ HASHKEY_SEAL_DEADLINE_SEC: '30' }) > 30000);
  assert.ok(route.parentTimeoutMs({ HASHKEY_SEAL_DEADLINE_SEC: 'soon' }) > route.DEFAULT_CHILD_DEADLINE_SEC * 1000);
});

test('a child that times out, crashes, or prints garbage is unknown on a publish and unreadable on a read', async () => {
  process.env.HASHKEY_SEAL_ENABLED = '1';
  const server = await listen(app());
  const reported = '0x' + 'ab'.repeat(32);
  const runs = [
    { status: null, signal: 'SIGTERM', timedOut: true, stdout: '', stderr: `tx_hash=${reported} nonce=3 sender=0x1\n`, expectReason: 'timed_out', expectHash: reported },
    { status: 1, stdout: '', stderr: 'RuntimeError\n', expectReason: 'child_failed', expectHash: null },
    { status: 0, stdout: 'not json\n', expectReason: 'child_failed', expectHash: null },
    { status: 0, stdout: '[]\n', expectReason: 'child_failed', expectHash: null },
    { status: 0, stdout: '', expectReason: 'child_failed', expectHash: null },
  ];
  try {
    for (const run of runs) {
      route.setRunnerForTests(() => run);
      const got = await hit(server, '/api/hashkey-seal?hash=' + HASH, { bearer: adminToken });
      assert.equal(got.status, 503);
      assert.equal(got.body.readable, false);
      assert.equal(got.body.anchored, null);
      assert.ok(!('signed' in got.body), 'a read never signs, and says nothing about it');
      const posted = await hit(server, '/api/hashkey-seal/anchor', {
        bearer: adminToken, method: 'POST', body: { hash: HASH, kind: KIND },
      });
      assert.equal(posted.status, 503);
      assert.equal(posted.body.reason, run.expectReason);
      assert.equal(posted.body.signed, null);
      assert.equal(posted.body.broadcast, null);
      assert.equal(posted.body.anchored, null);
      assert.equal(posted.body.readable, null);
      assert.equal(posted.body.tx_hash, run.expectHash);
      assert.notEqual(posted.body.signed, false);
      assert.notEqual(posted.body.broadcast, false);
    }
    route.setRunnerForTests(() => Promise.resolve({ status: 0, stdout: JSON.stringify({ ok: true, signed: true, broadcast: true, anchored: true, reason: null }) + '\n' }));
    const awaited = await hit(server, '/api/hashkey-seal/anchor', {
      bearer: adminToken, method: 'POST', body: { hash: HASH, kind: KIND },
    });
    assert.equal(awaited.status, 200, 'a runner that returns a promise is awaited');
  } finally {
    delete process.env.HASHKEY_SEAL_ENABLED;
    route.setRunnerForTests(null);
    await close(server);
  }
});

test('an unread reason is 503 on a publish too, and a submitted publish is 202', async () => {
  process.env.HASHKEY_SEAL_ENABLED = '1';
  const server = await listen(app());
  const tx = '0x' + 'cd'.repeat(32);
  try {
    for (const reason of ['rpc_unreadable', 'chain_unreadable', 'result_unreadable']) {
      route.setRunnerForTests(scripted({ ok: false, signed: false, broadcast: false, reason, anchored: null }));
      const posted = await hit(server, '/api/hashkey-seal/anchor', {
        bearer: adminToken, method: 'POST', body: { hash: HASH, kind: KIND },
      });
      assert.equal(posted.status, 503, reason);
      assert.equal(posted.body.anchored, null);
      const read = await hit(server, '/api/hashkey-seal?hash=' + HASH, { bearer: adminToken });
      assert.equal(read.status, 503, reason);
    }
    for (const reason of ['submitted', 'send_unconfirmed', 'hash_mismatch', 'receipt_unreadable']) {
      route.setRunnerForTests(scripted({ ok: null, signed: true, broadcast: reason === 'submitted' ? true : null, reason, tx_hash: tx, anchored: null }));
      const posted = await hit(server, '/api/hashkey-seal/anchor', {
        bearer: adminToken, method: 'POST', body: { hash: HASH, kind: KIND },
      });
      assert.equal(posted.status, 202, reason);
      assert.equal(posted.body.ok, null);
      assert.equal(posted.body.tx_hash, tx);
    }
    route.setRunnerForTests(scripted({ ok: false, signed: true, broadcast: true, reason: 'reverted', tx_hash: tx, anchored: false }));
    const reverted = await hit(server, '/api/hashkey-seal/anchor', {
      bearer: adminToken, method: 'POST', body: { hash: HASH, kind: KIND },
    });
    assert.equal(reverted.status, 200);
    assert.equal(reverted.body.ok, false);
    assert.equal(reverted.body.reason, 'reverted');
  } finally {
    delete process.env.HASHKEY_SEAL_ENABLED;
    route.setRunnerForTests(null);
    await close(server);
  }
});

test('the route adds nothing from its own environment to a publish body', async () => {
  process.env.HASHKEY_SEAL_ENABLED = '1';
  process.env.HASHKEY_SEAL_SIGNER_KEY = 'ab'.repeat(32);
  process.env.HASHKEY_SEAL_RPC_URL = 'https://rpc.example/secret-path';
  const server = await listen(app());
  try {
    route.setRunnerForTests(scripted({ ok: true, signed: true, broadcast: true, reason: null, tx_hash: '0x' + 'cd'.repeat(32), anchored: true }));
    const posted = await hit(server, '/api/hashkey-seal/anchor', {
      bearer: adminToken, method: 'POST', body: { hash: HASH, kind: KIND },
    });
    assert.equal(posted.status, 200);
    const text = JSON.stringify(posted.body);
    assert.doesNotMatch(text, /ab{32}|secret-path|HASHKEY_SEAL_SIGNER_KEY/);
    assert.deepEqual(Object.keys(posted.body).sort(), ['anchored', 'banner', 'broadcast', 'ok', 'preview', 'reason', 'signed', 'tx_hash']);
  } finally {
    delete process.env.HASHKEY_SEAL_ENABLED;
    delete process.env.HASHKEY_SEAL_SIGNER_KEY;
    delete process.env.HASHKEY_SEAL_RPC_URL;
    route.setRunnerForTests(null);
    await close(server);
  }
});
