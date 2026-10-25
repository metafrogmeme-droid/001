'use strict';
/**
 * Claw Watch. Flag off is 404 for anyone who is not an operator. A check
 * returns signals and an unsigned revoke. It does not return a verdict,
 * and it does not store the address.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.DRAIN_WATCH_ENABLED;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const route = require('../routes/drain_watch');
const flag = require('../lib/drain_watch_flag');
const panic = require('../lib/panic_tx');
const { encodeCall } = require('../lib/abi_call');

const ADMIN_ID = 96041;
const USER_ID = 96042;
pool.users.push({ id: ADMIN_ID, email: 'claw-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: USER_ID, email: 'claw-user@example.com', plan: 'free', token_epoch: 0 });

function tokenFor(id, email) {
  return jwt.sign({ user_id: id, email, epoch: 0 }, process.env.JWT_SECRET);
}
const adminToken = tokenFor(ADMIN_ID, 'claw-admin@example.com');
const userToken = tokenFor(USER_ID, 'claw-user@example.com');

const SPENDER = '0x3333333333333333333333333333333333333333';
const TOKEN = '0x2222222222222222222222222222222222222222';
const KNOWN = '0x1111222233334444555566667777888899991111';
const DEST = '0x1111aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1111';

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
  if (body) headers['content-type'] = 'application/json';
  const res = await fetch('http://127.0.0.1:' + server.address().port + url, {
    method: method || 'GET',
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const raw = await res.text();
  let parsed = raw;
  try { parsed = JSON.parse(raw); } catch (err) { parsed = raw; }
  return { status: res.status, body: parsed, raw: raw };
}

function app() {
  const server = express();
  server.use(flag.pageGate);
  server.get('/claw-watch', (req, res) => flag.sendPage(req, res));
  server.use('/api/drain-watch', route);
  server.use(express.static(path.join(__dirname, '..', 'public')));
  return server;
}

test('flag is off unless the value is an on-word', () => {
  assert.equal(flag.flagOn({}), false);
  assert.equal(flag.flagOn({ DRAIN_WATCH_ENABLED: 'off' }), false);
  assert.equal(flag.flagOn({ DRAIN_WATCH_ENABLED: 'maybe' }), false);
  assert.equal(flag.flagOn({ DRAIN_WATCH_ENABLED: 'yes' }), true);
  assert.equal(flag.flagOn({ DRAIN_WATCH_ENABLED: ' ON ' }), true);
  assert.equal(flag.FLAG, 'DRAIN_WATCH_ENABLED');
});

test('the check route names optionalAuth', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'drain_watch.js'), 'utf8');
  assert.match(src, /optionalAuth/);
  assert.doesNotMatch(src, /ethereum:/);
  assert.doesNotMatch(src, /Drain Watch/);
});

test('flag off is 404 for anonymous and for a signed-in non-admin', async () => {
  delete process.env.DRAIN_WATCH_ENABLED;
  const server = await listen(app());
  try {
    const anon = await hit(server, '/claw-watch');
    const user = await hit(server, '/claw-watch', { bearer: userToken });
    const check = await hit(server, '/api/drain-watch/check', {
      method: 'POST', bearer: userToken, body: { data: '0x' },
    });
    assert.equal(anon.status, 404);
    assert.equal(user.status, 404);
    assert.equal(check.status, 404);
  } finally {
    await close(server);
  }
});

test('an operator previews while the flag is off, and the public page opens when it is on', async () => {
  delete process.env.DRAIN_WATCH_ENABLED;
  const server = await listen(app());
  try {
    const preview = await hit(server, '/claw-watch', { bearer: adminToken });
    assert.equal(preview.status, 200);
    assert.match(preview.raw, /Admin preview\. Not public\./);
    assert.match(preview.raw, /Claw Watch is advisory only/);
    assert.match(preview.raw, /never asks for a seed phrase/);
    assert.doesNotMatch(preview.raw, /Drain Watch/);
    assert.doesNotMatch(preview.raw, /ethereum:/);
    process.env.DRAIN_WATCH_ENABLED = 'on';
    const open = await hit(server, '/claw-watch');
    assert.equal(open.status, 200);
    assert.match(open.raw, /Claw Watch/);
    assert.doesNotMatch(open.raw, /Admin preview/);
    const script = await hit(server, '/js/claw-watch.js');
    assert.equal(script.status, 200);
    assert.match(script.raw, /eth_sendTransaction/);
    assert.doesNotMatch(script.raw, /ethereum:/);
    assert.doesNotMatch(script.raw, /private key/);
  } finally {
    delete process.env.DRAIN_WATCH_ENABLED;
    await close(server);
  }
});

test('a check returns an unsigned revoke and no verdict', async () => {
  process.env.DRAIN_WATCH_ENABLED = 'on';
  const server = await listen(app());
  try {
    const data = encodeCall('approve(address,uint256)', [SPENDER, 1n]);
    const expected = panic.buildRevoke({
      kind: 'erc20', token: TOKEN, spender: SPENDER, chain_id: 1,
    });
    const got = await hit(server, '/api/drain-watch/check', {
      method: 'POST',
      body: { chain_id: 1, to: TOKEN, data: data, value: '0' },
    });
    assert.equal(got.status, 200);
    assert.equal(Object.prototype.hasOwnProperty.call(got.body, 'verdict'), false);
    assert.equal(got.body.ai_summary, null);
    assert.equal(got.body.ai_summary_label, null);
    assert.equal(got.body.stored, false);
    assert.equal(got.body.signals.exploit_list, 'unread');
    assert.equal(got.body.signals.claw_guard, 'unread');
    assert.equal(got.body.signals.claw_guard_reason, 'claw_guard_signals_not_shipped');
    assert.equal(got.body.signals.unlimited, 'no');
    assert.equal(got.body.cancel.available, false);
    assert.equal(got.body.cancel.reason, 'module_not_installed');
    assert.equal(got.body.cancel.tx, null);
    assert.equal(got.body.tx.unsigned, true);
    assert.equal(got.body.tx.data, expected.data);
    assert.equal(got.body.tx.to, TOKEN);
    assert.equal(Object.prototype.hasOwnProperty.call(got.body.tx, 'r'), false);
    assert.equal(got.body.sealed.reason, 'anonymous');
    const dumped = JSON.stringify(got.body);
    assert.equal(dumped.includes('ethereum:'), false);
    assert.equal(dumped.includes('not the same as safe'), false);
    assert.equal(dumped.includes('Drain Watch'), false);
  } finally {
    delete process.env.DRAIN_WATCH_ENABLED;
    await close(server);
  }
});

test('a lookalike destination alerts and dust is hidden', async () => {
  process.env.DRAIN_WATCH_ENABLED = 'on';
  const server = await listen(app());
  try {
    const data = encodeCall('transfer(address,uint256)', [DEST, 1n]);
    const look = await hit(server, '/api/drain-watch/check', {
      method: 'POST',
      body: {
        chain_id: 1,
        to: TOKEN,
        data: data,
        destination: DEST,
        counterparties: [KNOWN],
      },
    });
    assert.equal(look.status, 200);
    assert.equal(look.body.signals.lookalike, 'alert');
    assert.equal(look.body.alert, true);
    const dust = await hit(server, '/api/drain-watch/check', {
      method: 'POST',
      body: {
        chain_id: 1,
        to: TOKEN,
        data: '0x',
        destination: DEST,
        counterparties: [],
        inbound: [{ from: DEST, amount: '1', at: Date.now() }],
      },
    });
    assert.equal(dust.body.signals.dust, 'hide');
    assert.equal(dust.body.alert, false);
    assert.equal(dust.body.signals.lookalike, 'not_checked');
  } finally {
    delete process.env.DRAIN_WATCH_ENABLED;
    await close(server);
  }
});

test('an unknown call is not described as safe', async () => {
  process.env.DRAIN_WATCH_ENABLED = 'on';
  const server = await listen(app());
  try {
    const got = await hit(server, '/api/drain-watch/check', {
      method: 'POST',
      body: { chain_id: 1, to: TOKEN, data: '0xdeadbeef' },
    });
    assert.equal(got.status, 200);
    assert.equal(got.body.actions[0].tid, 'txr.a_unknown');
    assert.equal(JSON.stringify(got.body).includes('safe'), false);
    assert.equal(got.body.tx, null);
  } finally {
    delete process.env.DRAIN_WATCH_ENABLED;
    await close(server);
  }
});
