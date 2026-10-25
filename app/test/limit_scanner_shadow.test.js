'use strict';
/**
 * Limit scanner forward shadow. Admin only. A missing summary is not zero.
 * A window on or before 2026-10-23 cannot show survives.
 */

process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.LIMIT_SCANNER_SHADOW;
delete process.env.LIMIT_SCANNER_SHADOW_SUMMARY;

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const express = require('express');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const flag = require('../lib/limit_scanner_shadow_flag');

const ADMIN_ID = 91051;
const USER_ID = 91052;
pool.users.push({ id: ADMIN_ID, email: 'scan-admin@example.com', plan: 'admin', token_epoch: 0 });
pool.users.push({ id: USER_ID, email: 'scan-user@example.com', plan: 'free', token_epoch: 0 });

function tokenFor(id, email) {
  return jwt.sign({ user_id: id, email, epoch: 0 }, process.env.JWT_SECRET);
}
const adminToken = tokenFor(ADMIN_ID, 'scan-admin@example.com');
const userToken = tokenFor(USER_ID, 'scan-user@example.com');

function listen(app) {
  return new Promise((resolve) => {
    const server = app.listen(0, '127.0.0.1', () => resolve(server));
  });
}
function close(server) {
  return new Promise((resolve) => server.close(resolve));
}
async function hit(server, token) {
  const headers = { accept: 'application/json' };
  if (token) headers.authorization = 'Bearer ' + token;
  const res = await fetch(`http://127.0.0.1:${server.address().port}/api/limit-scanner/shadow`, { headers });
  const raw = await res.text();
  let body = raw;
  try { body = JSON.parse(raw); } catch (e) { body = raw; }
  return { status: res.status, raw, body };
}

function appFor() {
  const app = express();
  app.use('/api/limit-scanner/shadow', require('../routes/limit_scanner_shadow'));
  return app;
}

test('the flag is off unless it is 1 or true', () => {
  assert.equal(flag.flagOn({}), false);
  assert.equal(flag.flagOn({ LIMIT_SCANNER_SHADOW: 'yes' }), false);
  assert.equal(flag.flagOn({ LIMIT_SCANNER_SHADOW: '1' }), true);
  assert.equal(flag.flagOn({ LIMIT_SCANNER_SHADOW: 'true' }), true);
});

test('anonymous and non-admin are 404, flag off and on', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lscan-'));
  const file = path.join(dir, 'missing.json');
  process.env.LIMIT_SCANNER_SHADOW_SUMMARY = file;
  const server = await listen(appFor());
  try {
    delete process.env.LIMIT_SCANNER_SHADOW;
    assert.equal((await hit(server)).status, 404);
    assert.equal((await hit(server, userToken)).status, 404);
    const admin = await hit(server, adminToken);
    assert.equal(admin.status, 200);
    assert.equal(admin.body.readable, false);
    assert.equal(admin.body.reason, 'summary_missing');
    assert.equal(admin.body.mean_net_bp, null);
    assert.equal(admin.body.survives, false);
    assert.equal(admin.body.banner, flag.BANNER);
    assert.equal(admin.raw.includes('$'), false);

    process.env.LIMIT_SCANNER_SHADOW = '1';
    assert.equal((await hit(server, userToken)).status, 404);
    const open = await hit(server, adminToken);
    assert.equal(open.status, 200);
    assert.equal(open.body.banner, undefined);
    assert.equal(open.body.mean_net_bp, null);
  } finally {
    delete process.env.LIMIT_SCANNER_SHADOW;
    delete process.env.LIMIT_SCANNER_SHADOW_SUMMARY;
    await close(server);
  }
});

test('a spent window cannot show survives, and a later one can', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lscan-'));
  const file = path.join(dir, 'summary.json');
  process.env.LIMIT_SCANNER_SHADOW_SUMMARY = file;
  process.env.LIMIT_SCANNER_SHADOW = '1';
  const spent = {
    window_start: '2026-10-01T00:00:00+00:00',
    account_equity_usd: 100,
    variants: {
      V0: { evaluable: true, verdict: 'survives', n: 120, mean_net_bp: 4.2, funding: 'priced', reason: '' },
    },
  };
  fs.writeFileSync(file, JSON.stringify(spent));
  const server = await listen(appFor());
  try {
    const early = await hit(server, adminToken);
    assert.equal(early.body.survives, false);
    assert.equal(early.body.variants.V0.survives, false);
    assert.equal(early.body.variants.V0.verdict, 'refused');
    assert.equal(early.body.variants.V0.reason, 'window is not after 2026-10-23');
    assert.equal(early.raw.includes('account_equity'), false);
    assert.equal(early.raw.includes('$'), false);

    const later = {
      window_start: '2026-10-24T00:00:00+00:00',
      variants: {
        V0: {
          evaluable: true, verdict: 'survives', n: 120, mean_net_bp: 4.2,
          lower_bp: 0.4, dsr: 0.97, funding: 'priced', reason: '',
          trades: [{ fill_ms: Date.parse('2026-10-24T01:00:00Z') }],
        },
      },
    };
    fs.writeFileSync(file, JSON.stringify(later));
    const ok = await hit(server, adminToken);
    assert.equal(ok.body.forward, true);
    assert.equal(ok.body.survives, true);
    assert.equal(ok.body.variants.V0.verdict, 'survives');
    assert.equal(ok.body.variants.V0.mean_net_bp, 4.2);
  } finally {
    delete process.env.LIMIT_SCANNER_SHADOW;
    delete process.env.LIMIT_SCANNER_SHADOW_SUMMARY;
    await close(server);
  }
});
