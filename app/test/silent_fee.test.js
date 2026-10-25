'use strict';
/**
 * Silent Fee Check stays off the public site.
 *
 * The flag accepts only "1" and "true". An anonymous caller and a signed-in
 * account that is not the operator get 404, whether or not the flag is on.
 * An operator gets 404 while the flag is off. The runner is not asked in
 * any of those cases. Nothing here signs.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.SILENT_FEE_CHECK_ENABLED;
delete process.env.SILENT_FEE_EXPLORER_ENABLED;
delete process.env.SFC_ALLOW_MAINNET_READ;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const jwt = require('jsonwebtoken');

const { flag_on, FLAG } = require('../lib/silent_fee_flag');
const route = require('../routes/silent_fee');
const { pool } = require('../db');
const { codeOnly } = require('./helpers/code_only');

const APP = path.join(__dirname, '..');
const OPERATOR = 91001;
const STRANGER = 91002;
if (!pool.users.some((u) => u.id === OPERATOR)) {
  pool.users.push({ id: OPERATOR, email: 'sfc-op@example.com', plan: 'admin' });
}
if (!pool.users.some((u) => u.id === STRANGER)) {
  pool.users.push({ id: STRANGER, email: 'sfc-free@example.com', plan: 'free' });
}
const OP = jwt.sign({ user_id: OPERATOR, email: 'sfc-op@example.com' }, process.env.JWT_SECRET);
const FREE = jwt.sign({ user_id: STRANGER, email: 'sfc-free@example.com' }, process.env.JWT_SECRET);

function app() {
  const server = express();
  server.use('/api/fees', route);
  return server;
}

function listen(server) {
  return new Promise((resolve) => {
    const httpServer = server.listen(0, '127.0.0.1', () => resolve(httpServer));
  });
}

function post(httpServer, token, body, raw) {
  const data = raw || Buffer.from(JSON.stringify(body || {}));
  return new Promise((resolve, reject) => {
    const headers = {
      'Content-Type': 'application/json',
      'Content-Length': data.length,
    };
    if (token) headers.authorization = 'Bearer ' + token;
    const req = http.request({
      host: '127.0.0.1', port: httpServer.address().port, path: '/api/fees',
      method: 'POST', headers,
    }, (res) => {
      let b = '';
      res.on('data', (d) => { b += d; });
      res.on('end', () => resolve({ status: res.statusCode, body: b }));
    });
    req.on('error', reject);
    req.end(data);
  });
}

test('only 1 and true turn the flag on', () => {
  assert.equal(FLAG, 'SILENT_FEE_CHECK_ENABLED');
  assert.equal(flag_on({ SILENT_FEE_CHECK_ENABLED: '1' }), true);
  assert.equal(flag_on({ SILENT_FEE_CHECK_ENABLED: 'true' }), true);
  assert.equal(flag_on({ SILENT_FEE_CHECK_ENABLED: 'TRUE' }), true);
  assert.equal(flag_on({ SILENT_FEE_CHECK_ENABLED: 'yes' }), false);
  assert.equal(flag_on({ SILENT_FEE_CHECK_ENABLED: 'on' }), false);
  assert.equal(flag_on({ SILENT_FEE_CHECK_ENABLED: '0' }), false);
  assert.equal(flag_on({}), false);
  const src = codeOnly(fs.readFileSync(path.join(APP, 'lib', 'silent_fee_flag.js'), 'utf8'));
  assert.equal(src.includes('function enabled'), false);
});

test('the route is admin-auth and does not sign', () => {
  const src = fs.readFileSync(path.join(APP, 'routes', 'silent_fee.js'), 'utf8');
  assert.match(src, /optionalAuth/);
  assert.match(src, /isOperator/);
  assert.equal(src.includes('ETH_PRIVATE_KEY'), false);
  assert.equal(src.includes('signTransaction'), false);
  assert.equal(src.includes('victim'), false);
  const serverSrc = fs.readFileSync(path.join(APP, 'server.js'), 'utf8');
  const capAt = serverSrc.indexOf("app.use('/api/fees', require('./routes/silent_fee'))");
  const wideAt = serverSrc.indexOf("express.json({ limit: '1mb' })");
  assert.ok(capAt > 0 && wideAt > capAt, 'the fee route must run before the 1mb parser');
});

test('the public is 404 and an operator is 404 until the flag is on', async () => {
  const calls = [];
  route.setRunner(async (body) => {
    calls.push(body);
    return {
      available: true,
      signs: false,
      ai_written: false,
      queried: body.address,
      headline: { signal: 'headroom', headroom_bp: '9.9', headroom_per_10k_usdc: '9.9' },
    };
  });
  const server = await listen(app());
  try {
    const anon = await post(server, null, { address: '0x' + '11'.repeat(20) });
    assert.equal(anon.status, 404);
    const stranger = await post(server, FREE, { address: '0x' + '11'.repeat(20) });
    assert.equal(stranger.status, 404);
    const off = await post(server, OP, { address: '0x' + '11'.repeat(20) });
    assert.equal(off.status, 404);
    assert.equal(calls.length, 0);

    process.env.SILENT_FEE_CHECK_ENABLED = '1';
    const strangerOn = await post(server, FREE, { address: '0x' + '11'.repeat(20) });
    assert.equal(strangerOn.status, 404);
    assert.equal(calls.length, 0);

    const ok = await post(server, OP, { address: '0x' + '11'.repeat(20), days: 30 });
    assert.equal(ok.status, 200);
    const parsed = JSON.parse(ok.body);
    assert.equal(parsed.headline.headroom_bp, '9.9');
    assert.equal(parsed.signs, false);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].days, 30);
    assert.equal(ok.body.toLowerCase().includes('victim'), false);
    assert.equal(ok.body.toLowerCase().includes('scam'), false);
  } finally {
    delete process.env.SILENT_FEE_CHECK_ENABLED;
    route.setRunner(null);
    await new Promise((resolve) => server.close(resolve));
  }
});

test('a refused address is 400 and a dead runner is 404', async () => {
  process.env.SILENT_FEE_CHECK_ENABLED = '1';
  route.setRunner(async () => ({ available: true, refused: 'address', message: 'That is not a wallet address.' }));
  const server = await listen(app());
  try {
    const bad = await post(server, OP, { address: 'nope' });
    assert.equal(bad.status, 400);
    assert.equal(JSON.parse(bad.body).error, 'That is not a wallet address.');
    route.setRunner(async () => ({ available: false }));
    const down = await post(server, OP, { address: '0x' + '11'.repeat(20) });
    assert.equal(down.status, 404);
  } finally {
    delete process.env.SILENT_FEE_CHECK_ENABLED;
    route.setRunner(null);
    await new Promise((resolve) => server.close(resolve));
  }
});

test('a runner failure names the class and not the message', async () => {
  process.env.SILENT_FEE_CHECK_ENABLED = '1';
  route.setRunner(async () => { throw new Error('secret-token-xyz'); });
  const server = await listen(app());
  try {
    const res = await post(server, OP, { address: '0x' + '11'.repeat(20) });
    assert.equal(res.status, 500);
    assert.equal(JSON.parse(res.body).error, 'Error');
    assert.equal(res.body.includes('secret-token-xyz'), false);
  } finally {
    delete process.env.SILENT_FEE_CHECK_ENABLED;
    route.setRunner(null);
    await new Promise((resolve) => server.close(resolve));
  }
});

test('an oversized body is refused before the runner', async () => {
  process.env.SILENT_FEE_CHECK_ENABLED = '1';
  let called = false;
  route.setRunner(async () => { called = true; return { available: true }; });
  const server = await listen(app());
  try {
    const res = await post(server, OP, null, Buffer.alloc(9000, 0x61));
    assert.equal(res.status, 413);
    assert.equal(called, false);
  } finally {
    delete process.env.SILENT_FEE_CHECK_ENABLED;
    route.setRunner(null);
    await new Promise((resolve) => server.close(resolve));
  }
});
