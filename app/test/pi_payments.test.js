'use strict';
/**
 * Pi tips, user to app: the Platform client and the routes, over every answer.
 *
 * The Pi Platform is stood in for by a stateful fake that holds payments the
 * way Pi does (who, how much, which order, approved, transaction, completed)
 * and records every call, so each test checks what this server would have
 * asked Pi to do as well as what it answered the page. The routes run over
 * HTTP on 127.0.0.1 against the real router, the real auth middleware and the
 * MemoryDB.
 *
 * What is held: the page names only payment ids; a payment that is not this
 * person's, not this amount or not this order is refused BEFORE Pi is asked
 * to approve anything; each step is safe to repeat (Pi retries); a payment Pi
 * could not complete is never recorded as received; and the server key never
 * reaches a response or a log line.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

const pay = require('../lib/pi_payments');
const auth = require('../auth');
const { pool } = require('../db');

const KEY = 'server-key-never-to-be-seen-0123456789';
const BASE = 'https://pi.test';

/** Pi's Platform API, as a stateful fake that records what it was asked. */
function fakePi() {
  const payments = new Map();
  const calls = [];
  const fail = {};   // { get, approve, complete } -> status to answer instead
  async function fetchImpl(url, opts = {}) {
    const u = new URL(url);
    const m = u.pathname.match(/^\/v2\/payments\/([^/]+)(?:\/(approve|complete))?$/);
    const op = m && m[2] ? m[2] : 'get';
    calls.push({ op, id: m && decodeURIComponent(m[1]), method: opts.method,
      auth: opts.headers && opts.headers.authorization,
      body: opts.body ? JSON.parse(opts.body) : null });
    const reply = (status, body) => ({
      status, ok: status >= 200 && status < 300,
      json: async () => body,
    });
    if (fail[op]) return reply(fail[op], { error: 'nope' });
    const p = m && payments.get(decodeURIComponent(m[1]));
    if (!p) return reply(404, { error: 'payment_not_found' });
    if (op === 'approve') p.status.developer_approved = true;
    if (op === 'complete') {
      if (!p.transaction || JSON.parse(opts.body).txid !== p.transaction.txid) return reply(400, { error: 'bad txid' });
      p.status.developer_completed = true;
    }
    return reply(200, structuredClone(p));
  }
  /** What Pi.createPayment would create on Pi's side, from the page's data. */
  function create(id, { uid, amount, metadata, direction = 'user_to_app' }) {
    payments.set(id, {
      identifier: id, user_uid: uid, amount, memo: pay.TIP_MEMO, metadata,
      direction, network: 'Pi Network', from_address: 'G-user', to_address: 'G-app',
      created_at: new Date().toISOString(),
      status: { developer_approved: false, transaction_verified: false,
        developer_completed: false, cancelled: false, user_cancelled: false },
      transaction: null,
    });
    return payments.get(id);
  }
  function sign(id, txid) {
    const p = payments.get(id);
    p.transaction = { txid, verified: true, _link: `https://chain.test/${txid}` };
    p.status.transaction_verified = true;
  }
  return { fetch: fetchImpl, payments, calls, fail, create, sign };
}

let piUser = 0;
async function piAccount() {
  piUser += 1;
  const uid = `pi-uid-tip-${piUser}-${Date.now()}`;
  const user = await auth.findOrCreateOAuthUser({ provider: 'pi', providerId: uid });
  return { uid, id: user.id, token: auth.signToken(user) };
}

async function webAccount() {
  const email = `tipless-${Date.now()}-${Math.random()}@example.com`;
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)', [email, 'x']);
  const [rows] = await pool.execute('SELECT id, email, plan, token_epoch FROM users WHERE email = ?', [email]);
  return { id: rows[0].id, token: auth.signToken(rows[0]) };
}

