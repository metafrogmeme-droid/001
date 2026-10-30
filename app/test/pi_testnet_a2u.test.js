'use strict';
/**
 * Temporary Testnet-only App-to-User payout (lib/pi_testnet_a2u.js).
 *
 * The flag is off unless it is exactly "1", and while it is off the seed is
 * not read. The Mainnet key is not a name in this file. A payment is signed
 * with "Pi Testnet" and submitted only to the Testnet horizon. A Mainnet
 * network, a Mainnet horizon, or a seed whose public key is a configured
 * Mainnet wallet is refused before anything is signed. One uid, once, at
 * most ten, and only a uid that signed in to the Testnet app. The seed and
 * the API key never appear in a log line or a response.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawnSync } = require('node:child_process');
const express = require('express');
const { Keypair, Account } = require('@stellar/stellar-sdk');

const a2u = require('../lib/pi_testnet_a2u');
const auth = require('../auth');
const { pool } = require('../db');
const { codeOnly } = require('./helpers/code_only');

const LIB = fs.readFileSync(path.join(__dirname, '..', 'lib', 'pi_testnet_a2u.js'), 'utf8');
const ROUTE = fs.readFileSync(path.join(__dirname, '..', 'routes', 'pi_testnet_a2u.js'), 'utf8');
const SCRIPT = fs.readFileSync(path.join(__dirname, '..', '..', 'scripts', 'pi_testnet_a2u.js'), 'utf8');
const API_KEY = 'testnet-server-key-not-a-secret-fixture';

function capture() {
  const lines = [];
  const orig = {
    log: console.log, error: console.error, warn: console.warn, info: console.info,
  };
  const wrap = (...args) => {
    lines.push(args.map((a) => {
      if (typeof a === 'string') return a;
      try { return JSON.stringify(a); } catch (e) { return String(a && a.name); }
    }).join(' '));
  };
  console.log = wrap;
  console.error = wrap;
  console.warn = wrap;
  console.info = wrap;
  return {
    lines,
    restore() { Object.assign(console, orig); },
  };
}

function envOf(kp, extra = {}) {
  return {
    PI_TESTNET_A2U_ENABLED: '1',
    PI_TESTNET_API_KEY: API_KEY,
    PI_TESTNET_APP_WALLET_SEED: kp.secret(),
    ...extra,
  };
}

function horizon() {
  const submitted = [];
  let n = 0;
  return {
    submitted,
    loadAccount: async (pk) => new Account(pk, '1'),
    submitTransaction: async (tx) => {
      submitted.push(tx);
      n += 1;
      return { hash: (n.toString(16).padStart(2, '0') + 'ab'.repeat(32)).slice(0, 64) };
    },
  };
}

function payment(over) {
  return {
    identifier: over.identifier,
    user_uid: over.uid,
    amount: over.amount == null ? 0.01 : over.amount,
    memo: over.memo || 'RUNECLAW testnet',
    metadata: { kind: 'testnet_a2u' },
    from_address: over.from,
    to_address: over.to,
    direction: over.direction || 'app_to_user',
    network: over.network == null ? 'Pi Testnet' : over.network,
    status: {
      developer_approved: true, transaction_verified: false, developer_completed: false,
      cancelled: false, user_cancelled: false, ...(over.status || {}),
    },
    transaction: over.txid ? { txid: over.txid } : null,
  };
}

function fakePi(opts) {
  const calls = [];
  const payments = new Map();
  let seq = 0;
  for (const item of opts.incomplete || []) payments.set(item.identifier, item);
  const fetchImpl = async (url, init = {}) => {
    const u = new URL(url);
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push({
      method: init.method, path: u.pathname, body,
      auth: init.headers && init.headers.authorization,
    });
    const reply = (status, payload) => ({
      status, ok: status >= 200 && status < 300,
      json: async () => {
        if (payload === undefined) throw new SyntaxError('not json');
        return payload;
      },
    });
    if (u.pathname === '/v2/payments/incomplete_server_payments') {
      if (opts.incompleteStatus) return reply(opts.incompleteStatus, opts.incompleteBody);
      return reply(200, { incomplete_server_payments: opts.incomplete || [] });
    }
    if (u.pathname === '/v2/payments' && init.method === 'POST') {
      if (opts.failCreate) return reply(500, { no: API_KEY });
      seq += 1;
      const id = opts.identifier || `p${seq}${Math.random().toString(36).slice(2, 10)}`;
      const p = payment({
        identifier: id, uid: body.payment.uid, amount: body.payment.amount,
        from: opts.from, to: opts.to, network: opts.network, direction: opts.direction,
        memo: body.payment.memo,
      });
      if (opts.mutate) opts.mutate(p);
      payments.set(id, p);
      return reply(200, p);
    }
    const done = u.pathname.match(/^\/v2\/payments\/([^/]+)\/complete$/);
    if (done) {
      const id = decodeURIComponent(done[1]);
      const p = payments.get(id);
      if (!p) return reply(404, { error: 'missing' });
      if (opts.failComplete) return reply(400, { error: 'no' });
      p.status.developer_completed = true;
      p.transaction = { txid: body.txid };
      return reply(200, p);
    }
    const one = u.pathname.match(/^\/v2\/payments\/([^/]+)$/);
    if (one && init.method === 'GET') {
      const p = payments.get(decodeURIComponent(one[1]));
      return p ? reply(200, p) : reply(404, { error: 'missing' });
    }
    return reply(500, { error: 'unexpected' });
  };
  return { fetch: fetchImpl, calls, payments };
}

async function signin(uid) {
  await a2u.recordSignin(uid);
}

function use(kp, pi, hz, extraEnv = {}) {
  return {
    env: envOf(kp, extraEnv),
    fetch: pi.fetch,
    apiBase: 'https://pi.test',
    horizon: hz,
    horizonUrl: a2u.TESTNET_HORIZON,
  };
}

test('the flag is off unless it is exactly 1, and the seed is not read while it is off', async () => {
  const kp = Keypair.random();
  let seedReads = 0;
  const env = {
    PI_TESTNET_A2U_ENABLED: '0',
    get PI_TESTNET_APP_WALLET_SEED() { seedReads += 1; return kp.secret(); },
    get PI_TESTNET_API_KEY() { throw new Error('key read while off'); },
  };
  for (const flag of ['', '0', 'true', 'yes', 'on', '01']) {
    env.PI_TESTNET_A2U_ENABLED = flag;
    await assert.rejects(() => a2u.pay('uid-off', { env }), (err) => err.code === 'off');
  }
  assert.strictEqual(seedReads, 0);
  assert.strictEqual(a2u.enabled({ PI_TESTNET_A2U_ENABLED: ' 1 ' }), true);
});

test('the library never names the Mainnet server key', () => {
  const src = codeOnly(LIB);
  assert.doesNotMatch(src, /PI_NETWORK_API_KEY/);
  assert.match(src, /const TESTNET_PASSPHRASE = 'Pi Testnet'/);
  assert.match(src, /networkPassphrase:\s*TESTNET_PASSPHRASE/);
  assert.doesNotMatch(src, /networkPassphrase:\s*payment\.network/);
  assert.doesNotMatch(src, /networkPassphrase:\s*['"]Pi Network['"]/);
  assert.match(src, /https:\/\/api\.testnet\.minepi\.com/);
});

test('a configured Mainnet wallet, a Mainnet horizon and a Mainnet network are each refused', async () => {
  const kp = Keypair.random();
  const to = Keypair.random().publicKey();
  const uid = `uid-refuse-${Date.now()}`;
  await signin(uid);
  const hz = horizon();
  const pi = fakePi({ from: kp.publicKey(), to });

  await assert.rejects(
    () => a2u.pay(uid, use(kp, pi, hz, { PI_MAINNET_APP_WALLET: kp.publicKey() })),
    (err) => err.code === 'mainnet_wallet');
  assert.strictEqual(pi.calls.length, 0, 'a Mainnet wallet was asked of Pi');
  assert.strictEqual(hz.submitted.length, 0);

  const piHost = fakePi({ from: kp.publicKey(), to });
  await assert.rejects(
    () => a2u.pay(uid, { ...use(kp, piHost, hz), horizonUrl: 'https://api.mainnet.minepi.com' }),
    (err) => err.code === 'mainnet_horizon');
  assert.strictEqual(piHost.calls.length, 0);
  assert.strictEqual(hz.submitted.length, 0);

  await assert.rejects(
    () => a2u.pay(uid, { ...use(kp, piHost, hz), apiBase: 'https://api.mainnet.minepi.com' }),
    (err) => err.code === 'mainnet_horizon');
  assert.strictEqual(piHost.calls.length, 0);

  const piNet = fakePi({ from: kp.publicKey(), to, network: 'Pi Network' });
  const logs = capture();
  try {
    await assert.rejects(
      () => a2u.pay(uid, use(kp, piNet, hz)),
      (err) => err.code === 'mainnet_network' && !err.message.includes(kp.secret()));
  } finally {
    logs.restore();
  }
  assert.strictEqual(hz.submitted.length, 0, 'a Mainnet payment was signed');
  assert.ok(piNet.calls.some((c) => c.method === 'POST' && c.path === '/v2/payments'));
  assert.ok(!logs.lines.join('\n').includes(kp.secret()));
  assert.ok(!logs.lines.join('\n').includes(API_KEY));
});

test('a payout signs Pi Testnet, pays one signed-in uid 0.01, and repeats nothing', async () => {
  const kp = Keypair.random();
  const to = Keypair.random().publicKey();
  const uid = `uid-ok-${Date.now()}`;
  await signin(uid);
  const hz = horizon();
  const pi = fakePi({ from: kp.publicKey(), to });
  const logs = capture();
  let result;
  try {
    result = await a2u.pay(uid, use(kp, pi, hz));
  } finally {
    logs.restore();
  }
  assert.strictEqual(result.status, 'completed');
  assert.strictEqual(result.uid, uid);
  assert.strictEqual(result.amount, '0.01');
  assert.strictEqual(hz.submitted.length, 1);
  assert.strictEqual(hz.submitted[0].networkPassphrase, 'Pi Testnet');
  assert.strictEqual(hz.submitted[0].fee, a2u.FEE);
  const ops = hz.submitted[0].operations;
  assert.strictEqual(ops[0].destination, to);
  assert.strictEqual(Number(ops[0].amount), 0.01);
  const created = pi.calls.find((c) => c.method === 'POST' && c.path === '/v2/payments');
  assert.deepStrictEqual(created.body.payment, {
    amount: 0.01, memo: a2u.MEMO, metadata: { kind: 'testnet_a2u' }, uid,
  });
  assert.ok(pi.calls.every((c) => c.auth === `Key ${API_KEY}`));
  const completed = pi.calls.find((c) => c.path.endsWith('/complete'));
  assert.strictEqual(completed.body.txid, result.txid);
  assert.ok(!JSON.stringify(result).includes(kp.secret()));
  assert.ok(!JSON.stringify(result).includes(API_KEY));
  assert.ok(!logs.lines.join('\n').includes(kp.secret()));
  assert.ok(!logs.lines.join('\n').includes(API_KEY));

  const again = fakePi({ from: kp.publicKey(), to });
  await assert.rejects(() => a2u.pay(uid, use(kp, again, horizon())), (err) => err.code === 'already_paid');
  assert.strictEqual(again.calls.length, 0, 'a second pay asked Pi');
});

test('an incomplete server payment is completed instead of creating another', async () => {
  const kp = Keypair.random();
  const to = Keypair.random().publicKey();
  const uid = `uid-inc-${Date.now()}`;
  await signin(uid);
  const txid = 'cd'.repeat(32);
  const existing = payment({
    identifier: 'incpay1', uid, from: kp.publicKey(), to, txid,
  });
  const hz = horizon();
  const pi = fakePi({ from: kp.publicKey(), to, incomplete: [existing] });
  const result = await a2u.pay(uid, use(kp, pi, hz));
  assert.strictEqual(result.status, 'completed');
  assert.strictEqual(result.payment_id, 'incpay1');
  assert.strictEqual(result.txid, txid);
  assert.strictEqual(hz.submitted.length, 0, 'a payment that already had a txid was signed again');
  assert.ok(!pi.calls.some((c) => c.method === 'POST' && c.path === '/v2/payments'));
  assert.ok(pi.calls.some((c) => c.path === '/v2/payments/incpay1/complete' && c.body.txid === txid));
});

test('a uid that has not signed in to the Testnet app is not paid', async () => {
  const kp = Keypair.random();
  const pi = fakePi({ from: kp.publicKey(), to: Keypair.random().publicKey() });
  await assert.rejects(
    () => a2u.pay(`uid-new-${Date.now()}`, use(kp, pi, horizon())),
    (err) => err.code === 'not_signed_in');
  assert.strictEqual(pi.calls.length, 0);
});

test('the amount defaults to 0.01, allows 0.1, and refuses more', async () => {
  const kp = Keypair.random();
  assert.strictEqual(a2u.amountOf({}).amount, '0.01');
  assert.strictEqual(a2u.amountOf({ PI_TESTNET_A2U_AMOUNT: '0.1' }).ok, true);
  assert.strictEqual(a2u.amountOf({ PI_TESTNET_A2U_AMOUNT: '0.2' }).ok, false);
  assert.strictEqual(a2u.amountOf({ PI_TESTNET_A2U_AMOUNT: '1' }).ok, false);
  const uid = `uid-amt-${Date.now()}`;
  await signin(uid);
  const pi = fakePi({ from: kp.publicKey(), to: Keypair.random().publicKey() });
  await assert.rejects(
    () => a2u.pay(uid, use(kp, pi, horizon(), { PI_TESTNET_A2U_AMOUNT: '0.2' })),
    (err) => err.code === 'bad_amount');
  assert.strictEqual(pi.calls.length, 0);
});

test('a long Pi payment id is still signed, with a memo that fits', async () => {
  const kp = Keypair.random();
  const to = Keypair.random().publicKey();
  const uid = `uid-memo-${Date.now()}`;
  await signin(uid);
  const id = 'abcdef01-2345-6789-abcd-ef0123456789';
  const hz = horizon();
  const pi = fakePi({ from: kp.publicKey(), to, identifier: id });
  const result = await a2u.pay(uid, use(kp, pi, hz));
  assert.strictEqual(result.payment_id, id);
  assert.strictEqual(hz.submitted.length, 1);
  const text = Buffer.from(hz.submitted[0].memo.value).toString();
  assert.ok(Buffer.byteLength(text) <= 28, text);
  assert.ok(!text.includes(id));
});

test('at most ten payments are created', async () => {
  const kp = Keypair.random();
  const fillers = [];
  for (let i = 0; i < a2u.MAX_PAYMENTS; i += 1) {
    const uid = `uid-fill-${i}-${Date.now()}`;
    fillers.push(uid);
    pool.piTestnetA2u.push({
      uid, amount: '0.01', payment_id: `fill${i}${Date.now()}`, txid: null,
      status: 'completed', created_at: new Date(), updated_at: new Date(),
    });
  }
  const uid = `uid-eleventh-${Date.now()}`;
  await signin(uid);
  const pi = fakePi({ from: kp.publicKey(), to: Keypair.random().publicKey() });
  try {
    await assert.rejects(() => a2u.pay(uid, use(kp, pi, horizon())), (err) => err.code === 'cap');
    assert.ok(!pi.calls.some((c) => c.method === 'POST'), 'the 11th payment was created');
  } finally {
    pool.piTestnetA2u = pool.piTestnetA2u.filter((r) => !fillers.includes(r.uid));
  }
});

test('a horizon failure does not put the seed or the key in the error or the log', async () => {
  const kp = Keypair.random();
  const secret = kp.secret();
  const uid = `uid-log-${Date.now()}`;
  await signin(uid);
  const pi = fakePi({ from: kp.publicKey(), to: Keypair.random().publicKey() });
  const hz = {
    loadAccount: async () => { throw new Error(secret + API_KEY); },
    submitTransaction: async () => { throw new Error('should not submit'); },
  };
  const logs = capture();
  let caught;
  try {
    await a2u.pay(uid, use(kp, pi, hz));
  } catch (err) {
    caught = err;
  } finally {
    logs.restore();
  }
  assert.strictEqual(caught.code, 'horizon_unreadable');
  const text = `${caught.message}\n${caught.stack}\n${logs.lines.join('\n')}`;
  assert.ok(!text.includes(secret));
  assert.ok(!text.includes(API_KEY));
});

test('the route is operator-only and its responses do not carry the seed', async () => {
  const kp = Keypair.random();
  const secret = kp.secret();
  const router = require('../routes/pi_testnet_a2u');
  const to = Keypair.random().publicKey();
  const pi = fakePi({ from: kp.publicKey(), to });
  router._deps = {
    env: envOf(kp), fetch: pi.fetch, apiBase: 'https://pi.test',
    horizon: horizon(), horizonUrl: a2u.TESTNET_HORIZON,
  };
  const app = express();
  app.use(express.json());
  app.use('/api/pi/testnet-a2u', router);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}/api/pi/testnet-a2u`;
  const stranger = await auth.findOrCreateOAuthUser({ provider: 'pi', providerId: `stranger-${Date.now()}` });
  const operator = await auth.findOrCreateOAuthUser({ provider: 'pi', providerId: `operator-${Date.now()}` });
  pool.users.find((u) => u.id === operator.id).plan = 'admin';
  const call = async (who, path, body) => {
    const response = await fetch(`${base}${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: {
        'content-type': 'application/json',
        ...(who ? { authorization: `Bearer ${auth.signToken(who)}` } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    return { status: response.status, text, body: text ? JSON.parse(text) : null };
  };
  const logs = capture();
  try {
    assert.strictEqual((await call(null, '/', undefined)).status, 401);
    assert.strictEqual((await call(stranger, '/pay', { uid: 'x' })).status, 403);
    const uid = `uid-route-${Date.now()}`;
    await signin(uid);
    const paid = await call(operator, '/pay', { uid });
    assert.strictEqual(paid.status, 200, paid.text);
    assert.strictEqual(paid.body.status, 'completed');
    assert.ok(!paid.text.includes(secret) && !paid.text.includes(API_KEY));
    const listed = await call(operator, '/signins', undefined);
    assert.strictEqual(listed.status, 200);
    assert.ok(listed.body.uids.some((r) => r.uid === uid));
    assert.ok(!listed.text.includes(secret));
    const status = await call(operator, '/', undefined);
    assert.strictEqual(status.body.enabled, true);
    assert.strictEqual(status.body.network, 'Pi Testnet');
    assert.ok(status.body.paid >= 1);
  } finally {
    logs.restore();
    server.close();
    router._deps = {};
    assert.ok(!logs.lines.join('\n').includes(secret));
    assert.ok(!logs.lines.join('\n').includes(API_KEY));
  }
});

test('a Testnet sign-in records the uid, and a www sign-in does not', async () => {
  const router = require('../routes/pi_auth');
  const secret = 's'.repeat(32);
  const uid = `uid-signin-${Date.now()}`;
  const calls = [];
  router._deps = {
    apiBase: 'https://api.minepi.test/v2',
    env: { PI_TESTNET_PROXY_SECRET: secret },
    fetch: async (url, init) => {
      calls.push({ url, auth: init.headers.Authorization });
      return {
        ok: true, status: 200,
        json: async () => ({ uid, username: 'pioneer' }),
      };
    },
  };
  const app = express();
  app.use(express.json());
  app.use('/api/pi', router);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const post = (header) => fetch(`http://127.0.0.1:${server.address().port}/api/pi/signin`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...(header || {}) },
    body: JSON.stringify({ accessToken: 'piAccessToken_abcdefghijklmnopqrstuvwxyz0123456789' }),
  });
  try {
    const on = await post({ 'x-runeclaw-pi-proxy': secret });
    assert.strictEqual(on.status, 200);
    assert.ok(pool.piTestnetSignins.some((r) => r.uid === uid));
    const other = `uid-www-${Date.now()}`;
    router._deps.fetch = async () => ({
      ok: true, status: 200, json: async () => ({ uid: other, username: 'other' }),
    });
    const off = await post({});
    assert.strictEqual(off.status, 200);
    assert.ok(!pool.piTestnetSignins.some((r) => r.uid === other));
  } finally {
    server.close();
    router._deps = {};
  }
});

test('the command prints codes and never the seed', () => {
  const secret = Keypair.random().secret();
  const src = codeOnly(SCRIPT);
  assert.match(src, /listSignins\(/);
  assert.match(src, /\.pay\(/);
  assert.doesNotMatch(src, /PI_TESTNET_APP_WALLET_SEED|PI_TESTNET_API_KEY|PI_NETWORK_API_KEY/);
  assert.doesNotMatch(src, /process\.env/);
  const run = spawnSync(process.execPath, [
    path.join(__dirname, '..', '..', 'scripts', 'pi_testnet_a2u.js'), 'list',
  ], {
    env: {
      ...process.env,
      DATABASE_URL: '',
      PI_TESTNET_A2U_ENABLED: '1',
      PI_TESTNET_APP_WALLET_SEED: secret,
      PI_TESTNET_API_KEY: API_KEY,
    },
    encoding: 'utf8',
  });
  const out = `${run.stdout}\n${run.stderr}`;
  assert.notStrictEqual(run.status, 0);
  assert.match(out, /no_database/);
  assert.ok(!out.includes(secret));
  assert.ok(!out.includes(API_KEY));
  const routeSrc = codeOnly(ROUTE);
  assert.match(routeSrc, /authMiddleware/);
  assert.match(routeSrc, /isOperator/);
});
