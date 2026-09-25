/**
 * The 2FA step-up on a trade confirm skips the code only on the probe's own
 * explicit word that the account is paper.
 *
 * `POST /api/trade/confirm` decides whether to demand an authenticator code by
 * asking the bot (`GET /gateway/trade/live_mode`) whether this identity is
 * live-capable. The comment above that read promised that "a gateway hiccup
 * requires the code", and the expression under it was
 * `lm.status === 200 && lm.data && lm.data.live_allowed` read as the condition
 * for LIVE — so every hiccup that ANSWERED came out not live-capable. Driven
 * against the unfixed route, with 2FA enrolled and no code sent: a 429, a 503,
 * a 500 and a 200 with no `live_allowed` field were each forwarded to the
 * confirm with no code examined. Only a thrown fetch kept the promise.
 *
 * The one answer that may skip the step-up is a 200 carrying
 * `live_allowed: false`. Every other outcome — a non-200, a thrown fetch, a 200
 * with no field, a 200 whose field is not the boolean false — requires the
 * code, and the confirm is never forwarded without one. Both arms are driven,
 * because a refusal-only table passes just as happily against a route that
 * demands a code from everyone.
 */

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(40);

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const jwt = require('jsonwebtoken');
const express = require('express');
const { pool } = require(path.join(ROOT, 'db'));
const gateway = require(path.join(ROOT, 'lib', 'gateway'));
const totp = require(path.join(ROOT, 'lib', 'totp'));

const calls = [];
let probe = null;   // (path) => {status, data} | throws

const realGet = gateway.getGateway;
const realPost = gateway.postGateway;

let server;
let base;
let seq = 0;

// One ACCOUNT per case: the confirm route's limiter allows ten a minute per
// user, and a table of fourteen drives against one account would measure the
// limiter from the eleventh on.
async function account({ enrolled = true } = {}) {
  seq += 1;
  const email = `stepup-probe-${seq}@test.io`;
  await pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    [email, 'x', 'Probe']);
  const [rows] = await pool.execute('SELECT id FROM users WHERE email = ?', [email]);
  const uid = rows[0].id;
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', [String(424200 + seq), uid]);
  const [u] = await pool.execute('SELECT * FROM users WHERE id = ?', [uid]);
  const row = u[0];
  row.telegram_linked = true;
  // MemoryDB has no TOTP DDL path; enrol on the row object, as
  // gateway_routes.test.js does.
  row.totp_enabled = enrolled ? 1 : 0;
  row.totp_secret = enrolled ? totp.generateSecret() : null;
  return { row, token: jwt.sign({ user_id: uid, email }, process.env.JWT_SECRET) };
}

test.before(async () => {
  gateway.getGateway = async (p) => {
    calls.push(['GET', p]);
    return probe(p);
  };
  gateway.postGateway = async (p, b) => {
    calls.push(['POST', p, b]);
    return { status: 200, data: { result_html: 'placed', placed: true } };
  };
  const app = express();
  app.use(express.json());
  app.use('/api/trade', require(path.join(ROOT, 'routes', 'webtrade')));
  server = http.createServer(app);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(async () => {
  gateway.getGateway = realGet;
  gateway.postGateway = realPost;
  await new Promise((r) => server.close(r));
});

function confirm(token, body) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const req = http.request(`${base}/api/trade/confirm`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    req.on('error', reject);
    req.write(payload);
    req.end();
  });
}

const forwarded = () => calls.some((c) => c[0] === 'POST' && c[1] === '/trade/confirm');
const probed = () => calls.some((c) => c[0] === 'GET' && String(c[1]).startsWith('/trade/live_mode'));

// Every answer that is NOT the explicit paper word. Each requires the code.
const REQUIRES_CODE = [
  ['a 200 saying live_allowed: true', () => ({ status: 200, data: { mode: 'LIVE', live_allowed: true } })],
  ['a 503', () => ({ status: 503, data: { error: 'unavailable' } })],
  ['a 500 with a JSON body', () => ({ status: 500, data: { error: 'boom' } })],
  ["the gateway's own 429", () => ({ status: 429, data: { error: 'rate_limited' } })],
  ['a 403', () => ({ status: 403, data: { error: 'not_authorized' } })],
  ['a 200 with no live_allowed field', () => ({ status: 200, data: { mode: 'PAPER' } })],
  ['a 200 whose live_allowed is the STRING "false"', () => ({ status: 200, data: { live_allowed: 'false' } })],
  ['a 200 whose live_allowed is 0', () => ({ status: 200, data: { live_allowed: 0 } })],
  ['a 200 with no body', () => ({ status: 200, data: null })],
  ['no answer object at all', () => undefined],
  ['a thrown fetch', () => { throw new Error('ECONNRESET'); }],
];

for (const [name, answer] of REQUIRES_CODE) {
  test(`${name} from the live-mode probe requires the code, and nothing is forwarded`, async () => {
    probe = answer;
    calls.length = 0;
    const { token } = await account();
    const r = await confirm(token, { trade_id: 'TI-probe0001' });
    assert.ok(probed(), 'the probe was asked');
    assert.equal(r.status, 401);
    assert.equal(r.data.error, 'two_factor_required');
    assert.equal(forwarded(), false, 'a live confirm went out with no code examined');
  });
}

test('a 200 saying live_allowed: false is the one answer that skips the step-up', async () => {
  probe = () => ({ status: 200, data: { mode: 'PAPER', live_allowed: false } });
  calls.length = 0;
  const { token } = await account();
  const r = await confirm(token, { trade_id: 'TI-probe0002' });   // no totp_code
  assert.equal(r.status, 200);
  assert.ok(forwarded(), 'a paper confirm must stay one-tap');
});

test('a valid code clears the step-up on an answer that could not be read', async () => {
  probe = () => ({ status: 503, data: { error: 'unavailable' } });
  calls.length = 0;
  const { token, row } = await account();
  const code = totp.hotp(row.totp_secret, Math.floor(Date.now() / 30000));
  const r = await confirm(token, { trade_id: 'TI-probe0003', totp_code: code });
  assert.equal(r.status, 200);
  assert.ok(forwarded(), 'the code is the remedy, and it must work');
});

test('an account with no 2FA enrolled is never asked, whatever the probe would say', async () => {
  const { token } = await account({ enrolled: false });
  probe = () => ({ status: 503, data: {} });
  calls.length = 0;
  const r = await confirm(token, { trade_id: 'TI-probe0004' });
  assert.equal(r.status, 200);
  assert.equal(probed(), false, 'nothing to gate, so nothing to probe');
  assert.ok(forwarded());
});