async function serve({ key = KEY, env = {}, omitKey = false } = {}) {
  const router = require('../routes/pi_payments');
  const pi = fakePi();
  const injected = { fetch: pi.fetch, apiBase: BASE, env };
  if (!omitKey) injected.apiKey = key;
  router._deps = injected;
  const app = express();
  app.use(express.json());
  app.use('/api/pi/payments', router);
  const server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}/api/pi/payments`;
  const call = (who, path, body) => fetch(`${base}${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'content-type': 'application/json', ...(who ? { authorization: `Bearer ${who.token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then(async (r) => ({ status: r.status, body: await r.json().catch(() => null) }));
  return { server, pi, call };
}

async function row(orderId) {
  return pool.piPayments.find((r) => r.id === orderId);
}

/** Order a tip, have "Pi" create the payment from the order, return both. */
async function ordered(s, who, amount = 3, paymentId = `pay-${Math.random().toString(36).slice(2)}`) {
  const o = await s.call(who, '/tips', { amount });
  assert.strictEqual(o.status, 200, JSON.stringify(o.body));
  s.pi.create(paymentId, { uid: who.uid, amount: o.body.amount, metadata: o.body.metadata });
  return { order: o.body, paymentId };
}

// ── the Platform client ────────────────────────────────────────────────────

test('the client asks Pi the documented way, with the key only in its header', async () => {
  const pi = fakePi();
  pi.create('p1', { uid: 'u', amount: 1, metadata: {} });
  const deps = { fetch: pi.fetch, apiKey: KEY, apiBase: BASE };
  assert.strictEqual((await pay.getPayment('p1', deps)).identifier, 'p1');
  await pay.approvePayment('p1', deps);
  pi.sign('p1', 'tx1');
  await pay.completePayment('p1', 'tx1', deps);
  assert.deepStrictEqual(pi.calls.map((c) => [c.method, c.op, c.id]),
    [['GET', 'get', 'p1'], ['POST', 'approve', 'p1'], ['POST', 'complete', 'p1']]);
  assert.ok(pi.calls.every((c) => c.auth === `Key ${KEY}`));
  assert.deepStrictEqual(pi.calls[2].body, { txid: 'tx1' });
  assert.strictEqual(pay.API_BASE, 'https://api.minepi.com');
});

test('the client\'s three answers: a payment, null for unknown, a throw for everything else', async () => {
  const pi = fakePi();
  const deps = { fetch: pi.fetch, apiKey: KEY, apiBase: BASE };
  assert.strictEqual(await pay.getPayment('nope', deps), null);
  pi.fail.get = 500;
  await assert.rejects(() => pay.getPayment('nope', deps), /answered 500/);
  pi.fail.get = 401;
  await assert.rejects(() => pay.getPayment('nope', deps), /answered 401/, 'our key refused is ours, not unknown');
  await assert.rejects(() => pay.getPayment('p', { ...deps, apiKey: null }), pay.PaymentsNotConfigured);
  const liar = { fetch: async () => ({ status: 200, ok: true, json: async () => ({ identifier: 'other' }) }), apiKey: KEY };
  await assert.rejects(() => pay.getPayment('mine', liar), /different payment/);
});

test('the testnet host uses its own API key, and falls back when that key is unset', async () => {
  const env = {
    PI_TESTNET_HOST: 'pi.humanoid-traders.com',
    PI_TESTNET_API_KEY: '  testnet-api-dummy  ',
    PI_NETWORK_API_KEY: 'main-api-dummy',
  };
  const on = (host) => ({ hostname: host });
  assert.strictEqual(pay.apiKey(env, on('PI.HUMANOID-TRADERS.COM')), 'testnet-api-dummy');
  assert.strictEqual(pay.apiKey(env, on('www.humanoid-traders.com')), 'main-api-dummy');
  assert.strictEqual(pay.apiKey({ ...env, PI_TESTNET_API_KEY: '' }, on('pi.humanoid-traders.com')), 'main-api-dummy');
  assert.strictEqual(pay.apiKey({ PI_NETWORK_API_KEY: 'main-api-dummy', PI_TESTNET_API_KEY: 'testnet-api-dummy' }, on('pi.humanoid-traders.com')), 'main-api-dummy');
  assert.strictEqual(pay.apiKey({}), null);
  assert.strictEqual(pay.configured(env, on('pi.humanoid-traders.com')), true);

  // The route, with no injected key: approval sends the key for the Host.
  const s = await serve({ omitKey: true, env });
  try {
    const me = await piAccount();
    const port = s.server.address().port;
    async function post(host, path, body) {
      const r = await new Promise((resolve, reject) => {
        const req = http.request({
          host: '127.0.0.1', port, path: `/api/pi/payments${path}`, method: 'POST',
          headers: {
            host, 'content-type': 'application/json', authorization: `Bearer ${me.token}`,
          },
        }, (res) => {
          const chunks = [];
          res.on('data', (c) => chunks.push(c));
          res.on('end', () => resolve({ status: res.statusCode, body: JSON.parse(Buffer.concat(chunks).toString('utf8')) }));
        });
        req.on('error', reject);
        req.end(JSON.stringify(body));
      });
      return r;
    }
    const orderedOn = async (host) => {
      const o = await post(host, '/tips', { amount: 1 });
      assert.strictEqual(o.status, 200, JSON.stringify(o.body));
      const paymentId = `pay-${Math.random().toString(36).slice(2)}`;
      s.pi.create(paymentId, { uid: me.uid, amount: o.body.amount, metadata: o.body.metadata });
      return paymentId;
    };
    const testnetId = await orderedOn('pi.humanoid-traders.com:443');
    const testnetApproved = await post('pi.humanoid-traders.com:443', '/approve', { paymentId: testnetId });
    assert.strictEqual(testnetApproved.status, 200, JSON.stringify(testnetApproved.body));
    const wwwId = await orderedOn('www.humanoid-traders.com');
    const wwwApproved = await post('www.humanoid-traders.com', '/approve', { paymentId: wwwId });
    assert.strictEqual(wwwApproved.status, 200, JSON.stringify(wwwApproved.body));
    const auths = s.pi.calls.filter((c) => c.op === 'approve').map((c) => c.auth);
    assert.deepStrictEqual(auths, ['Key testnet-api-dummy', 'Key main-api-dummy']);
  } finally { s.server.close(); }
});

test('tip amounts are the server\'s, and a bad setting falls back rather than offering nothing', () => {
  assert.deepStrictEqual(pay.tipAmounts({}), [1, 3, 5]);
  assert.deepStrictEqual(pay.tipAmounts({ PI_TIP_AMOUNTS: '2, 0.5, abc, 1000, -1, 2' }), [0.5, 2]);
  assert.deepStrictEqual(pay.tipAmounts({ PI_TIP_AMOUNTS: 'abc,0' }), [1, 3, 5]);
});

test('a payment matches its order only on every count', () => {
  const order = { id: 'o1', amount: '3.0000000', kind: 'tip' };
  const good = { direction: 'user_to_app', user_uid: 'u1', amount: 3,
    metadata: { orderId: 'o1', kind: 'tip' }, status: {} };
  assert.deepStrictEqual(pay.mismatches(good, order, 'u1'), []);
  assert.deepStrictEqual(pay.mismatches({ ...good, direction: 'app_to_user' }, order, 'u1'), ['direction']);
  assert.deepStrictEqual(pay.mismatches(good, order, 'u2'), ['user']);
  assert.deepStrictEqual(pay.mismatches({ ...good, amount: 3.5 }, order, 'u1'), ['amount']);
  assert.deepStrictEqual(pay.mismatches({ ...good, metadata: { orderId: 'o2', kind: 'tip' } }, order, 'u1'), ['order']);
  assert.deepStrictEqual(pay.mismatches({ ...good, metadata: { orderId: 'o1', kind: 'pro' } }, order, 'u1'), ['kind']);
  assert.deepStrictEqual(pay.mismatches({ ...good, status: { user_cancelled: true } }, order, 'u1'), ['cancelled']);
  assert.deepStrictEqual(pay.mismatches(good, null, 'u1'), ['order']);
});

// ── the routes ─────────────────────────────────────────────────────────────

test('a tip runs end to end: ordered, approved, completed, once', async () => {
  const s = await serve();
  try {
    const me = await piAccount();
    const offer = await s.call(me, '/tips');
    assert.deepStrictEqual(offer.body, { enabled: true, amounts: [1, 3, 5], memo: 'Support RUNECLAW' });

    const { order, paymentId } = await ordered(s, me, 3);
    assert.strictEqual(order.amount, 3);
    assert.deepStrictEqual(order.metadata, { orderId: order.orderId, kind: 'tip' });
    assert.strictEqual((await row(order.orderId)).status, 'created');

    const a = await s.call(me, '/approve', { paymentId });
    assert.deepStrictEqual(a.body, { ok: true, status: 'approved' });
    assert.strictEqual((await row(order.orderId)).payment_id, paymentId, 'the payment id is kept');

    s.pi.sign(paymentId, 'txabc');
    const c = await s.call(me, '/complete', { paymentId, txid: 'txabc' });
    assert.deepStrictEqual(c.body, { ok: true, status: 'completed' });
    const r = await row(order.orderId);
    assert.strictEqual(r.status, 'completed');
    assert.strictEqual(r.txid, 'txabc');

    // Pi retries both. Repeats answer the same and ask Pi for nothing new.
    const before = s.pi.calls.filter((x) => x.op !== 'get').length;
    assert.strictEqual((await s.call(me, '/approve', { paymentId })).status, 200);
    assert.strictEqual((await s.call(me, '/complete', { paymentId, txid: 'txabc' })).status, 200);
    assert.strictEqual(s.pi.calls.filter((x) => x.op !== 'get').length, before,
      'a repeated step asked Pi to approve or complete again');
    assert.deepStrictEqual(s.pi.calls.filter((x) => x.op !== 'get').map((x) => x.op), ['approve', 'complete']);
  } finally { s.server.close(); }
});

test('someone else\'s payment is refused before Pi is asked to approve anything', async () => {
  const s = await serve();
  try {
    const me = await piAccount();
    const them = await piAccount();
    const { paymentId } = await ordered(s, them, 1);
    const r = await s.call(me, '/approve', { paymentId });
    assert.strictEqual(r.status, 403);
    assert.strictEqual(r.body.error, 'not_your_payment');
    assert.ok(!s.pi.calls.some((c) => c.op === 'approve'));
  } finally { s.server.close(); }
});

test('a payment for another amount, or naming another person\'s order, is not approved', async () => {
  const s = await serve();
  try {
    const me = await piAccount();
    const o = await s.call(me, '/tips', { amount: 3 });
    s.pi.create('pay-cheap', { uid: me.uid, amount: 1, metadata: o.body.metadata });
    const cheap = await s.call(me, '/approve', { paymentId: 'pay-cheap' });
    assert.strictEqual(cheap.status, 409);
    assert.deepStrictEqual(cheap.body.reasons, ['amount']);

    // My uid, but metadata naming THEIR order: the order lookup is by owner.
    const them = await piAccount();
    const theirs = await s.call(them, '/tips', { amount: 5 });
    s.pi.create('pay-borrowed', { uid: me.uid, amount: 5, metadata: theirs.body.metadata });
    const borrowed = await s.call(me, '/approve', { paymentId: 'pay-borrowed' });
    assert.strictEqual(borrowed.status, 409);
    assert.deepStrictEqual(borrowed.body.reasons, ['order']);
    assert.ok(!s.pi.calls.some((c) => c.op === 'approve'));
  } finally { s.server.close(); }
});

test('one order is settled by one payment, and one payment settles one order', async () => {
  const s = await serve();
  try {
    const me = await piAccount();
    const { order, paymentId } = await ordered(s, me, 1, 'pay-first');
    assert.strictEqual((await s.call(me, '/approve', { paymentId })).status, 200);
    s.pi.create('pay-second', { uid: me.uid, amount: 1, metadata: order.metadata });
    const again = await s.call(me, '/approve', { paymentId: 'pay-second' });
    assert.strictEqual(again.status, 409);
    assert.strictEqual(again.body.error, 'order_already_paid');
    assert.strictEqual((await row(order.orderId)).payment_id, 'pay-first');
  } finally { s.server.close(); }
});

test('completion needs Pi\'s own txid, and a payment Pi did not complete is never recorded', async () => {
  const s = await serve();
  try {
    const me = await piAccount();
    const { order, paymentId } = await ordered(s, me, 5);
    await s.call(me, '/approve', { paymentId });
    s.pi.sign(paymentId, 'realtx');
    const forged = await s.call(me, '/complete', { paymentId, txid: 'forgedtx' });
    assert.strictEqual(forged.status, 409);
    assert.strictEqual(forged.body.error, 'txid_mismatch');
    assert.ok(!s.pi.calls.some((c) => c.op === 'complete'));

    s.pi.fail.complete = 500;
    const down = await s.call(me, '/complete', { paymentId, txid: 'realtx' });
    assert.strictEqual(down.status, 503);
    assert.strictEqual(down.body.error, 'payments_unavailable');
    assert.notStrictEqual((await row(order.orderId)).status, 'completed',
      'a payment Pi did not complete was recorded as received');

    delete s.pi.fail.complete;
    assert.strictEqual((await s.call(me, '/complete', { paymentId, txid: 'realtx' })).status, 200);
    assert.strictEqual((await row(order.orderId)).status, 'completed');
  } finally { s.server.close(); }
});

test('Pi unreachable at approval is ours, and the retry goes through', async () => {
  const s = await serve();
  try {
    const me = await piAccount();
    const { order, paymentId } = await ordered(s, me, 1);
    s.pi.fail.approve = 502;
    const down = await s.call(me, '/approve', { paymentId });
    assert.strictEqual(down.status, 503);
    assert.strictEqual((await row(order.orderId)).status, 'created');
    delete s.pi.fail.approve;
    assert.strictEqual((await s.call(me, '/approve', { paymentId })).status, 200);
    assert.strictEqual((await row(order.orderId)).status, 'approved');
  } finally { s.server.close(); }
});

test('an unfinished payment is completed from Pi\'s record, and an unsigned one is not', async () => {
  const s = await serve();
  try {
    const me = await piAccount();
    const { order, paymentId } = await ordered(s, me, 3);
    await s.call(me, '/approve', { paymentId });
    const unsigned = await s.call(me, '/incomplete', { paymentId });
    assert.strictEqual(unsigned.status, 409);
    assert.strictEqual(unsigned.body.error, 'not_submitted');

    s.pi.sign(paymentId, 'leftover');
    const done = await s.call(me, '/incomplete', { paymentId });
    assert.deepStrictEqual(done.body, { ok: true, status: 'completed' });
    assert.strictEqual((await row(order.orderId)).txid, 'leftover', 'the txid is Pi\'s, not the page\'s');
  } finally { s.server.close(); }
});

test('offers and refusals: amounts not offered, accounts without Pi, no session', async () => {
  const s = await serve();
  try {
    const me = await piAccount();
    assert.strictEqual((await s.call(me, '/tips', { amount: 2 })).body.error, 'amount_not_offered');
    assert.strictEqual((await s.call(me, '/tips', { amount: '3; DROP' })).status, 400);
    const web = await webAccount();
    const r = await s.call(web, '/tips', { amount: 1 });
    assert.strictEqual(r.status, 403);
    assert.strictEqual(r.body.error, 'pi_account_required');
    assert.strictEqual((await s.call(null, '/tips')).status, 401);
    assert.strictEqual((await s.call(me, '/approve', { paymentId: '../../etc' })).status, 400);
    assert.strictEqual((await s.call(me, '/complete', { paymentId: 'p', txid: 'not hex!' })).status, 400);
    assert.strictEqual((await s.call(me, '/approve', { paymentId: 'never-made' })).status, 404);
  } finally { s.server.close(); }
});

test('with no API key, tips are not offered, and every step says so', async () => {
  const s = await serve({ key: null });
  try {
    const me = await piAccount();
    assert.deepStrictEqual((await s.call(me, '/tips')).body, { enabled: false });
    for (const [path, body] of [['/tips', { amount: 1 }], ['/approve', { paymentId: 'p1' }]]) {
      const r = await s.call(me, path, body);
      assert.strictEqual(r.status, 503, path);
      assert.strictEqual(r.body.error, 'payments_not_configured', path);
    }
  } finally { s.server.close(); }
});

test('the server key reaches neither a response nor a log line', async () => {
  const s = await serve();
  const logged = [];
  const orig = console.error;
  console.error = (...a) => { logged.push(a.join(' ')); };
  try {
    const me = await piAccount();
    const { paymentId } = await ordered(s, me, 1);
    s.pi.fail.approve = 401;
    const r = await s.call(me, '/approve', { paymentId });
    assert.strictEqual(r.status, 503);
    assert.doesNotMatch(JSON.stringify(r.body), new RegExp(KEY));
    assert.ok(logged.length > 0, 'the failure was not logged at all');
    assert.ok(logged.every((l) => !l.includes(KEY)), 'the key was logged');
  } finally {
    console.error = orig;
    s.server.close();
  }
});
